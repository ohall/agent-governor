from __future__ import annotations

import httpx
import pytest
from fastapi.testclient import TestClient

from agent_governor.agent.tools import ToolExecutor
from agent_governor.mockops.app import create_app
from agent_governor.mockops.config import MockOpsSettings
from agent_governor.warden.gateway import (
    TOOL_REGISTRY,
    AgentManifest,
    Gateway,
    ToolCall,
)


@pytest.fixture
def gateway() -> Gateway:
    client = TestClient(create_app(MockOpsSettings(seed=42)))
    return Gateway(client)


MANIFEST = AgentManifest(
    agent_id="test-agent",
    owner="maria.chen",
    purpose="testing",
    allowed_tools=("get_incident", "deploy_service", "list_incidents"),
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

    def test_tool_outside_manifest_is_denied(self, gateway: Gateway) -> None:
        session = gateway.register(MANIFEST)
        result = gateway.execute(
            session, ToolCall(tool="delete_records", params={"record_set": "tmp_sessions"})
        )
        assert result.denied
        assert "not permitted by manifest" in (result.error or "")

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
