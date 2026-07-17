"""Real OpenTelemetry export for gateway traces.

Every governed action becomes a genuine OTel span tree — GenAI semantic
convention names where they map (gen_ai.agent.id, gen_ai.tool.name), plus a
governor.* namespace for the authorization story (level, path, class,
category, policy version, approver). Exports to the console by default, or
to any OTLP/HTTP endpoint:

    WARDEN_OTEL_ENABLED=1                         # console exporter
    WARDEN_OTEL_ENDPOINT=http://localhost:4318    # OTLP (install extra: agent-governor[otlp])

The SQLite audit store remains the source of truth for `warden audit`; OTel
export is the interop path into whatever tracing backend legal, security,
and SRE teams already trust.
"""

from __future__ import annotations

from datetime import timedelta
from typing import TYPE_CHECKING, Any

from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import (
    ConsoleSpanExporter,
    SimpleSpanProcessor,
    SpanExporter,
)
from opentelemetry.trace import Status, StatusCode, set_span_in_context

if TYPE_CHECKING:
    from agent_governor.warden.audit import Span
    from agent_governor.warden.gateway import ActionRecord

_ATTR_KEYS = (
    ("level", "governor.autonomy_level"),
    ("path", "governor.authorization_path"),
    ("action_class", "governor.action_class"),
    ("category", "governor.always_approve_category"),
    ("policy_version", "governor.policy_version"),
    ("approval_id", "governor.approval_id"),
    ("decided_by", "governor.decided_by"),
    ("token_jti", "governor.token_jti"),
    ("fault", "governor.injected_fault"),
    ("error", "governor.error"),
)


def build_exporter(endpoint: str | None) -> SpanExporter:
    """Console exporter by default; OTLP/HTTP when an endpoint is configured."""
    if endpoint is None:
        return ConsoleSpanExporter()
    try:
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import (
            OTLPSpanExporter,
        )
    except ImportError as exc:  # pragma: no cover - depends on extras
        raise ImportError(
            "OTLP export requires the 'otlp' extra: uv add 'agent-governor[otlp]'"
        ) from exc
    exporter: SpanExporter = OTLPSpanExporter(endpoint=f"{endpoint.rstrip('/')}/v1/traces")
    return exporter


class OtelBridge:
    """Converts recorded gateway traces into real OTel spans."""

    def __init__(
        self,
        exporter: SpanExporter | None = None,
        service_name: str = "agent-governor-warden",
    ) -> None:
        self._provider = TracerProvider(resource=Resource.create({"service.name": service_name}))
        self._provider.add_span_processor(SimpleSpanProcessor(exporter or ConsoleSpanExporter()))
        self._tracer = self._provider.get_tracer("agent_governor.warden")

    def export_action(
        self,
        record: ActionRecord,
        spans: list[Span],
        owner: str | None,
    ) -> None:
        total_ms = record.duration_ms or max(
            (s.start_offset_ms + s.duration_ms for s in spans), default=0.0
        )
        started = record.timestamp - timedelta(milliseconds=total_ms)
        t0_ns = int(started.timestamp() * 1e9)

        attributes: dict[str, Any] = {
            "gen_ai.agent.id": record.agent_id,
            "gen_ai.tool.name": record.tool,
            "governor.action_id": record.action_id,
            "governor.ok": record.ok,
            "governor.denied": record.denied,
            "http.response.status_code": record.status_code,
        }
        if owner:
            attributes["governor.owner"] = owner
        for field, key in _ATTR_KEYS:
            value = getattr(record, field)
            if value is not None:
                attributes[key] = value

        root = self._tracer.start_span(
            f"gateway.execute {record.tool}", start_time=t0_ns, attributes=attributes
        )
        if record.denied:
            root.set_status(Status(StatusCode.ERROR, record.error or "denied"))
        root_context = set_span_in_context(root)

        exported: dict[str, Any] = {}
        # parents carry parent_id=None and were recorded before their children
        for span in sorted(spans, key=lambda s: (s.parent_id is not None, s.start_offset_ms)):
            parent_context = (
                set_span_in_context(exported[span.parent_id])
                if span.parent_id in exported
                else root_context
            )
            child = self._tracer.start_span(
                span.name,
                context=parent_context,
                start_time=t0_ns + int(span.start_offset_ms * 1e6),
                attributes={k: v for k, v in span.attrs.items() if v is not None},
            )
            exported[span.span_id] = child
            child.end(end_time=t0_ns + int((span.start_offset_ms + span.duration_ms) * 1e6))

        root.end(end_time=int(record.timestamp.timestamp() * 1e9))

    def shutdown(self) -> None:
        self._provider.shutdown()
