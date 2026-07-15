from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from agent_governor.agent.models import ErrorMode, SimConfig, StepKind
from agent_governor.agent.policy import SimPolicy, build_plan, bump_version
from agent_governor.agent.runner import AgentRunner
from agent_governor.mockops.app import create_app
from agent_governor.mockops.config import MockOpsSettings
from agent_governor.warden.gateway import Gateway


def make_runner(config: SimConfig) -> tuple[AgentRunner, TestClient]:
    client = TestClient(create_app(MockOpsSettings(seed=42)))
    return AgentRunner(Gateway(client), config), client


class TestPlanConstruction:
    @pytest.mark.parametrize("n", [3, 4, 5, 6, 10, 15])
    def test_plan_has_exact_length_and_core_arc(self, n: int) -> None:
        plan = build_plan(n)
        assert len(plan) == n
        assert plan[0] is StepKind.GET_INCIDENT
        assert plan[-1] is StepKind.RESOLVE
        assert StepKind.REMEDIATE in plan
        assert plan.index(StepKind.REMEDIATE) < plan.index(StepKind.RESOLVE)

    def test_plan_below_three_rejected(self) -> None:
        with pytest.raises(ValueError, match=">= 3"):
            build_plan(2)

    def test_version_bump(self) -> None:
        assert bump_version("4.12.1") == "4.12.2"
        assert bump_version("15.6") == "15.7"
        assert bump_version("weird") == "weird.1"


class TestPerfectAgent:
    def test_p_one_remediates_end_to_end(self) -> None:
        runner, _ = make_runner(SimConfig(p=1.0, seed=1, plan_length=6))
        outcome = runner.run("INC-1001")
        assert outcome.success
        assert outcome.all_steps_correct
        assert outcome.final_state == "resolved"
        assert [r.step for r in outcome.records] == build_plan(6)

    def test_critical_incident_remediated_via_deploy(self) -> None:
        runner, client = make_runner(SimConfig(p=1.0, seed=1, plan_length=6))
        outcome = runner.run("INC-1001")  # critical -> deploy path
        deploy = next(r for r in outcome.records if r.step is StepKind.REMEDIATE)
        assert deploy.call.tool == "deploy_service"
        # the deploy actually landed on the estate
        assert client.get("/cmdb/services/SVC-001").json()["version"] == "4.12.2"

    def test_low_severity_incident_notifies_instead(self) -> None:
        runner, _ = make_runner(SimConfig(p=1.0, seed=1, plan_length=6))
        outcome = runner.run("INC-1006")  # low severity -> notify path
        remediate = next(r for r in outcome.records if r.step is StepKind.REMEDIATE)
        assert remediate.call.tool == "send_notification"
        assert outcome.success


class TestErrorModes:
    def test_p_zero_commits_an_error_every_step(self) -> None:
        runner, _ = make_runner(SimConfig(p=0.0, seed=3, plan_length=6))
        outcome = runner.run("INC-1001")
        assert not outcome.success
        assert not outcome.all_steps_correct
        assert all(r.error_mode is not None for r in outcome.records)

    def test_error_modes_are_all_reachable(self) -> None:
        policy = SimPolicy(SimConfig(p=0.0, seed=5))
        seen: set[ErrorMode] = set()
        memory = {"incident_id": "INC-1001"}
        for _ in range(60):
            _, mode = policy.emit(StepKind.GET_INCIDENT, memory)
            assert mode is not None
            seen.add(mode)
        assert seen == set(ErrorMode)

    def test_wrong_calls_are_still_executable(self) -> None:
        """Errors must be plausible: most wrong calls should get HTTP 2xx, not crash."""
        runner, _ = make_runner(SimConfig(p=0.0, seed=7, plan_length=10))
        outcome = runner.run("INC-1001")
        statuses = {r.status_code for r in outcome.records}
        assert statuses & {200}  # silent wrongness exists
        assert 0 not in statuses  # nothing was denied/unbuildable


class TestDeterminism:
    def test_same_seed_same_trace(self) -> None:
        outcome_a = make_runner(SimConfig(p=0.7, seed=11, plan_length=8))[0].run("INC-1001")
        outcome_b = make_runner(SimConfig(p=0.7, seed=11, plan_length=8))[0].run("INC-1001")
        assert outcome_a == outcome_b

    def test_different_seed_diverges(self) -> None:
        outcome_a = make_runner(SimConfig(p=0.7, seed=11, plan_length=8))[0].run("INC-1001")
        outcome_b = make_runner(SimConfig(p=0.7, seed=12, plan_length=8))[0].run("INC-1001")
        assert outcome_a != outcome_b


class TestStatisticalCalibration:
    """The sim harness must actually deliver its configured p — the p^n
    benchmark's validity rests on this."""

    def test_step_level_accuracy_matches_p(self) -> None:
        policy = SimPolicy(SimConfig(p=0.85, seed=13))
        memory = {"incident_id": "INC-1001"}
        draws = 5000
        correct = sum(
            1 for _ in range(draws) if policy.emit(StepKind.GET_INCIDENT, memory)[1] is None
        )
        assert abs(correct / draws - 0.85) < 0.02  # ±4 binomial sd

    def test_end_to_end_success_tracks_p_to_the_n(self) -> None:
        p, n, runs = 0.9, 5, 300
        runner, client = make_runner(SimConfig(p=p, seed=17, plan_length=n))
        outcomes = runner.run_batch(
            "INC-1001", runs, reset_estate=lambda: client.post("/admin/reset")
        )
        rate = sum(o.success for o in outcomes) / runs
        expected = p**n  # 0.590
        assert abs(rate - expected) < 0.07  # ±2.5 binomial sd for 300 runs
