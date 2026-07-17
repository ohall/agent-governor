from __future__ import annotations

from fastapi.testclient import TestClient
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.trace import StatusCode

from agent_governor.mockops.app import create_app
from agent_governor.mockops.config import MockOpsSettings
from agent_governor.warden.gateway import AgentManifest, Gateway, ToolCall
from agent_governor.warden.otel import OtelBridge

MANIFEST = AgentManifest(
    agent_id="otel-agent",
    owner="maria.chen",
    purpose="otel tests",
    allowed_tools=("get_incident", "delete_records"),
)


def make_gateway() -> tuple[Gateway, InMemorySpanExporter]:
    exporter = InMemorySpanExporter()
    gateway = Gateway(TestClient(create_app(MockOpsSettings(seed=42))))
    gateway.otel = OtelBridge(exporter)
    return gateway, exporter


class TestOtelExport:
    def test_action_becomes_a_span_tree(self) -> None:
        gateway, exporter = make_gateway()
        session = gateway.register(MANIFEST)
        gateway.execute(
            session,
            ToolCall(
                tool="get_incident",
                params={"incident_id": "INC-1001"},
                reasoning="triage read",
            ),
        )
        spans = exporter.get_finished_spans()
        names = {s.name for s in spans}
        assert "gateway.execute get_incident" in names
        assert {"identity.validate", "policy.classify", "authz.decide", "tool.execute"} <= names

    def test_root_span_carries_the_authorization_story(self) -> None:
        gateway, exporter = make_gateway()
        session = gateway.register(MANIFEST)
        gateway.execute(session, ToolCall(tool="get_incident", params={"incident_id": "INC-1001"}))
        root = next(
            s for s in exporter.get_finished_spans() if s.name.startswith("gateway.execute")
        )
        attrs = dict(root.attributes or {})
        assert attrs["gen_ai.agent.id"] == "otel-agent"
        assert attrs["gen_ai.tool.name"] == "get_incident"
        assert attrs["governor.owner"] == "maria.chen"
        assert attrs["governor.autonomy_level"] == 4
        assert attrs["governor.authorization_path"] == "auto"
        assert attrs["governor.policy_version"] == "2026.07.14-1"
        assert attrs["governor.ok"] is True

    def test_children_parent_to_the_root(self) -> None:
        gateway, exporter = make_gateway()
        session = gateway.register(MANIFEST)
        gateway.execute(session, ToolCall(tool="get_incident", params={"incident_id": "INC-1001"}))
        spans = exporter.get_finished_spans()
        root = next(s for s in spans if s.name.startswith("gateway.execute"))
        child = next(s for s in spans if s.name == "tool.execute")
        assert child.parent is not None
        assert child.parent.span_id == root.context.span_id
        assert child.start_time is not None
        assert root.start_time is not None
        assert child.start_time >= root.start_time

    def test_denied_action_exports_error_status(self) -> None:
        gateway, exporter = make_gateway()
        session = gateway.register(MANIFEST)
        gateway.execute(session, ToolCall(tool="drop_database", params={}))
        root = next(
            s for s in exporter.get_finished_spans() if s.name.startswith("gateway.execute")
        )
        assert root.status.status_code is StatusCode.ERROR
        attrs = dict(root.attributes or {})
        assert attrs["governor.denied"] is True

    def test_queued_action_records_approval_id(self) -> None:
        gateway, exporter = make_gateway()
        session = gateway.register(MANIFEST)
        result = gateway.execute(
            session,
            ToolCall(tool="delete_records", params={"record_set": "customer_pii_archive"}),
        )
        root = next(
            s for s in exporter.get_finished_spans() if s.name.startswith("gateway.execute")
        )
        attrs = dict(root.attributes or {})
        assert attrs["governor.authorization_path"] == "queued"
        assert attrs["governor.always_approve_category"] == "data_deletion"
        assert attrs["governor.approval_id"] == result.approval_id
