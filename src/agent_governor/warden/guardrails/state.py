"""Shared state for guardrail pipelines.

Parallel checks publish verdicts and trip counters through this interface.
The in-memory implementation is the keyless default; the same protocol is
the seam for a Redis-backed implementation when the pipeline spans
processes (documented future work — it changes deployment, not the math).
"""

from __future__ import annotations

import asyncio
from collections import defaultdict
from datetime import UTC, datetime
from typing import Protocol

from agent_governor.warden.guardrails.models import GuardrailVerdict


class SharedState(Protocol):
    async def record_verdict(self, action_id: str, verdict: GuardrailVerdict) -> None: ...

    async def verdicts_for(self, action_id: str) -> list[GuardrailVerdict]: ...

    async def record_trip(self, agent_id: str, check: str) -> None: ...

    async def trips_since(self, agent_id: str, since: datetime) -> int: ...


class InMemorySharedState:
    """Asyncio-safe in-process shared state."""

    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._verdicts: dict[str, list[GuardrailVerdict]] = defaultdict(list)
        self._trips: dict[str, list[tuple[datetime, str]]] = defaultdict(list)

    async def record_verdict(self, action_id: str, verdict: GuardrailVerdict) -> None:
        async with self._lock:
            self._verdicts[action_id].append(verdict)

    async def verdicts_for(self, action_id: str) -> list[GuardrailVerdict]:
        async with self._lock:
            return list(self._verdicts[action_id])

    async def record_trip(self, agent_id: str, check: str) -> None:
        async with self._lock:
            self._trips[agent_id].append((datetime.now(UTC), check))

    async def trips_since(self, agent_id: str, since: datetime) -> int:
        async with self._lock:
            return sum(1 for ts, _ in self._trips[agent_id] if ts >= since)
