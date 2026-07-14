"""Fault-injection layer.

Every non-admin request passes through :class:`FaultMiddleware`, which may
inject one of four fault kinds. Faults are drawn from a seeded RNG so a run
is reproducible given (seed, request sequence), and any request can force or
suppress a fault via headers:

    X-Inject-Fault: rate_limit | timeout | malformed | wrong_data | none

Injected responses always carry ``X-Fault-Injected: <kind>`` so harnesses
have ground truth for recovery-rate measurement.
"""

from __future__ import annotations

import asyncio
import json
import random
import re
from enum import StrEnum
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, Field
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse, Response

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from starlette.requests import Request

FAULT_HEADER = "X-Fault-Injected"
FORCE_HEADER = "x-inject-fault"

_ID_PATTERN = re.compile(r"^(?P<prefix>[A-Z]{3}-)(?P<num>\d+)$")

# Value pools for plausible corruption: a wrong-but-valid-looking enum value
# is far more dangerous to an agent than an obviously broken one.
_ENUM_POOLS: list[list[str]] = [
    ["critical", "high", "medium", "low"],
    ["new", "investigating", "remediating", "resolved", "closed"],
    ["tier-1", "tier-2", "tier-3"],
]


class FaultKind(StrEnum):
    RATE_LIMIT = "rate_limit"
    TIMEOUT = "timeout"
    MALFORMED = "malformed"
    WRONG_DATA = "wrong_data"


class FaultConfig(BaseModel):
    """Per-kind injection probabilities plus the RNG seed."""

    rates: dict[FaultKind, float] = Field(
        default_factory=lambda: dict.fromkeys(FaultKind, 0.0),
    )
    seed: int = 42
    timeout_delay_s: float = 0.05

    def total_rate(self) -> float:
        return sum(self.rates.values())


class FaultInjector:
    """Seeded fault chooser + fault response builder."""

    def __init__(self, config: FaultConfig) -> None:
        self.config = config
        self.rng = random.Random(config.seed)

    def choose(self) -> FaultKind | None:
        """Draw at most one fault for a request (cumulative-probability walk)."""
        if self.config.total_rate() <= 0:
            return None
        draw = self.rng.random()
        cumulative = 0.0
        for kind in FaultKind:
            cumulative += self.config.rates.get(kind, 0.0)
            if draw < cumulative:
                return kind
        return None

    async def respond(
        self,
        kind: FaultKind,
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        if kind is FaultKind.RATE_LIMIT:
            return JSONResponse(
                {"detail": "rate limited"},
                status_code=429,
                headers={"Retry-After": "1", FAULT_HEADER: kind.value},
            )
        if kind is FaultKind.TIMEOUT:
            await asyncio.sleep(self.config.timeout_delay_s)
            return JSONResponse(
                {"detail": "upstream timeout"},
                status_code=504,
                headers={FAULT_HEADER: kind.value},
            )
        if kind is FaultKind.MALFORMED:
            return Response(
                content='{"incidents": [{"id": "INC-10',
                media_type="application/json",
                status_code=200,
                headers={FAULT_HEADER: kind.value},
            )
        return await self._respond_wrong_data(request, call_next)

    async def _respond_wrong_data(
        self,
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        """Serve the real response with one field silently corrupted."""
        response = await call_next(request)
        body = b"".join([chunk async for chunk in response.body_iterator])  # type: ignore[attr-defined]
        if response.status_code >= 300 or not body:
            return self._rebuild(response, body, mark=True)
        try:
            payload = json.loads(body)
        except json.JSONDecodeError:
            return self._rebuild(response, body, mark=True)
        corrupt_value(payload, self.rng)
        return JSONResponse(
            payload,
            status_code=response.status_code,
            headers={FAULT_HEADER: FaultKind.WRONG_DATA.value},
        )

    @staticmethod
    def _rebuild(response: Response, body: bytes, *, mark: bool) -> Response:
        headers = dict(response.headers)
        headers.pop("content-length", None)
        if mark:
            headers[FAULT_HEADER] = FaultKind.WRONG_DATA.value
        return Response(
            content=body,
            status_code=response.status_code,
            headers=headers,
            media_type=response.media_type,
        )


def _candidates(node: Any, out: list[tuple[Any, Any, Any]]) -> None:
    """Collect (container, key, value) triples of corruptible leaf fields."""
    if isinstance(node, dict):
        for key, value in node.items():
            if isinstance(value, dict | list):
                _candidates(value, out)
            elif isinstance(value, str | int | float | bool) and key != "detail":
                out.append((node, key, value))
    elif isinstance(node, list):
        for idx, value in enumerate(node):
            if isinstance(value, dict | list):
                _candidates(value, out)
            elif isinstance(value, str | int | float | bool):
                out.append((node, idx, value))


def corrupt_value(payload: Any, rng: random.Random) -> None:
    """Mutate exactly one leaf field into a wrong-but-plausible value, in place."""
    found: list[tuple[Any, Any, Any]] = []
    _candidates(payload, found)
    if not found:
        return
    container, key, value = rng.choice(found)
    container[key] = _plausible_wrong(value, rng)


def _plausible_wrong(value: Any, rng: random.Random) -> Any:
    if isinstance(value, bool):
        return not value
    if isinstance(value, int | float):
        return value + 1
    if isinstance(value, str):
        match = _ID_PATTERN.match(value)
        if match:
            return f"{match.group('prefix')}{int(match.group('num')) + 1}"
        for pool in _ENUM_POOLS:
            if value in pool:
                alternatives = [v for v in pool if v != value]
                return rng.choice(alternatives)
        return value + " (updated)"
    return value


class FaultMiddleware(BaseHTTPMiddleware):
    """Applies fault injection to every non-exempt request."""

    EXEMPT_PREFIXES = ("/health", "/admin", "/docs", "/openapi")

    async def dispatch(
        self,
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        if request.url.path.startswith(self.EXEMPT_PREFIXES):
            return await call_next(request)

        injector: FaultInjector = request.app.state.faults
        forced = request.headers.get(FORCE_HEADER)
        if forced is not None:
            if forced.lower() in {"none", ""}:
                return await call_next(request)
            kind = FaultKind(forced.lower())
        else:
            kind = injector.choose()  # type: ignore[assignment]
            if kind is None:
                return await call_next(request)
        return await injector.respond(kind, request, call_next)
