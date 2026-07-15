"""The queryable audit store: every action, its authorization story, and its
span tree, in SQLite.

Design test (the auditor's question): for ANY action_id, this store ALONE
must answer "under what governance regime was this action authorized?" —
which agent, which owner, which autonomy level, which policy version, which
authorization path, approved by whom, with which guardrail verdicts and
reasoning context. `warden audit <action_id>` renders that answer.

Span attribute names follow OpenTelemetry GenAI semantic conventions where
they map cleanly (gen_ai.agent.id, gen_ai.tool.name); OTLP export is a
documented seam, not a dependency.
"""

from __future__ import annotations

import json
import sqlite3
import time
import uuid
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, Field

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator

    from agent_governor.warden.gateway import ActionRecord
    from agent_governor.warden.guardrails.models import GuardrailVerdict

_SCHEMA = [
    """
    CREATE TABLE IF NOT EXISTS actions (
        action_id TEXT PRIMARY KEY,
        timestamp TEXT NOT NULL,
        agent_id TEXT NOT NULL,
        owner TEXT,
        tool TEXT NOT NULL,
        params_json TEXT NOT NULL,
        reasoning TEXT,
        status_code INTEGER NOT NULL,
        ok INTEGER NOT NULL,
        denied INTEGER NOT NULL,
        fault TEXT,
        level INTEGER,
        path TEXT,
        action_class TEXT,
        category TEXT,
        risk TEXT,
        policy_version TEXT,
        approval_id TEXT,
        decided_by TEXT,
        token_jti TEXT,
        error TEXT,
        verdicts_json TEXT,
        duration_ms REAL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS budget_events (
        event_id TEXT PRIMARY KEY,
        ts TEXT NOT NULL,
        agent_id TEXT NOT NULL,
        kind TEXT NOT NULL,
        payload_json TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS spans (
        span_id TEXT PRIMARY KEY,
        action_id TEXT NOT NULL,
        parent_id TEXT,
        name TEXT NOT NULL,
        start_offset_ms REAL NOT NULL,
        duration_ms REAL NOT NULL,
        attrs_json TEXT NOT NULL
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_actions_agent_ts ON actions (agent_id, timestamp)",
    "CREATE INDEX IF NOT EXISTS idx_spans_action ON spans (action_id)",
    "CREATE INDEX IF NOT EXISTS idx_events_agent_ts ON budget_events (agent_id, ts)",
]


class Span(BaseModel):
    span_id: str
    parent_id: str | None = None
    name: str
    start_offset_ms: float
    duration_ms: float
    attrs: dict[str, Any] = Field(default_factory=dict)


class Trace:
    """Collects a span tree for one action with perf_counter timing."""

    def __init__(self) -> None:
        self._t0 = time.perf_counter()
        self.spans: list[Span] = []

    @contextmanager
    def span(
        self,
        name: str,
        parent_id: str | None = None,
        attrs: dict[str, Any] | None = None,
    ) -> Iterator[str]:
        span_id = uuid.uuid4().hex[:12]
        start = time.perf_counter()
        try:
            yield span_id
        finally:
            self.spans.append(
                Span(
                    span_id=span_id,
                    parent_id=parent_id,
                    name=name,
                    start_offset_ms=round((start - self._t0) * 1000.0, 3),
                    duration_ms=round((time.perf_counter() - start) * 1000.0, 3),
                    attrs=attrs or {},
                )
            )

    def add_span(
        self,
        name: str,
        start_offset_ms: float,
        duration_ms: float,
        parent_id: str | None = None,
        attrs: dict[str, Any] | None = None,
    ) -> str:
        span_id = uuid.uuid4().hex[:12]
        self.spans.append(
            Span(
                span_id=span_id,
                parent_id=parent_id,
                name=name,
                start_offset_ms=round(start_offset_ms, 3),
                duration_ms=round(duration_ms, 3),
                attrs=attrs or {},
            )
        )
        return span_id

    def offset_ms(self) -> float:
        return round((time.perf_counter() - self._t0) * 1000.0, 3)


class StoredAction(BaseModel):
    action_id: str
    timestamp: datetime
    agent_id: str
    owner: str | None
    tool: str
    params: dict[str, Any]
    reasoning: str | None
    status_code: int
    ok: bool
    denied: bool
    fault: str | None
    level: int | None
    path: str | None
    action_class: str | None
    category: str | None
    risk: str | None
    policy_version: str | None
    approval_id: str | None
    decided_by: str | None
    token_jti: str | None
    error: str | None
    verdicts: list[dict[str, Any]] = Field(default_factory=list)
    duration_ms: float | None = None


class AuditStore:
    """SQLite-backed action + span store (in-memory or file)."""

    def __init__(
        self,
        db_path: str = ":memory:",
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._db_path = db_path
        self._clock = clock or (lambda: datetime.now(UTC))
        self._memory_conn: sqlite3.Connection | None = None
        if db_path == ":memory:":
            self._memory_conn = sqlite3.connect(":memory:")
        else:
            Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        conn = self._connect()
        try:
            for statement in _SCHEMA:
                conn.execute(statement)
            conn.commit()
        finally:
            if self._memory_conn is None:
                conn.close()

    def _connect(self) -> sqlite3.Connection:
        return self._memory_conn or sqlite3.connect(self._db_path)

    def _run(self, query: str, args: tuple[Any, ...] = ()) -> list[tuple[Any, ...]]:
        conn = self._connect()
        try:
            cursor = conn.execute(query, args)
            rows = cursor.fetchall()
            conn.commit()
            return rows
        finally:
            if self._memory_conn is None:
                conn.close()

    def record(
        self,
        record: ActionRecord,
        *,
        owner: str | None,
        risk: str | None,
        reasoning: str | None,
        verdicts: list[GuardrailVerdict] | None,
        spans: list[Span] | None,
    ) -> None:
        self._run(
            "INSERT INTO actions VALUES "
            "(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                record.action_id,
                record.timestamp.isoformat(),
                record.agent_id,
                owner,
                record.tool,
                json.dumps(record.params),
                reasoning,
                record.status_code,
                int(record.ok),
                int(record.denied),
                record.fault,
                record.level,
                record.path,
                record.action_class,
                record.category,
                risk,
                record.policy_version,
                record.approval_id,
                record.decided_by,
                record.token_jti,
                record.error,
                json.dumps([v.model_dump() for v in verdicts]) if verdicts else None,
                record.duration_ms,
            ),
        )
        for span in spans or []:
            self._run(
                "INSERT INTO spans VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    span.span_id,
                    record.action_id,
                    span.parent_id,
                    span.name,
                    span.start_offset_ms,
                    span.duration_ms,
                    json.dumps(span.attrs),
                ),
            )

    def get_action(self, action_id: str) -> StoredAction | None:
        rows = self._run("SELECT * FROM actions WHERE action_id = ?", (action_id,))
        return self._to_action(rows[0]) if rows else None

    def list_actions(
        self,
        agent_id: str | None = None,
        since: datetime | None = None,
        limit: int = 50,
    ) -> list[StoredAction]:
        query = "SELECT * FROM actions"
        clauses, args = [], []
        if agent_id is not None:
            clauses.append("agent_id = ?")
            args.append(agent_id)
        if since is not None:
            clauses.append("timestamp >= ?")
            args.append(since.isoformat())
        if clauses:
            query += " WHERE " + " AND ".join(clauses)
        query += " ORDER BY timestamp DESC, action_id DESC LIMIT ?"
        args.append(str(limit))
        return [self._to_action(r) for r in self._run(query, tuple(args))]

    def spans_for(self, action_id: str) -> list[Span]:
        rows = self._run(
            "SELECT span_id, parent_id, name, start_offset_ms, duration_ms, attrs_json "
            "FROM spans WHERE action_id = ? ORDER BY start_offset_ms",
            (action_id,),
        )
        return [
            Span(
                span_id=r[0],
                parent_id=r[1],
                name=r[2],
                start_offset_ms=r[3],
                duration_ms=r[4],
                attrs=json.loads(r[5]),
            )
            for r in rows
        ]

    def record_event(
        self,
        agent_id: str,
        kind: str,
        payload: dict[str, Any],
        at: datetime | None = None,
    ) -> str:
        event_id = f"EVT-{uuid.uuid4().hex[:10]}"
        self._run(
            "INSERT INTO budget_events VALUES (?, ?, ?, ?, ?)",
            (
                event_id,
                (at or self._clock()).isoformat(),
                agent_id,
                kind,
                json.dumps(payload),
            ),
        )
        return event_id

    def list_events(
        self,
        agent_id: str | None = None,
        kind: str | None = None,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        query = "SELECT event_id, ts, agent_id, kind, payload_json FROM budget_events"
        clauses, args = [], []
        if agent_id is not None:
            clauses.append("agent_id = ?")
            args.append(agent_id)
        if kind is not None:
            clauses.append("kind = ?")
            args.append(kind)
        if clauses:
            query += " WHERE " + " AND ".join(clauses)
        query += " ORDER BY ts DESC, event_id DESC LIMIT ?"
        args.append(str(limit))
        return [
            {
                "event_id": r[0],
                "ts": datetime.fromisoformat(r[1]),
                "agent_id": r[2],
                "kind": r[3],
                "payload": json.loads(r[4]),
            }
            for r in self._run(query, tuple(args))
        ]

    @staticmethod
    def _to_action(row: tuple[Any, ...]) -> StoredAction:
        return StoredAction(
            action_id=row[0],
            timestamp=datetime.fromisoformat(row[1]),
            agent_id=row[2],
            owner=row[3],
            tool=row[4],
            params=json.loads(row[5]),
            reasoning=row[6],
            status_code=row[7],
            ok=bool(row[8]),
            denied=bool(row[9]),
            fault=row[10],
            level=row[11],
            path=row[12],
            action_class=row[13],
            category=row[14],
            risk=row[15],
            policy_version=row[16],
            approval_id=row[17],
            decided_by=row[18],
            token_jti=row[19],
            error=row[20],
            verdicts=json.loads(row[21]) if row[21] else [],
            duration_ms=row[22],
        )


def render_audit(action: StoredAction, spans: list[Span]) -> str:
    """Human-readable reconstruction of one action's authorization story."""
    lines = [
        f"action    {action.action_id}  at {action.timestamp.isoformat()}",
        f"agent     {action.agent_id}" + (f"  (owner: {action.owner})" if action.owner else ""),
        f"tool      {action.tool}  params={json.dumps(action.params)}",
    ]
    if action.reasoning:
        lines.append(f"reasoning {action.reasoning}")
    if action.token_jti:
        lines.append(f"identity  token jti={action.token_jti}")
    lines.append(
        f"policy    version={action.policy_version or '-'}"
        f"  class={action.action_class or '-'}"
        f"  category={action.category or '-'}  risk={action.risk or '-'}"
    )
    level = f"L{action.level}" if action.level else "-"
    authz = f"authz     level={level}  path={action.path or '-'}"
    if action.approval_id:
        authz += f"  approval={action.approval_id}"
    if action.decided_by:
        authz += f"  decided_by={action.decided_by}"
    lines.append(authz)
    if action.verdicts:
        parts = []
        for v in action.verdicts:
            mark = "pass" if v.get("passed") else "TRIP"
            parts.append(f"{v.get('check')}={mark} ({v.get('latency_ms', 0):.0f}ms)")
        lines.append("guardrails " + " · ".join(parts))
    outcome = "DENIED" if action.denied else str(action.status_code)
    duration = f"  ({action.duration_ms:.0f}ms)" if action.duration_ms is not None else ""
    lines.append(
        f"outcome   {outcome}{duration}" + (f"  error={action.error}" if action.error else "")
    )
    if spans:
        lines.append("spans:")
        by_parent: dict[str | None, list[Span]] = {}
        for span in spans:
            by_parent.setdefault(span.parent_id, []).append(span)

        def walk(parent: str | None, depth: int) -> None:
            for span in sorted(by_parent.get(parent, []), key=lambda s: s.start_offset_ms):
                pad = "  " * (depth + 1)
                lines.append(
                    f"{pad}{span.name}  +{span.start_offset_ms:.1f}ms  dur={span.duration_ms:.1f}ms"
                )
                walk(span.span_id, depth + 1)

        walk(None, 0)
    return "\n".join(lines)
