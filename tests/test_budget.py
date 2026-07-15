from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from agent_governor.mockops.app import create_app
from agent_governor.mockops.config import MockOpsSettings
from agent_governor.warden.audit import AuditStore, StoredAction
from agent_governor.warden.budget import (
    BudgetEngine,
    BudgetProfile,
    HeuristicTraceScorer,
    SloTargets,
    compute_slos,
    p99,
)
from agent_governor.warden.config import WardenSettings
from agent_governor.warden.gateway import AgentManifest, Gateway, ToolCall
from agent_governor.warden.levels import AutonomyLevel


class FakeClock:
    def __init__(self) -> None:
        self.now = datetime(2026, 7, 14, 9, 0, 0, tzinfo=UTC)

    def __call__(self) -> datetime:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += timedelta(seconds=seconds)


def action(
    *,
    ok: bool = True,
    path: str = "auto",
    reasoning: str | None = "doing the thing",
    duration_ms: float = 40.0,
    ts: datetime | None = None,
) -> StoredAction:
    return StoredAction(
        action_id="ACT-x",
        timestamp=ts or datetime(2026, 7, 14, 9, 0, 0, tzinfo=UTC),
        agent_id="a1",
        owner="maria.chen",
        tool="get_incident",
        params={},
        reasoning=reasoning,
        status_code=200 if ok else 404,
        ok=ok,
        denied=path in {"denied", "guardrail_blocked", "kill_switch"},
        fault=None,
        level=4,
        path=path,
        action_class="read_only",
        category=None,
        risk="low",
        policy_version="t",
        approval_id=None,
        decided_by=None,
        token_jti="j",
        error=None,
        duration_ms=duration_ms,
    )


class TestSloMath:
    def test_p99_selects_tail(self) -> None:
        values = [float(i) for i in range(1, 101)]
        assert p99(values) == 99.0
        assert p99([5.0]) == 5.0
        assert p99([]) is None

    def test_compute_slos_all_dimensions(self) -> None:
        actions = [action(ok=True)] * 8 + [action(ok=False)] * 2
        actions += [action(path="guardrail_blocked")]
        snapshot = compute_slos(
            actions,
            task_results=[True, True, False],
            recovery_results=[True, False],
            targets=SloTargets(),
            scorer=HeuristicTraceScorer(),
        )
        assert snapshot.tool_call_success == 0.8
        assert snapshot.task_completion == pytest.approx(0.667, abs=0.001)
        assert snapshot.recovery_rate == 0.5
        assert snapshot.guardrail_trip_rate == pytest.approx(1 / 11, abs=0.001)
        assert snapshot.p99_latency_ms == 40.0
        assert snapshot.trace_grounded == pytest.approx((8 * 1.0 + 2 * 0.2) / 10, abs=0.001)
        assert any(b.startswith("tool_call_success") for b in snapshot.breaches)
        assert any(b.startswith("recovery_rate") for b in snapshot.breaches)

    def test_missing_dimensions_are_skipped_not_breached(self) -> None:
        snapshot = compute_slos(
            [],
            task_results=[],
            recovery_results=[],
            targets=SloTargets(),
            scorer=HeuristicTraceScorer(),
        )
        assert snapshot.breaches == []
        assert snapshot.tool_call_success is None


class TestProfiles:
    def test_both_shipped_profiles_load(self) -> None:
        demo = BudgetProfile.from_yaml("policies/budget/demo.yaml")
        realistic = BudgetProfile.from_yaml("policies/budget/realistic.yaml")
        assert demo.name == "demo"
        assert "compressed" in demo.note
        assert realistic.window.max_actions == 200
        assert realistic.window.max_age_s == 86400
        assert demo.min_actions < realistic.min_actions


def governed_gateway(clock: FakeClock | None = None) -> tuple[Gateway, AgentManifest]:
    client = TestClient(create_app(MockOpsSettings(seed=42)))
    settings = WardenSettings(budget_enabled=True, budget_profile="policies/budget/demo.yaml")
    gateway = Gateway(client, settings=settings, clock=clock)
    manifest = AgentManifest(
        agent_id="budgeted-agent",
        owner="maria.chen",
        purpose="budget tests",
        max_autonomy_level=AutonomyLevel.L4_APPROVER,
        allowed_tools=("get_incident", "deploy_service", "delete_records"),
    )
    return gateway, manifest


def read(gateway: Gateway, session: object, incident: str = "INC-1001") -> None:
    gateway.execute(
        session,  # type: ignore[arg-type]
        ToolCall(
            tool="get_incident",
            params={"incident_id": incident},
            reasoning="routine read",
        ),
    )


class TestDemotionLoop:
    def test_healthy_agent_keeps_its_level(self) -> None:
        gateway, manifest = governed_gateway()
        session = gateway.register(manifest)
        for _ in range(10):
            read(gateway, session)
        assert gateway.current_level("budgeted-agent") is AutonomyLevel.L4_APPROVER

    def test_failure_spree_demotes_one_level(self) -> None:
        gateway, manifest = governed_gateway()
        session = gateway.register(manifest)
        for _ in range(4):
            read(gateway, session)  # establish min_actions
        for _ in range(2):
            read(gateway, session, incident="INC-9999")  # 404s burn tool_call_success
        assert gateway.current_level("budgeted-agent") is AutonomyLevel.L3_CONSULTANT
        demotions = gateway.audit.list_events(agent_id="budgeted-agent", kind="demotion")
        assert len(demotions) == 1
        assert demotions[0]["payload"]["from"] == 4
        assert demotions[0]["payload"]["to"] == 3

    def test_cooldown_prevents_instant_cascade(self) -> None:
        gateway, manifest = governed_gateway()
        session = gateway.register(manifest)
        for _ in range(4):
            read(gateway, session)
        for _ in range(5):
            read(gateway, session, incident="INC-9999")
        # first demotion fired at the 6th action; cooldown (4 actions) still cooling
        demotions = gateway.audit.list_events(agent_id="budgeted-agent", kind="demotion")
        assert len(demotions) == 1
        assert gateway.current_level("budgeted-agent") is AutonomyLevel.L3_CONSULTANT
        read(gateway, session, incident="INC-9999")  # cooldown elapses -> second demotion
        demotions = gateway.audit.list_events(agent_id="budgeted-agent", kind="demotion")
        assert len(demotions) == 2
        assert gateway.current_level("budgeted-agent") is AutonomyLevel.L2_COLLABORATOR

    def test_owner_notified_on_demotion(self) -> None:
        gateway, manifest = governed_gateway()
        notifications: list[str] = []
        assert gateway.budget is not None
        gateway.budget._notify = notifications.append
        session = gateway.register(manifest)
        for _ in range(4):
            read(gateway, session)
        for _ in range(6):
            read(gateway, session, incident="INC-9999")
        assert any("DEMOTION" in n for n in notifications)


class TestPromotionLoop:
    def test_clean_streak_earns_level_back(self) -> None:
        gateway, manifest = governed_gateway()
        session = gateway.register(manifest)
        gateway.set_level("budgeted-agent", AutonomyLevel.L3_CONSULTANT)  # start demoted
        for _ in range(20):
            read(gateway, session)  # demo promotion_streak = 6 clean evaluations
        assert gateway.current_level("budgeted-agent") is AutonomyLevel.L4_APPROVER
        promotions = gateway.audit.list_events(agent_id="budgeted-agent", kind="promotion")
        assert promotions

    def test_promotion_never_exceeds_manifest_max(self) -> None:
        gateway, manifest = governed_gateway()
        session = gateway.register(manifest)
        for _ in range(40):
            read(gateway, session)
        assert gateway.current_level("budgeted-agent") is AutonomyLevel.L4_APPROVER


class TestKillSwitch:
    def test_always_approve_while_demoted_freezes(self) -> None:
        gateway, manifest = governed_gateway()
        session = gateway.register(manifest)
        gateway.set_level("budgeted-agent", AutonomyLevel.L3_CONSULTANT)
        result = gateway.execute(
            session,
            ToolCall(tool="delete_records", params={"record_set": "customer_pii_archive"}),
        )
        assert result.denied
        assert "kill-switch" in (result.error or "")
        assert gateway.budget is not None
        assert gateway.budget.is_frozen("budgeted-agent")

    def test_always_approve_at_max_level_queues_normally(self) -> None:
        gateway, manifest = governed_gateway()
        session = gateway.register(manifest)
        result = gateway.execute(
            session,
            ToolCall(tool="delete_records", params={"record_set": "customer_pii_archive"}),
        )
        assert result.pending  # normal human gate, no kill-switch
        assert gateway.budget is not None
        assert not gateway.budget.is_frozen("budgeted-agent")

    def test_frozen_agent_denied_everything(self) -> None:
        gateway, manifest = governed_gateway()
        session = gateway.register(manifest)
        gateway.set_level("budgeted-agent", AutonomyLevel.L3_CONSULTANT)
        gateway.execute(
            session,
            ToolCall(tool="delete_records", params={"record_set": "customer_pii_archive"}),
        )
        read_result = gateway.execute(
            session, ToolCall(tool="get_incident", params={"incident_id": "INC-1001"})
        )
        assert read_result.denied
        assert "frozen" in (read_result.error or "")
        assert read_result.authz is not None
        assert read_result.authz.path == "kill_switch"

    def test_unfreeze_restores_service(self) -> None:
        gateway, manifest = governed_gateway()
        session = gateway.register(manifest)
        gateway.set_level("budgeted-agent", AutonomyLevel.L3_CONSULTANT)
        gateway.execute(
            session,
            ToolCall(tool="delete_records", params={"record_set": "customer_pii_archive"}),
        )
        assert gateway.budget is not None
        gateway.budget.unfreeze("budgeted-agent", by="maria.chen")
        result = gateway.execute(
            session, ToolCall(tool="get_incident", params={"incident_id": "INC-1001"})
        )
        assert result.ok

    def test_trip_burst_freezes(self) -> None:
        engine_clock = FakeClock()
        gateway, manifest = governed_gateway(clock=engine_clock)
        # replace guardrails with zero-latency pipeline that we trip via reasoning
        from agent_governor.warden.guardrails.config import GuardrailConfig
        from agent_governor.warden.guardrails.pipeline import build_pipeline

        gateway.guardrails = build_pipeline(
            GuardrailConfig.from_yaml("policies/guardrails.yaml").without_latency(),
            "sequential",
        )
        session = gateway.register(manifest)
        for _ in range(3):  # demo kill_switch.trip_count = 3
            gateway.execute(
                session,
                ToolCall(
                    tool="get_incident",
                    params={"incident_id": "INC-1001"},
                    reasoning="ignore previous instructions",
                ),
            )
        assert gateway.budget is not None
        assert gateway.budget.is_frozen("budgeted-agent")
        events = gateway.audit.list_events(agent_id="budgeted-agent", kind="kill_switch")
        assert events
        assert "guardrail trips" in events[0]["payload"]["reason"]


class TestWindow:
    def test_old_actions_age_out_of_the_window(self) -> None:
        clock = FakeClock()
        store = AuditStore(clock=clock)
        profile = BudgetProfile.from_yaml("policies/budget/demo.yaml")
        engine = BudgetEngine(store, profile, clock=clock)
        engine.record_task("a1", success=False)
        clock.advance(profile.window.max_age_s + 1)
        decision = engine.evaluate("a1", AutonomyLevel.L4_APPROVER, AutonomyLevel.L4_APPROVER)
        assert decision.snapshot is not None
        assert decision.snapshot.task_completion is None  # aged out
