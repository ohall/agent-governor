"""The pending-approvals queue: SQLite-backed so a human can approve from a
separate process (`warden approvals approve <id>`) while the agent waits.

Pending requests auto-expire (default 30 minutes) — an unanswered approval
is a denial, and the expiry is recorded, not silent.
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from pathlib import Path
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel

if TYPE_CHECKING:
    from collections.abc import Callable

_SCHEMA = """
CREATE TABLE IF NOT EXISTS approvals (
    id TEXT PRIMARY KEY,
    created_at TEXT NOT NULL,
    agent_id TEXT NOT NULL,
    owner TEXT NOT NULL,
    tool TEXT NOT NULL,
    params_json TEXT NOT NULL,
    action_class TEXT NOT NULL,
    category TEXT,
    risk TEXT NOT NULL,
    status TEXT NOT NULL,
    timeout_s INTEGER NOT NULL,
    decided_by TEXT,
    decided_at TEXT,
    reason TEXT
)
"""


class ApprovalStatus(StrEnum):
    PENDING = "pending"
    APPROVED = "approved"
    DENIED = "denied"
    EXPIRED = "expired"


class ApprovalRequest(BaseModel):
    id: str
    created_at: datetime
    agent_id: str
    owner: str
    tool: str
    params: dict[str, Any]
    action_class: str
    category: str | None
    risk: str
    status: ApprovalStatus
    timeout_s: int
    decided_by: str | None = None
    decided_at: datetime | None = None
    reason: str | None = None


class ApprovalQueue:
    """Approval requests with lazy expiry, usable across processes via a file DB."""

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
        with self._connect() as conn:
            conn.execute(_SCHEMA)

    def _connect(self) -> sqlite3.Connection:
        if self._memory_conn is not None:
            return self._memory_conn
        return sqlite3.connect(self._db_path)

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

    def submit(
        self,
        *,
        agent_id: str,
        owner: str,
        tool: str,
        params: dict[str, Any],
        action_class: str,
        category: str | None,
        risk: str,
        timeout_s: int,
    ) -> ApprovalRequest:
        request = ApprovalRequest(
            id=f"APR-{uuid.uuid4().hex[:10]}",
            created_at=self._clock(),
            agent_id=agent_id,
            owner=owner,
            tool=tool,
            params=params,
            action_class=action_class,
            category=category,
            risk=risk,
            status=ApprovalStatus.PENDING,
            timeout_s=timeout_s,
        )
        self._run(
            "INSERT INTO approvals VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                request.id,
                request.created_at.isoformat(),
                request.agent_id,
                request.owner,
                request.tool,
                json.dumps(request.params),
                request.action_class,
                request.category,
                request.risk,
                request.status.value,
                request.timeout_s,
                None,
                None,
                None,
            ),
        )
        return request

    def get(self, approval_id: str) -> ApprovalRequest | None:
        self.expire_stale()
        rows = self._run("SELECT * FROM approvals WHERE id = ?", (approval_id,))
        return self._row_to_request(rows[0]) if rows else None

    def list(self, status: ApprovalStatus | None = None) -> list[ApprovalRequest]:
        self.expire_stale()
        if status is None:
            rows = self._run("SELECT * FROM approvals ORDER BY created_at")
        else:
            rows = self._run(
                "SELECT * FROM approvals WHERE status = ? ORDER BY created_at",
                (status.value,),
            )
        return [self._row_to_request(r) for r in rows]

    def decide(
        self,
        approval_id: str,
        *,
        approve: bool,
        decided_by: str,
        reason: str | None = None,
    ) -> ApprovalRequest:
        request = self.get(approval_id)
        if request is None:
            raise KeyError(f"approval {approval_id!r} not found")
        if request.status is not ApprovalStatus.PENDING:
            raise ValueError(f"approval {approval_id!r} already {request.status}")
        status = ApprovalStatus.APPROVED if approve else ApprovalStatus.DENIED
        self._run(
            "UPDATE approvals SET status = ?, decided_by = ?, decided_at = ?, reason = ? "
            "WHERE id = ?",
            (status.value, decided_by, self._clock().isoformat(), reason, approval_id),
        )
        result = self.get(approval_id)
        if result is None:  # pragma: no cover - row was just written
            raise KeyError(f"approval {approval_id!r} vanished mid-decision")
        return result

    def expire_stale(self) -> int:
        """Auto-deny pending requests older than their timeout. Returns count expired."""
        now = self._clock()
        rows = self._run(
            "SELECT id, created_at, timeout_s FROM approvals WHERE status = ?",
            (ApprovalStatus.PENDING.value,),
        )
        expired = 0
        for approval_id, created_at, timeout_s in rows:
            deadline = datetime.fromisoformat(created_at) + timedelta(seconds=timeout_s)
            if now >= deadline:
                self._run(
                    "UPDATE approvals SET status = ?, decided_by = ?, decided_at = ?, "
                    "reason = ? WHERE id = ?",
                    (
                        ApprovalStatus.EXPIRED.value,
                        "system",
                        now.isoformat(),
                        "approval window elapsed; auto-denied",
                        approval_id,
                    ),
                )
                expired += 1
        return expired

    @staticmethod
    def _row_to_request(row: tuple[Any, ...]) -> ApprovalRequest:
        return ApprovalRequest(
            id=row[0],
            created_at=datetime.fromisoformat(row[1]),
            agent_id=row[2],
            owner=row[3],
            tool=row[4],
            params=json.loads(row[5]),
            action_class=row[6],
            category=row[7],
            risk=row[8],
            status=ApprovalStatus(row[9]),
            timeout_s=row[10],
            decided_by=row[11],
            decided_at=datetime.fromisoformat(row[12]) if row[12] else None,
            reason=row[13],
        )
