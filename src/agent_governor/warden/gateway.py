"""The governance gateway (M2: pass-through with the chokepoint contract).

Responsibilities already live in M2:
- agents register a manifest and receive a session (opaque token now, JWT in M3)
- tool calls resolve against a declarative registry (unknown tool -> denied)
- calls outside the manifest's allowed_tools are denied, never forwarded
- every call is appended to an action log (SQLite audit store arrives in M4)
"""

from __future__ import annotations

import secrets
import string
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import httpx
from pydantic import BaseModel, Field

from agent_governor.warden.levels import AutonomyLevel

FAULT_HEADER = "X-Fault-Injected"


class AgentManifest(BaseModel):
    """Who the agent is, who owns it, and the outer bounds of what it may ever do."""

    agent_id: str = Field(min_length=1)
    owner: str = Field(min_length=1, description="A named human, never a team.")
    purpose: str = Field(min_length=1)
    max_autonomy_level: AutonomyLevel = AutonomyLevel.L4_APPROVER
    allowed_tools: tuple[str, ...] = ()
    risk_tier: str = "medium"


@dataclass(frozen=True)
class GatewaySession:
    """A registered agent's live session. The token is opaque in M2; M3 makes it a JWT."""

    token: str
    manifest: AgentManifest
    issued_at: datetime


class ToolCall(BaseModel):
    tool: str
    params: dict[str, Any] = Field(default_factory=dict)


class ToolResult(BaseModel):
    tool: str
    status_code: int
    ok: bool
    denied: bool = False
    data: Any = None
    fault: str | None = None
    error: str | None = None


@dataclass(frozen=True)
class ActionRecord:
    """Minimal per-action trace; the auditor's-question record grows from here."""

    timestamp: datetime
    agent_id: str
    tool: str
    params: dict[str, Any]
    status_code: int
    ok: bool
    denied: bool
    fault: str | None


class ToolSpecError(Exception):
    """A tool call could not be built into an HTTP request."""


@dataclass(frozen=True)
class ToolSpec:
    """Declarative mapping from a named tool to a mockops endpoint."""

    name: str
    method: str
    path: str
    body_fields: frozenset[str] = frozenset()
    query_fields: frozenset[str] = frozenset()

    def path_params(self) -> set[str]:
        return {name for _, name, _, _ in string.Formatter().parse(self.path) if name is not None}

    def build(self, params: dict[str, Any]) -> tuple[str, dict[str, Any], dict[str, Any]]:
        """Return (url, json_body, query) or raise ToolSpecError on missing path params."""
        required = self.path_params()
        missing = required - params.keys()
        if missing:
            raise ToolSpecError(f"missing path params {sorted(missing)} for tool {self.name!r}")
        url = self.path.format(**{k: params[k] for k in required})
        body = {k: v for k, v in params.items() if k in self.body_fields and v is not None}
        query = {k: v for k, v in params.items() if k in self.query_fields and v is not None}
        return url, body, query


TOOL_REGISTRY: dict[str, ToolSpec] = {
    spec.name: spec
    for spec in (
        ToolSpec(
            name="list_incidents",
            method="GET",
            path="/incidents",
            query_fields=frozenset({"state", "service_id"}),
        ),
        ToolSpec(name="get_incident", method="GET", path="/incidents/{incident_id}"),
        ToolSpec(
            name="update_incident",
            method="PATCH",
            path="/incidents/{incident_id}",
            body_fields=frozenset({"state", "severity", "assigned_to", "description"}),
        ),
        ToolSpec(
            name="resolve_incident",
            method="POST",
            path="/incidents/{incident_id}/resolve",
            body_fields=frozenset({"resolution_note"}),
        ),
        ToolSpec(name="list_services", method="GET", path="/cmdb/services"),
        ToolSpec(name="get_service", method="GET", path="/cmdb/services/{service_id}"),
        ToolSpec(
            name="deploy_service",
            method="POST",
            path="/deploy",
            body_fields=frozenset({"service_id", "version"}),
        ),
        ToolSpec(
            name="send_notification",
            method="POST",
            path="/notify",
            body_fields=frozenset({"channel", "recipient", "message"}),
        ),
        ToolSpec(
            name="change_privileges",
            method="POST",
            path="/users/{user_id}/privileges",
            body_fields=frozenset({"role", "action"}),
        ),
        ToolSpec(name="list_record_sets", method="GET", path="/records"),
        ToolSpec(name="delete_records", method="DELETE", path="/records/{record_set}"),
    )
}


class Gateway:
    """The single door between agents and the ops estate."""

    def __init__(self, client: httpx.Client) -> None:
        self._client = client
        self.action_log: list[ActionRecord] = []

    def register(self, manifest: AgentManifest) -> GatewaySession:
        return GatewaySession(
            token=secrets.token_urlsafe(24),
            manifest=manifest,
            issued_at=datetime.now(UTC),
        )

    def execute(self, session: GatewaySession, call: ToolCall) -> ToolResult:
        spec = TOOL_REGISTRY.get(call.tool)
        if spec is None:
            return self._deny(session, call, f"unknown tool {call.tool!r}")
        if call.tool not in session.manifest.allowed_tools:
            return self._deny(session, call, f"tool {call.tool!r} not permitted by manifest")
        try:
            url, body, query = spec.build(call.params)
        except ToolSpecError as exc:
            return self._deny(session, call, str(exc))

        response = self._client.request(
            spec.method,
            url,
            json=body or None,
            params=query or None,
            headers={"Authorization": f"Bearer {session.token}"},
        )
        fault = response.headers.get(FAULT_HEADER)
        try:
            data: Any = response.json()
        except ValueError:
            data = None
        result = ToolResult(
            tool=call.tool,
            status_code=response.status_code,
            ok=response.is_success,
            data=data,
            fault=fault,
        )
        self._record(session, call, result)
        return result

    def _deny(self, session: GatewaySession, call: ToolCall, reason: str) -> ToolResult:
        result = ToolResult(tool=call.tool, status_code=0, ok=False, denied=True, error=reason)
        self._record(session, call, result)
        return result

    def _record(self, session: GatewaySession, call: ToolCall, result: ToolResult) -> None:
        self.action_log.append(
            ActionRecord(
                timestamp=datetime.now(UTC),
                agent_id=session.manifest.agent_id,
                tool=call.tool,
                params=dict(call.params),
                status_code=result.status_code,
                ok=result.ok,
                denied=result.denied,
                fault=result.fault,
            )
        )
