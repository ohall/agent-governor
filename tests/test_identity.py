from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from agent_governor.warden.gateway import AgentManifest
from agent_governor.warden.identity import TokenError, TokenIssuer
from agent_governor.warden.levels import AutonomyLevel

MANIFEST = AgentManifest(
    agent_id="agent-x",
    owner="priya.nair",
    purpose="testing",
    max_autonomy_level=AutonomyLevel.L3_CONSULTANT,
    allowed_tools=("get_incident", "list_incidents"),
)


class FakeClock:
    def __init__(self, start: datetime) -> None:
        self.now = start

    def __call__(self) -> datetime:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += timedelta(seconds=seconds)


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock(datetime(2026, 7, 14, 9, 0, 0, tzinfo=UTC))


def test_issue_and_validate_roundtrip(clock: FakeClock) -> None:
    issuer = TokenIssuer(
        "unit-test-secret-0123456789abcdef0123456789abcdef", ttl_s=600, clock=clock
    )
    token, issued_claims = issuer.issue(MANIFEST)
    claims = issuer.validate(token)
    assert claims == issued_claims
    assert claims.sub == "agent-x"
    assert claims.owner == "priya.nair"
    assert claims.scope == ("get_incident", "list_incidents")
    assert claims.lvl == 3
    assert claims.exp - claims.iat == 600


def test_token_expires_after_ttl(clock: FakeClock) -> None:
    issuer = TokenIssuer(
        "unit-test-secret-0123456789abcdef0123456789abcdef", ttl_s=600, clock=clock
    )
    token, _ = issuer.issue(MANIFEST)
    clock.advance(599)
    issuer.validate(token)  # still fine at 9:59
    clock.advance(2)
    with pytest.raises(TokenError, match="expired"):
        issuer.validate(token)


def test_tampered_token_rejected(clock: FakeClock) -> None:
    issuer = TokenIssuer(
        "unit-test-secret-0123456789abcdef0123456789abcdef", ttl_s=600, clock=clock
    )
    token, _ = issuer.issue(MANIFEST)
    with pytest.raises(TokenError, match="invalid"):
        issuer.validate(token[:-2] + "xx")


def test_wrong_secret_rejected(clock: FakeClock) -> None:
    token, _ = TokenIssuer(
        "unit-test-secret-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", ttl_s=600, clock=clock
    ).issue(MANIFEST)
    with pytest.raises(TokenError, match="invalid"):
        TokenIssuer(
            "unit-test-secret-bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb", ttl_s=600, clock=clock
        ).validate(token)


def test_jti_unique_per_issue(clock: FakeClock) -> None:
    issuer = TokenIssuer(
        "unit-test-secret-0123456789abcdef0123456789abcdef", ttl_s=600, clock=clock
    )
    _, a = issuer.issue(MANIFEST)
    _, b = issuer.issue(MANIFEST)
    assert a.jti != b.jti
