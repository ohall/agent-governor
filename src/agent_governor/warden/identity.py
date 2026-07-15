"""Agent identity: short-lived JWT credentials.

Simulates the SPIFFE / RFC 8693 short-lived-workload-credential pattern
without running SPIRE: agents register a manifest and receive a 10-minute
HS256 JWT scoped to exactly that manifest. Expired or over-scope tokens are
denied and audited. The clock is injectable so expiry is testable.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING

import jwt
from pydantic import BaseModel

if TYPE_CHECKING:
    from collections.abc import Callable

    from agent_governor.warden.gateway import AgentManifest

ALGORITHM = "HS256"


class TokenError(Exception):
    """Token failed validation; the message is safe to audit and return."""


class TokenClaims(BaseModel):
    sub: str  # agent_id
    owner: str
    scope: tuple[str, ...]  # allowed tools frozen into the credential
    lvl: int  # max autonomy level at issue time
    iat: int
    exp: int
    jti: str


class TokenIssuer:
    """Issues and validates gateway session tokens."""

    def __init__(
        self,
        secret: str,
        ttl_s: int,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._secret = secret
        self._ttl_s = ttl_s
        self._clock = clock or (lambda: datetime.now(UTC))

    def now(self) -> datetime:
        return self._clock()

    def issue(self, manifest: AgentManifest) -> tuple[str, TokenClaims]:
        issued = int(self.now().timestamp())
        claims = TokenClaims(
            sub=manifest.agent_id,
            owner=manifest.owner,
            scope=manifest.allowed_tools,
            lvl=int(manifest.max_autonomy_level),
            iat=issued,
            exp=issued + self._ttl_s,
            jti=uuid.uuid4().hex,
        )
        token = jwt.encode(claims.model_dump(), self._secret, algorithm=ALGORITHM)
        return token, claims

    def validate(self, token: str) -> TokenClaims:
        """Return claims or raise TokenError. Expiry is checked against the
        injected clock (not the library's wall clock) so tests can travel in time."""
        try:
            payload = jwt.decode(
                token,
                self._secret,
                algorithms=[ALGORITHM],
                options={"verify_exp": False},
            )
        except jwt.InvalidTokenError as exc:
            raise TokenError(f"token invalid: {exc}") from exc
        claims = TokenClaims.model_validate(payload)
        if int(self.now().timestamp()) >= claims.exp:
            raise TokenError("token expired")
        return claims
