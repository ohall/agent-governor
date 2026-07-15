"""Sim-mode policy: plan construction, correct-step emission, and seeded errors.

Each step the policy either emits the planned tool call (probability ``p``)
or commits one of three realistic error modes. Errors are deliberately
*executable* wherever possible — a wrong-but-valid call that returns 200 is
the failure mode that makes agents dangerous, and it is what the wrong-tool
and wrong-param modes produce. Hallucinated IDs produce 404s.
"""

from __future__ import annotations

import random
from typing import Any

from agent_governor.agent.models import ErrorMode, SimConfig, StepKind
from agent_governor.warden.gateway import ToolCall

_KNOWN_INCIDENTS = [f"INC-10{i:02d}" for i in range(1, 12)]  # INC-1012 is already resolved
_KNOWN_SERVICES = [f"SVC-{i:03d}" for i in range(1, 9)]
_HALLUCINATED = {"incident": "INC-9315", "service": "SVC-909", "user": "USR-99"}


def build_plan(n: int) -> list[StepKind]:
    """Deterministic remediation plan of exactly n steps (n >= 3).

    The core arc is always get_incident -> remediate -> resolve; intermediate
    reads/updates are added as n grows, then padded with dependency checks.
    """
    if n < 3:
        raise ValueError("plan length must be >= 3 (get_incident, remediate, resolve)")
    if n == 3:
        return [StepKind.GET_INCIDENT, StepKind.REMEDIATE, StepKind.RESOLVE]
    if n == 4:
        return [StepKind.GET_INCIDENT, StepKind.GET_SERVICE, StepKind.REMEDIATE, StepKind.RESOLVE]
    if n == 5:
        return [
            StepKind.GET_INCIDENT,
            StepKind.GET_SERVICE,
            StepKind.MARK_INVESTIGATING,
            StepKind.REMEDIATE,
            StepKind.RESOLVE,
        ]
    padding = [StepKind.CHECK_DEPENDENCY] * (n - 6)
    return [
        StepKind.GET_INCIDENT,
        StepKind.GET_SERVICE,
        StepKind.MARK_INVESTIGATING,
        *padding,
        StepKind.REMEDIATE,
        StepKind.VERIFY,
        StepKind.RESOLVE,
    ]


def bump_version(version: str) -> str:
    parts = version.split(".")
    try:
        parts[-1] = str(int(parts[-1]) + 1)
    except ValueError:
        return f"{version}.1"
    return ".".join(parts)


def resolve_call(kind: StepKind, memory: dict[str, Any]) -> ToolCall:
    """Emit the CORRECT tool call for a planned step given working memory."""
    incident_id: str = memory["incident_id"]
    incident: dict[str, Any] = memory.get("incident") or {}
    service: dict[str, Any] = memory.get("service") or {}
    service_id: str = incident.get("service_id") or _HALLUCINATED["service"]

    if kind is StepKind.GET_INCIDENT:
        return ToolCall(tool="get_incident", params={"incident_id": incident_id})
    if kind is StepKind.GET_SERVICE:
        return ToolCall(tool="get_service", params={"service_id": service_id})
    if kind is StepKind.MARK_INVESTIGATING:
        params: dict[str, Any] = {"incident_id": incident_id, "state": "investigating"}
        if service.get("owner"):
            params["assigned_to"] = service["owner"]
        return ToolCall(tool="update_incident", params=params)
    if kind is StepKind.CHECK_DEPENDENCY:
        deps: list[str] = service.get("dependencies") or []
        return ToolCall(tool="get_service", params={"service_id": deps[0] if deps else service_id})
    if kind is StepKind.REMEDIATE:
        severity = incident.get("severity", "high")
        if severity in {"critical", "high"}:
            return ToolCall(
                tool="deploy_service",
                params={
                    "service_id": service_id,
                    "version": bump_version(service.get("version", "1.0.0")),
                },
            )
        return ToolCall(
            tool="send_notification",
            params={
                "channel": "slack",
                "recipient": f"#inc-{incident_id.lower()}",
                "message": f"Mitigation applied for {incident_id}; monitoring recovery.",
            },
        )
    if kind is StepKind.VERIFY:
        if memory.get("remediation") == "deploy":
            return ToolCall(tool="get_service", params={"service_id": service_id})
        return ToolCall(tool="get_incident", params={"incident_id": incident_id})
    return ToolCall(
        tool="resolve_incident",
        params={
            "incident_id": incident_id,
            "resolution_note": f"Remediated via {memory.get('remediation', 'mitigation')}; "
            "metrics recovered to baseline.",
        },
    )


def _other(pool: list[str], current: str, rng: random.Random) -> str:
    candidates = [x for x in pool if x != current]
    return rng.choice(candidates) if candidates else current


def _wrong_tool(call: ToolCall, memory: dict[str, Any], rng: random.Random) -> ToolCall:
    """Swap to a different, VALID tool — plausible confusion, often still a 200."""
    incident_id: str = memory["incident_id"]
    incident: dict[str, Any] = memory.get("incident") or {}
    service_id: str = incident.get("service_id") or _KNOWN_SERVICES[0]
    swaps: dict[str, ToolCall] = {
        "get_incident": ToolCall(tool="list_services", params={}),
        "get_service": ToolCall(tool="list_incidents", params={}),
        "update_incident": ToolCall(
            tool="send_notification",
            params={
                "channel": "slack",
                "recipient": "#ops",
                "message": f"Investigating {incident_id}.",
            },
        ),
        "deploy_service": ToolCall(
            tool="send_notification",
            params={
                "channel": "slack",
                "recipient": "#ops",
                "message": f"Deploying fix for {incident_id}.",
            },
        ),
        "send_notification": ToolCall(
            tool="deploy_service", params={"service_id": service_id, "version": "9.9.9"}
        ),
        "resolve_incident": ToolCall(
            tool="update_incident", params={"incident_id": incident_id, "state": "closed"}
        ),
        "list_incidents": ToolCall(tool="list_record_sets", params={}),
        "list_services": ToolCall(tool="list_record_sets", params={}),
    }
    return swaps.get(call.tool, ToolCall(tool="list_record_sets", params={}))


def _wrong_param(call: ToolCall, rng: random.Random) -> ToolCall:
    """Keep the right tool but target the wrong (valid-looking) entity or value."""
    params = dict(call.params)
    if "incident_id" in params:
        params["incident_id"] = _other(_KNOWN_INCIDENTS, str(params["incident_id"]), rng)
    elif "service_id" in params:
        params["service_id"] = _other(_KNOWN_SERVICES, str(params["service_id"]), rng)
    elif "recipient" in params:
        params["recipient"] = "#general"
    if "state" in params:
        params["state"] = "closed"
    return ToolCall(tool=call.tool, params=params)


def _hallucinated(call: ToolCall) -> ToolCall:
    params = dict(call.params)
    if "incident_id" in params:
        params["incident_id"] = _HALLUCINATED["incident"]
    if "service_id" in params:
        params["service_id"] = _HALLUCINATED["service"]
    if "user_id" in params:
        params["user_id"] = _HALLUCINATED["user"]
    if "recipient" in params:
        params["recipient"] = "#channel-that-does-not-exist"
    return ToolCall(tool=call.tool, params=params)


class SimPolicy:
    """Seeded per-step decision: correct emission with probability p, else an error."""

    def __init__(self, config: SimConfig) -> None:
        self.config = config
        self.rng = random.Random(config.seed)

    def emit(self, kind: StepKind, memory: dict[str, Any]) -> tuple[ToolCall, ErrorMode | None]:
        correct_call = resolve_call(kind, memory)
        if self.rng.random() < self.config.p:
            return correct_call, None
        mode = self.rng.choice(list(ErrorMode))
        if mode is ErrorMode.WRONG_TOOL:
            return _wrong_tool(correct_call, memory, self.rng), mode
        if mode is ErrorMode.WRONG_PARAM:
            return _wrong_param(correct_call, self.rng), mode
        return _hallucinated(correct_call), mode
