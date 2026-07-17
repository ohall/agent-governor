"""The autonomy budget: error budgets applied to agency.

Six SLOs are computed over a rolling window (max_actions or max_age,
whichever is smaller). Breaching the profile's threshold demotes the agent
one level; a sustained clean streak earns one level back (never above the
manifest maximum). Catastrophic signals — a guardrail-trip burst, or an
always-approve attempt while already demoted — trip the CIRCUIT BREAKER
(the kill-switch): the agent is frozen entirely, every subsequent call
denied, the event recorded, the owner notified.

Evaluation is a synchronous tick after each gateway action: deterministic,
reproducible, demo-safe. Every level change and snapshot is persisted to
the audit store, and the owner is notified (logging stub; webhook seam).
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol

import yaml
from pydantic import BaseModel, Field

from agent_governor.warden.levels import AutonomyLevel

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

    from agent_governor.warden.audit import AuditStore, StoredAction

logger = logging.getLogger("governor.budget")

EXECUTED_PATHS = frozenset({"auto", "human_approved"})


class WindowConfig(BaseModel):
    max_actions: int = 200
    max_age_s: int = 86400


class KillSwitchConfig(BaseModel):
    trip_count: int = 5
    trip_window_s: int = 300


class SloTargets(BaseModel):
    """Lower-is-breach: first four. Higher-is-breach: latency and trip rate."""

    task_completion: float = 0.85
    tool_call_success: float = 0.95
    recovery_rate: float = 0.80
    p99_latency_ms: float = 2000.0
    guardrail_trip_rate: float = 0.05
    trace_grounded: float = 0.70


class BudgetProfile(BaseModel):
    name: str
    note: str = ""
    window: WindowConfig = Field(default_factory=WindowConfig)
    min_actions: int = 50
    breach_threshold: int = 2
    cooldown_actions: int = 50
    promotion_streak: int = 200
    kill_switch: KillSwitchConfig = Field(default_factory=KillSwitchConfig)
    targets: SloTargets = Field(default_factory=SloTargets)

    @classmethod
    def from_yaml(cls, path: str | Path) -> BudgetProfile:
        return cls.model_validate(yaml.safe_load(Path(path).read_text()))


class SloSnapshot(BaseModel):
    """The six dimensions, None where the window has no samples."""

    task_completion: float | None = None
    tool_call_success: float | None = None
    recovery_rate: float | None = None
    p99_latency_ms: float | None = None
    guardrail_trip_rate: float | None = None
    trace_grounded: float | None = None
    samples: dict[str, int] = Field(default_factory=dict)
    breaches: list[str] = Field(default_factory=list)


class BudgetDecision(BaseModel):
    agent_id: str
    level: AutonomyLevel
    frozen: bool
    change: str | None = None  # demotion | promotion | kill_switch | None
    reasons: list[str] = Field(default_factory=list)
    snapshot: SloSnapshot | None = None


class TraceScorer(Protocol):
    """Pluggable trace-grounded scorer; the heuristic default needs no keys."""

    def score(self, actions: Sequence[StoredAction]) -> float | None: ...


class HeuristicTraceScorer:
    """Cheap grounding proxy: successful actions with stated reasoning score
    high; failures score low. An LLM-as-judge scorer plugs into the same
    protocol for richer trajectory review."""

    def score(self, actions: Sequence[StoredAction]) -> float | None:
        executed = [a for a in actions if a.path in EXECUTED_PATHS]
        if not executed:
            return None
        total = 0.0
        for action in executed:
            if not action.ok:
                total += 0.2
            elif action.reasoning:
                total += 1.0
            else:
                total += 0.7
        return round(total / len(executed), 3)


def p99(values: Sequence[float]) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = max(0, math.ceil(0.99 * len(ordered)) - 1)
    return ordered[index]


def compute_slos(
    actions: Sequence[StoredAction],
    task_results: Sequence[bool],
    recovery_results: Sequence[bool],
    targets: SloTargets,
    scorer: TraceScorer,
) -> SloSnapshot:
    """Pure function: window evidence in, six dimensions + breach list out."""
    executed = [a for a in actions if a.path in EXECUTED_PATHS]
    blocked = [a for a in actions if a.path == "guardrail_blocked"]
    attempts = len(executed) + len(blocked)
    latencies = [a.duration_ms for a in executed if a.duration_ms is not None]

    snapshot = SloSnapshot(
        task_completion=(round(sum(task_results) / len(task_results), 3) if task_results else None),
        tool_call_success=(
            round(sum(1 for a in executed if a.ok) / len(executed), 3) if executed else None
        ),
        recovery_rate=(
            round(sum(recovery_results) / len(recovery_results), 3) if recovery_results else None
        ),
        p99_latency_ms=p99(latencies),
        guardrail_trip_rate=round(len(blocked) / attempts, 3) if attempts else None,
        trace_grounded=scorer.score(actions),
        samples={
            "actions": len(actions),
            "executed": len(executed),
            "blocked": len(blocked),
            "tasks": len(task_results),
            "recoveries": len(recovery_results),
        },
    )

    floor_dims = ("task_completion", "tool_call_success", "recovery_rate", "trace_grounded")
    ceiling_dims = ("p99_latency_ms", "guardrail_trip_rate")
    for dim in floor_dims:
        value = getattr(snapshot, dim)
        if value is not None and value < getattr(targets, dim):
            snapshot.breaches.append(f"{dim} {value} < {getattr(targets, dim)}")
    for dim in ceiling_dims:
        value = getattr(snapshot, dim)
        if value is not None and value > getattr(targets, dim):
            snapshot.breaches.append(f"{dim} {value} > {getattr(targets, dim)}")
    return snapshot


@dataclass
class _AgentBudget:
    frozen: bool = False
    freeze_reason: str | None = None
    clean_streak: int = 0
    actions_since_change: int = 0
    task_results: list[tuple[datetime, bool]] = field(default_factory=list)
    recovery_results: list[tuple[datetime, bool]] = field(default_factory=list)
    last_snapshot: SloSnapshot | None = None


class BudgetEngine:
    def __init__(
        self,
        audit: AuditStore,
        profile: BudgetProfile,
        clock: Callable[[], datetime] | None = None,
        notifier: Callable[[str], None] | None = None,
        scorer: TraceScorer | None = None,
    ) -> None:
        self.audit = audit
        self.profile = profile
        self._clock = clock or (lambda: datetime.now(UTC))
        self._notify = notifier or (lambda msg: logger.warning(msg))
        self.scorer: TraceScorer = scorer or HeuristicTraceScorer()
        self._states: dict[str, _AgentBudget] = {}
        logger.info(
            "budget engine active: profile=%s (%s)", profile.name, profile.note or "no note"
        )

    # --- evidence intake ---------------------------------------------------

    def record_task(self, agent_id: str, success: bool) -> None:
        self._state(agent_id).task_results.append((self._clock(), success))

    def record_recovery(self, agent_id: str, recovered: bool) -> None:
        self._state(agent_id).recovery_results.append((self._clock(), recovered))

    # --- kill-switch -------------------------------------------------------

    def is_frozen(self, agent_id: str) -> bool:
        return self._state(agent_id).frozen

    def freeze_reason(self, agent_id: str) -> str | None:
        return self._state(agent_id).freeze_reason

    def note_always_approve_attempt(
        self, agent_id: str, level: AutonomyLevel, max_level: AutonomyLevel
    ) -> bool:
        """An always-approve attempt while ALREADY DEMOTED is catastrophic:
        the agent is reaching for its most dangerous tools while under
        sanction. Freezes and returns True when tripped."""
        if level < max_level:
            self._freeze(
                agent_id,
                f"always-approve action attempted while demoted (L{int(level)} < "
                f"max L{int(max_level)})",
                level,
            )
            return True
        return False

    def unfreeze(self, agent_id: str, by: str) -> None:
        state = self._state(agent_id)
        state.frozen = False
        state.freeze_reason = None
        state.actions_since_change = 0
        state.clean_streak = 0
        self._event(agent_id, "unfreeze", {"by": by})
        self._notify(f"[governor] {agent_id} un-frozen by {by}")

    # --- the tick ------------------------------------------------------------

    def evaluate(
        self, agent_id: str, level: AutonomyLevel, max_level: AutonomyLevel
    ) -> BudgetDecision:
        state = self._state(agent_id)
        if state.frozen:
            return BudgetDecision(
                agent_id=agent_id,
                level=level,
                frozen=True,
                reasons=[state.freeze_reason or "frozen"],
            )

        now = self._clock()
        since = now - timedelta(seconds=self.profile.window.max_age_s)
        actions = self.audit.list_actions(
            agent_id=agent_id, since=since, limit=self.profile.window.max_actions
        )
        state.task_results = [(ts, v) for ts, v in state.task_results if ts >= since][
            -self.profile.window.max_actions :
        ]
        state.recovery_results = [(ts, v) for ts, v in state.recovery_results if ts >= since][
            -self.profile.window.max_actions :
        ]

        snapshot = compute_slos(
            actions,
            [v for _, v in state.task_results],
            [v for _, v in state.recovery_results],
            self.profile.targets,
            self.scorer,
        )
        state.last_snapshot = snapshot
        state.actions_since_change += 1
        self._event(
            agent_id,
            "snapshot",
            {
                "level": int(level),
                "frozen": False,
                "profile": self.profile.name,
                "snapshot": snapshot.model_dump(),
            },
        )

        # kill-switch: guardrail-trip burst
        kill_since = now - timedelta(seconds=self.profile.kill_switch.trip_window_s)
        recent_trips = [
            a for a in actions if a.path == "guardrail_blocked" and a.timestamp >= kill_since
        ]
        if len(recent_trips) >= self.profile.kill_switch.trip_count:
            self._freeze(
                agent_id,
                f"{len(recent_trips)} guardrail trips within "
                f"{self.profile.kill_switch.trip_window_s}s",
                level,
            )
            return BudgetDecision(
                agent_id=agent_id,
                level=level,
                frozen=True,
                change="kill_switch",
                reasons=[self._state(agent_id).freeze_reason or ""],
                snapshot=snapshot,
            )

        executed = [a for a in actions if a.path in EXECUTED_PATHS]
        if len(executed) < self.profile.min_actions:
            return BudgetDecision(agent_id=agent_id, level=level, frozen=False, snapshot=snapshot)

        if snapshot.breaches and len(snapshot.breaches) >= self.profile.breach_threshold:
            state.clean_streak = 0
            if (
                state.actions_since_change >= self.profile.cooldown_actions
                and level > AutonomyLevel.L1_OPERATOR
            ):
                new_level = AutonomyLevel(int(level) - 1)
                state.actions_since_change = 0
                self._event(
                    agent_id,
                    "demotion",
                    {"from": int(level), "to": int(new_level), "reasons": snapshot.breaches},
                )
                self._notify(
                    f"[governor] DEMOTION {agent_id}: L{int(level)} -> L{int(new_level)} "
                    f"({'; '.join(snapshot.breaches)})"
                )
                return BudgetDecision(
                    agent_id=agent_id,
                    level=new_level,
                    frozen=False,
                    change="demotion",
                    reasons=snapshot.breaches,
                    snapshot=snapshot,
                )
        elif not snapshot.breaches:
            state.clean_streak += 1
            if state.clean_streak >= self.profile.promotion_streak and level < max_level:
                new_level = AutonomyLevel(int(level) + 1)
                state.clean_streak = 0
                state.actions_since_change = 0
                self._event(
                    agent_id,
                    "promotion",
                    {"from": int(level), "to": int(new_level)},
                )
                self._notify(
                    f"[governor] promotion {agent_id}: L{int(level)} -> L{int(new_level)} "
                    f"(clean streak of {self.profile.promotion_streak})"
                )
                return BudgetDecision(
                    agent_id=agent_id,
                    level=new_level,
                    frozen=False,
                    change="promotion",
                    snapshot=snapshot,
                )

        return BudgetDecision(agent_id=agent_id, level=level, frozen=False, snapshot=snapshot)

    def last_snapshot(self, agent_id: str) -> SloSnapshot | None:
        return self._state(agent_id).last_snapshot

    # --- internals -----------------------------------------------------------

    def _state(self, agent_id: str) -> _AgentBudget:
        return self._states.setdefault(agent_id, _AgentBudget())

    def _freeze(self, agent_id: str, reason: str, level: AutonomyLevel) -> None:
        state = self._state(agent_id)
        state.frozen = True
        state.freeze_reason = reason
        self._event(agent_id, "kill_switch", {"reason": reason, "level": int(level)})
        self._notify(f"[governor] KILL-SWITCH {agent_id}: {reason} — agent frozen")

    def _event(self, agent_id: str, kind: str, payload: dict[str, Any]) -> None:
        self.audit.record_event(agent_id, kind, payload, at=self._clock())
