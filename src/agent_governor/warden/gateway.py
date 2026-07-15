"""The governance gateway (M3: identity, classification, tiered authorization).

Every call now flows: token validation -> registry/scope check ->
policy classification -> L1-L5 authorization -> auto-forward OR approval
queue. Every decision — allowed, queued, or denied — lands in the action
log with enough context to answer the auditor's question; M4 moves that
log into a queryable SQLite trace store.
"""

from __future__ import annotations

import string
import time
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

import httpx
from pydantic import BaseModel, Field

from agent_governor.warden.approvals import ApprovalQueue, ApprovalStatus
from agent_governor.warden.authz import AuthzPath, authorize
from agent_governor.warden.config import WardenSettings
from agent_governor.warden.identity import TokenClaims, TokenError, TokenIssuer
from agent_governor.warden.levels import AutonomyLevel
from agent_governor.warden.policy import PolicyEngine

if TYPE_CHECKING:
    from collections.abc import Callable

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
    """A registered agent's live session, carrying a short-lived JWT."""

    token: str
    manifest: AgentManifest
    issued_at: datetime


class ToolCall(BaseModel):
    tool: str
    params: dict[str, Any] = Field(default_factory=dict)


class AuthzInfo(BaseModel):
    """How this action was authorized — the heart of the audit record."""

    path: str  # auto | queued | human_approved | denied
    level: int
    action_class: str | None = None
    risk: str | None = None
    category: str | None = None
    policy_version: str | None = None
    reason: str | None = None
    approval_id: str | None = None
    decided_by: str | None = None


class ToolResult(BaseModel):
    tool: str
    status_code: int
    ok: bool
    denied: bool = False
    pending: bool = False
    approval_id: str | None = None
    data: Any = None
    fault: str | None = None
    error: str | None = None
    authz: AuthzInfo | None = None


@dataclass(frozen=True)
class ActionRecord:
    """Per-action trace answering: which agent, which level, which policy,
    which authorization path, approved by whom."""

    action_id: str
    timestamp: datetime
    agent_id: str
    tool: str
    params: dict[str, Any]
    status_code: int
    ok: bool
    denied: bool
    fault: str | None
    level: int | None = None
    path: str | None = None
    action_class: str | None = None
    category: str | None = None
    policy_version: str | None = None
    approval_id: str | None = None
    decided_by: str | None = None
    token_jti: str | None = None
    error: str | None = None


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

    def __init__(
        self,
        client: httpx.Client,
        settings: WardenSettings | None = None,
        policy: PolicyEngine | None = None,
        queue: ApprovalQueue | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._client = client
        self.settings = settings or WardenSettings()
        self._clock = clock or (lambda: datetime.now(UTC))
        self.policy = policy or PolicyEngine.from_yaml(self.settings.policy_path)
        self.queue = queue or ApprovalQueue(self.settings.approvals_db, clock=self._clock)
        self._issuer = TokenIssuer(
            self.settings.jwt_secret, self.settings.token_ttl_s, clock=self._clock
        )
        self._manifests: dict[str, AgentManifest] = {}
        self._levels: dict[str, AutonomyLevel] = {}
        self.action_log: list[ActionRecord] = []

    # --- identity ---------------------------------------------------------

    def register(self, manifest: AgentManifest) -> GatewaySession:
        self._manifests[manifest.agent_id] = manifest
        self._levels.setdefault(manifest.agent_id, manifest.max_autonomy_level)
        token, _ = self._issuer.issue(manifest)
        return GatewaySession(token=token, manifest=manifest, issued_at=self._clock())

    def refresh(self, agent_id: str) -> GatewaySession:
        """Re-issue a short-lived token for an already-registered agent."""
        manifest = self._manifests.get(agent_id)
        if manifest is None:
            raise KeyError(f"agent {agent_id!r} is not registered")
        token, _ = self._issuer.issue(manifest)
        return GatewaySession(token=token, manifest=manifest, issued_at=self._clock())

    # --- autonomy level (mutated by the budget loop in M5) ------------------

    def current_level(self, agent_id: str) -> AutonomyLevel:
        return self._levels[agent_id]

    def set_level(self, agent_id: str, level: AutonomyLevel) -> AutonomyLevel:
        """Clamped to the manifest maximum: promotion never exceeds registration."""
        manifest = self._manifests[agent_id]
        clamped = AutonomyLevel(min(int(level), int(manifest.max_autonomy_level)))
        self._levels[agent_id] = clamped
        return clamped

    # --- the governed execute path -----------------------------------------

    def execute(self, session: GatewaySession, call: ToolCall) -> ToolResult:
        action_id = f"ACT-{uuid.uuid4().hex[:10]}"

        try:
            claims = self._issuer.validate(session.token)
        except TokenError as exc:
            return self._deny(action_id, session, call, str(exc), path="denied")

        spec = TOOL_REGISTRY.get(call.tool)
        if spec is None:
            return self._deny(
                action_id, session, call, f"unknown tool {call.tool!r}", claims=claims
            )
        if call.tool not in claims.scope:
            return self._deny(
                action_id,
                session,
                call,
                f"tool {call.tool!r} outside token scope",
                claims=claims,
            )

        classification = self.policy.classify(call.tool, call.params)
        level = self.current_level(claims.sub)
        decision = authorize(level, classification)

        if decision.path is AuthzPath.APPROVAL_REQUIRED:
            request = self.queue.submit(
                agent_id=claims.sub,
                owner=claims.owner,
                tool=call.tool,
                params=call.params,
                action_class=classification.action_class.value,
                category=classification.category.value if classification.category else None,
                risk=classification.risk.value,
                timeout_s=self.settings.approval_timeout_s,
            )
            authz = AuthzInfo(
                path="queued",
                level=int(level),
                action_class=classification.action_class.value,
                risk=classification.risk.value,
                category=classification.category.value if classification.category else None,
                policy_version=classification.policy_version,
                reason=decision.reason,
                approval_id=request.id,
            )
            result = ToolResult(
                tool=call.tool,
                status_code=202,
                ok=False,
                pending=True,
                approval_id=request.id,
                error=f"queued for human approval ({decision.reason})",
                authz=authz,
            )
            self._record(action_id, session, call, result, authz, claims)
            return result

        authz = AuthzInfo(
            path="auto",
            level=int(level),
            action_class=classification.action_class.value,
            risk=classification.risk.value,
            category=classification.category.value if classification.category else None,
            policy_version=classification.policy_version,
            reason=decision.reason,
        )
        return self._forward(action_id, session, call, spec, authz, claims)

    def await_approval(
        self,
        session: GatewaySession,
        approval_id: str,
        poll_interval_s: float = 0.2,
        wait_timeout_s: float | None = None,
    ) -> ToolResult:
        """Block until a queued action is decided, then execute or report denial."""
        deadline = time.monotonic() + (
            wait_timeout_s if wait_timeout_s is not None else self.settings.approval_timeout_s
        )
        while True:
            request = self.queue.get(approval_id)
            if request is None:
                raise KeyError(f"approval {approval_id!r} not found")
            if request.status is ApprovalStatus.APPROVED:
                return self._execute_approved(session, request)
            if request.status in (ApprovalStatus.DENIED, ApprovalStatus.EXPIRED):
                action_id = f"ACT-{uuid.uuid4().hex[:10]}"
                call = ToolCall(tool=request.tool, params=request.params)
                authz = AuthzInfo(
                    path="denied",
                    level=int(self.current_level(request.agent_id)),
                    action_class=request.action_class,
                    risk=request.risk,
                    category=request.category,
                    policy_version=self.policy.version,
                    reason=request.reason or f"approval {request.status.value}",
                    approval_id=request.id,
                    decided_by=request.decided_by,
                )
                result = ToolResult(
                    tool=request.tool,
                    status_code=0,
                    ok=False,
                    denied=True,
                    approval_id=request.id,
                    error=f"approval {request.status.value}"
                    + (f" by {request.decided_by}" if request.decided_by else ""),
                    authz=authz,
                )
                self._record(action_id, session, call, result, authz, claims=None)
                return result
            if time.monotonic() >= deadline:
                return ToolResult(
                    tool=request.tool,
                    status_code=0,
                    ok=False,
                    denied=True,
                    approval_id=approval_id,
                    error="gave up waiting for approval decision",
                )
            time.sleep(poll_interval_s)

    def _execute_approved(self, session: GatewaySession, request: Any) -> ToolResult:
        action_id = f"ACT-{uuid.uuid4().hex[:10]}"
        call = ToolCall(tool=request.tool, params=request.params)
        try:
            claims = self._issuer.validate(session.token)
        except TokenError:
            session = self.refresh(session.manifest.agent_id)
            claims = self._issuer.validate(session.token)
        spec = TOOL_REGISTRY[request.tool]
        authz = AuthzInfo(
            path="human_approved",
            level=int(self.current_level(request.agent_id)),
            action_class=request.action_class,
            risk=request.risk,
            category=request.category,
            policy_version=self.policy.version,
            reason="explicitly approved by a human",
            approval_id=request.id,
            decided_by=request.decided_by,
        )
        return self._forward(action_id, session, call, spec, authz, claims)

    # --- internals -----------------------------------------------------------

    def _forward(
        self,
        action_id: str,
        session: GatewaySession,
        call: ToolCall,
        spec: ToolSpec,
        authz: AuthzInfo,
        claims: TokenClaims,
    ) -> ToolResult:
        try:
            url, body, query = spec.build(call.params)
        except ToolSpecError as exc:
            return self._deny(action_id, session, call, str(exc), claims=claims)

        response = self._client.request(
            spec.method,
            url,
            json=body or None,
            params=query or None,
            headers={"Authorization": f"Bearer {session.token}"},
        )
        try:
            data: Any = response.json()
        except ValueError:
            data = None
        result = ToolResult(
            tool=call.tool,
            status_code=response.status_code,
            ok=response.is_success,
            data=data,
            fault=response.headers.get(FAULT_HEADER),
            authz=authz,
        )
        self._record(action_id, session, call, result, authz, claims)
        return result

    def _deny(
        self,
        action_id: str,
        session: GatewaySession,
        call: ToolCall,
        reason: str,
        claims: TokenClaims | None = None,
        path: str = "denied",
    ) -> ToolResult:
        authz = AuthzInfo(
            path=path,
            level=int(self._levels.get(session.manifest.agent_id, 0)),
            reason=reason,
        )
        result = ToolResult(
            tool=call.tool, status_code=0, ok=False, denied=True, error=reason, authz=authz
        )
        self._record(action_id, session, call, result, authz, claims)
        return result

    def _record(
        self,
        action_id: str,
        session: GatewaySession,
        call: ToolCall,
        result: ToolResult,
        authz: AuthzInfo | None,
        claims: TokenClaims | None,
    ) -> None:
        self.action_log.append(
            ActionRecord(
                action_id=action_id,
                timestamp=self._clock(),
                agent_id=session.manifest.agent_id,
                tool=call.tool,
                params=dict(call.params),
                status_code=result.status_code,
                ok=result.ok,
                denied=result.denied,
                fault=result.fault,
                level=authz.level if authz else None,
                path=authz.path if authz else None,
                action_class=authz.action_class if authz else None,
                category=authz.category if authz else None,
                policy_version=authz.policy_version if authz else None,
                approval_id=result.approval_id,
                decided_by=authz.decided_by if authz else None,
                token_jti=claims.jti if claims else None,
                error=result.error,
            )
        )
