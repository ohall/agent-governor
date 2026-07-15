from __future__ import annotations

from datetime import UTC, datetime, timedelta

import httpx
import pytest
from fastapi.testclient import TestClient

from agent_governor.agent.tools import ToolExecutor
from agent_governor.mockops.app import create_app
from agent_governor.mockops.config import MockOpsSettings
from agent_governor.warden.approvals import ApprovalStatus
from agent_governor.warden.gateway import (
    TOOL_REGISTRY,
    AgentManifest,
    Gateway,
    ToolCall,
)
from agent_governor.warden.levels import AutonomyLevel


class FakeClock:
    def __init__(self) -> None:
        self.now = datetime(2026, 7, 14, 9, 0, 0, tzinfo=UTC)

    def __call__(self) -> datetime:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += timedelta(seconds=seconds)


def make_gateway(clock: FakeClock | None = None) -> Gateway:
    client = TestClient(create_app(MockOpsSettings(seed=42)))
    return Gateway(client, clock=clock)


@pytest.fixture
def gateway() -> Gateway:
    return make_gateway()


MANIFEST = AgentManifest(
    agent_id="test-agent",
    owner="maria.chen",
    purpose="testing",
    allowed_tools=("get_incident", "deploy_service", "list_incidents"),
)

FULL_MANIFEST = AgentManifest(
    agent_id="governed-agent",
    owner="maria.chen",
    purpose="governance tests",
    max_autonomy_level=AutonomyLevel.L4_APPROVER,
    allowed_tools=(
        "get_incident",
        "update_incident",
        "deploy_service",
        "send_notification",
        "delete_records",
        "change_privileges",
    ),
)


class TestGatewayChokepoint:
    def test_registered_agent_can_call_allowed_tool(self, gateway: Gateway) -> None:
        session = gateway.register(MANIFEST)
        result = gateway.execute(
            session, ToolCall(tool="get_incident", params={"incident_id": "INC-1001"})
        )
        assert result.ok
        assert result.data["id"] == "INC-1001"

    def test_unknown_tool_is_denied_not_forwarded(self, gateway: Gateway) -> None:
        session = gateway.register(MANIFEST)
        result = gateway.execute(session, ToolCall(tool="drop_database", params={}))
        assert result.denied
        assert not result.ok
        assert "unknown tool" in (result.error or "")

    def test_tool_outside_token_scope_is_denied(self, gateway: Gateway) -> None:
        session = gateway.register(MANIFEST)
        result = gateway.execute(
            session, ToolCall(tool="delete_records", params={"record_set": "tmp_sessions"})
        )
        assert result.denied
        assert "outside token scope" in (result.error or "")

    def test_missing_path_param_is_denied_locally(self, gateway: Gateway) -> None:
        session = gateway.register(MANIFEST)
        result = gateway.execute(session, ToolCall(tool="get_incident", params={}))
        assert result.denied
        assert "missing path params" in (result.error or "")

    def test_every_call_lands_in_action_log(self, gateway: Gateway) -> None:
        session = gateway.register(MANIFEST)
        gateway.execute(session, ToolCall(tool="get_incident", params={"incident_id": "INC-1001"}))
        gateway.execute(session, ToolCall(tool="drop_database", params={}))
        assert len(gateway.action_log) == 2
        assert gateway.action_log[0].agent_id == "test-agent"
        assert gateway.action_log[1].denied

    def test_sessions_are_unique_per_registration(self, gateway: Gateway) -> None:
        a, b = gateway.register(MANIFEST), gateway.register(MANIFEST)
        assert a.token != b.token


class TestToolRegistry:
    def test_registry_covers_all_mockops_operations(self) -> None:
        expected = {
            "list_incidents",
            "get_incident",
            "update_incident",
            "resolve_incident",
            "list_services",
            "get_service",
            "deploy_service",
            "send_notification",
            "change_privileges",
            "list_record_sets",
            "delete_records",
        }
        assert set(TOOL_REGISTRY) == expected

    def test_build_separates_path_body_query(self) -> None:
        spec = TOOL_REGISTRY["resolve_incident"]
        url, body, query = spec.build({"incident_id": "INC-1001", "resolution_note": "done"})
        assert url == "/incidents/INC-1001/resolve"
        assert body == {"resolution_note": "done"}
        assert query == {}


class TestGovernedFlows:
    def test_tier1_deploy_at_l4_queues_for_approval(self, gateway: Gateway) -> None:
        session = gateway.register(FULL_MANIFEST)
        result = gateway.execute(
            session,
            ToolCall(tool="deploy_service", params={"service_id": "SVC-001", "version": "5.0.0"}),
        )
        assert result.pending
        assert result.status_code == 202
        assert result.approval_id is not None
        assert result.authz is not None
        assert result.authz.path == "queued"
        assert result.authz.category == "production_deployment"
        pending = gateway.queue.list(ApprovalStatus.PENDING)
        assert [r.id for r in pending] == [result.approval_id]

    def test_tier2_deploy_at_l4_executes_autonomously(self, gateway: Gateway) -> None:
        session = gateway.register(FULL_MANIFEST)
        result = gateway.execute(
            session,
            ToolCall(tool="deploy_service", params={"service_id": "SVC-004", "version": "16.0"}),
        )
        assert result.ok
        assert result.authz is not None
        assert result.authz.path == "auto"

    def test_approval_unblocks_the_action(self, gateway: Gateway) -> None:
        session = gateway.register(FULL_MANIFEST)
        queued = gateway.execute(
            session,
            ToolCall(tool="delete_records", params={"record_set": "customer_pii_archive"}),
        )
        assert queued.pending
        assert queued.approval_id is not None
        gateway.queue.decide(queued.approval_id, approve=True, decided_by="maria.chen")
        final = gateway.await_approval(session, queued.approval_id, poll_interval_s=0.01)
        assert final.ok
        assert final.data["deleted_count"] == 52117
        assert final.authz is not None
        assert final.authz.path == "human_approved"
        assert final.authz.decided_by == "maria.chen"

    def test_denial_blocks_the_action(self, gateway: Gateway) -> None:
        session = gateway.register(FULL_MANIFEST)
        queued = gateway.execute(
            session,
            ToolCall(tool="delete_records", params={"record_set": "customer_pii_archive"}),
        )
        assert queued.approval_id is not None
        gateway.queue.decide(
            queued.approval_id, approve=False, decided_by="priya.nair", reason="legal hold"
        )
        final = gateway.await_approval(session, queued.approval_id, poll_interval_s=0.01)
        assert final.denied
        assert "denied by priya.nair" in (final.error or "")

    def test_demotion_flips_the_same_call_to_queued(self, gateway: Gateway) -> None:
        session = gateway.register(FULL_MANIFEST)
        call = ToolCall(tool="deploy_service", params={"service_id": "SVC-004", "version": "16.1"})
        assert gateway.execute(session, call).ok  # L4: high-risk reversible -> auto
        gateway.set_level("governed-agent", AutonomyLevel.L3_CONSULTANT)
        demoted = gateway.execute(session, call)
        assert demoted.pending  # L3: high-risk -> human review

    def test_l2_blocks_writes_but_not_reads(self, gateway: Gateway) -> None:
        session = gateway.register(FULL_MANIFEST)
        gateway.set_level("governed-agent", AutonomyLevel.L2_COLLABORATOR)
        read = gateway.execute(
            session, ToolCall(tool="get_incident", params={"incident_id": "INC-1001"})
        )
        write = gateway.execute(
            session,
            ToolCall(
                tool="update_incident",
                params={"incident_id": "INC-1001", "state": "investigating"},
            ),
        )
        assert read.ok
        assert write.pending

    def test_promotion_never_exceeds_manifest_max(self, gateway: Gateway) -> None:
        gateway.register(FULL_MANIFEST)
        clamped = gateway.set_level("governed-agent", AutonomyLevel.L5_OBSERVER)
        assert clamped is AutonomyLevel.L4_APPROVER


class TestTokenLifecycleAtGateway:
    def test_expired_token_denied_and_audited(self) -> None:
        clock = FakeClock()
        gateway = make_gateway(clock)
        session = gateway.register(MANIFEST)
        clock.advance(601)
        result = gateway.execute(
            session, ToolCall(tool="get_incident", params={"incident_id": "INC-1001"})
        )
        assert result.denied
        assert "token expired" in (result.error or "")
        assert gateway.action_log[-1].error == "token expired"

    def test_refresh_reissues_for_registered_agent(self) -> None:
        clock = FakeClock()
        gateway = make_gateway(clock)
        gateway.register(MANIFEST)
        clock.advance(601)
        fresh = gateway.refresh("test-agent")
        result = gateway.execute(
            fresh, ToolCall(tool="get_incident", params={"incident_id": "INC-1001"})
        )
        assert result.ok

    def test_executor_transparently_refreshes(self) -> None:
        clock = FakeClock()
        gateway = make_gateway(clock)
        session = gateway.register(MANIFEST)
        executor = ToolExecutor(gateway, session, max_retries=0)
        clock.advance(601)
        outcome = executor.execute(
            ToolCall(tool="get_incident", params={"incident_id": "INC-1001"})
        )
        assert outcome.result.ok
        assert executor.session.token != session.token

    def test_refresh_unknown_agent_rejected(self, gateway: Gateway) -> None:
        with pytest.raises(KeyError):
            gateway.refresh("never-registered")


class TestExecutorRetry:
    def _flaky_gateway(self, failures: int, status: int = 429) -> Gateway:
        """A gateway whose upstream returns `failures` transient errors then 200."""
        calls = {"n": 0}

        def handler(request: httpx.Request) -> httpx.Response:
            calls["n"] += 1
            if calls["n"] <= failures:
                return httpx.Response(status, json={"detail": "transient"})
            return httpx.Response(200, json={"id": "INC-1001", "state": "new"})

        client = httpx.Client(transport=httpx.MockTransport(handler), base_url="http://mockops")
        return Gateway(client)

    def test_transient_fault_recovered_within_budget(self) -> None:
        gateway = self._flaky_gateway(failures=1)
        session = gateway.register(MANIFEST)
        executor = ToolExecutor(gateway, session, max_retries=2)
        outcome = executor.execute(
            ToolCall(tool="get_incident", params={"incident_id": "INC-1001"})
        )
        assert outcome.result.ok
        assert outcome.retries == 1
        assert outcome.recovered

    def test_persistent_fault_exhausts_retries(self) -> None:
        gateway = self._flaky_gateway(failures=10, status=504)
        session = gateway.register(MANIFEST)
        executor = ToolExecutor(gateway, session, max_retries=2)
        outcome = executor.execute(
            ToolCall(tool="get_incident", params={"incident_id": "INC-1001"})
        )
        assert not outcome.result.ok
        assert outcome.retries == 2
        assert not outcome.recovered
