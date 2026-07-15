"""Models shared across the agent's policy, graph, and runner."""

from __future__ import annotations

from collections.abc import Callable
from enum import StrEnum
from typing import Any, TypedDict

from pydantic import BaseModel, Field

from agent_governor.warden.gateway import ToolCall


class StepKind(StrEnum):
    """Planned steps of an incident-remediation task."""

    GET_INCIDENT = "get_incident"
    GET_SERVICE = "get_service"
    MARK_INVESTIGATING = "mark_investigating"
    CHECK_DEPENDENCY = "check_dependency"
    REMEDIATE = "remediate"
    VERIFY = "verify"
    RESOLVE = "resolve"


class ErrorMode(StrEnum):
    """Realistic agent failure modes committed on an unlucky step draw."""

    WRONG_TOOL = "wrong_tool"
    WRONG_PARAM = "wrong_param"
    HALLUCINATED_ID = "hallucinated_id"


class SimConfig(BaseModel):
    """Sim-mode knobs. Same config + same seed -> identical run."""

    p: float = Field(default=0.95, ge=0.0, le=1.0, description="Per-step success probability.")
    seed: int = 42
    plan_length: int = Field(default=6, ge=3)
    max_retries: int = Field(default=2, ge=0, description="Retries on transient 429/504.")


class StepRecord(BaseModel):
    """What the agent did at one step, and whether it was the right thing."""

    step: StepKind
    call: ToolCall
    error_mode: ErrorMode | None = None
    status_code: int
    ok: bool
    fault: str | None = None
    retries: int = 0
    recovered: bool = False

    @property
    def correct(self) -> bool:
        """The step emitted the planned call AND the estate accepted it."""
        return self.error_mode is None and self.ok


class AgentState(TypedDict):
    """LangGraph channel state for one remediation task."""

    plan: list[StepKind]
    idx: int
    memory: dict[str, Any]
    records: list[StepRecord]


StepFn = Callable[[AgentState], dict[str, Any]]
"""One graph step: consumes state, returns the state delta."""


class TaskOutcome(BaseModel):
    """End-to-end result of one remediation task."""

    incident_id: str
    plan_length: int
    success: bool
    all_steps_correct: bool
    final_state: str | None
    records: list[StepRecord]

    @property
    def error_steps(self) -> list[StepRecord]:
        return [r for r in self.records if r.error_mode is not None]
