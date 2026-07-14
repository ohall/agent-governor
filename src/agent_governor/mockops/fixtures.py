"""Deterministic fixture data for the mock ops estate.

Fixtures are pure data keyed off a fixed base time so two stores built from
the same seed are byte-identical — the property every benchmark relies on.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from agent_governor.mockops.models import (
    Incident,
    IncidentState,
    RecordSet,
    Service,
    ServiceTier,
    Severity,
    User,
)

BASE_TIME = datetime(2026, 3, 2, 9, 0, 0, tzinfo=UTC)


def _t(minutes: int) -> datetime:
    return BASE_TIME + timedelta(minutes=minutes)


def build_services() -> list[Service]:
    return [
        Service(
            id="SVC-001",
            name="payments-api",
            owner="maria.chen",
            tier=ServiceTier.TIER_1,
            dependencies=["SVC-004", "SVC-008"],
            version="4.12.1",
        ),
        Service(
            id="SVC-002",
            name="checkout-web",
            owner="dev.okafor",
            tier=ServiceTier.TIER_1,
            dependencies=["SVC-001", "SVC-006"],
            version="9.3.0",
        ),
        Service(
            id="SVC-003",
            name="auth-service",
            owner="priya.nair",
            tier=ServiceTier.TIER_1,
            dependencies=["SVC-004"],
            version="2.31.4",
        ),
        Service(
            id="SVC-004",
            name="inventory-db",
            owner="sam.wexler",
            tier=ServiceTier.TIER_2,
            dependencies=[],
            version="15.6",
        ),
        Service(
            id="SVC-005",
            name="notifications-worker",
            owner="dev.okafor",
            tier=ServiceTier.TIER_3,
            dependencies=["SVC-004"],
            version="1.8.2",
        ),
        Service(
            id="SVC-006",
            name="search-api",
            owner="lena.fischer",
            tier=ServiceTier.TIER_2,
            dependencies=["SVC-004"],
            version="3.2.7",
        ),
        Service(
            id="SVC-007",
            name="billing-batch",
            owner="maria.chen",
            tier=ServiceTier.TIER_2,
            dependencies=["SVC-001", "SVC-004"],
            version="0.44.0",
        ),
        Service(
            id="SVC-008",
            name="edge-gateway",
            owner="priya.nair",
            tier=ServiceTier.TIER_1,
            dependencies=[],
            version="11.0.3",
        ),
    ]


def build_incidents() -> list[Incident]:
    rows: list[tuple[str, str, str, Severity, IncidentState, str, str | None, int]] = [
        (
            "INC-1001",
            "payments-api p99 latency breach",
            "p99 latency on POST /charge climbed from 240ms to 2.1s over 20 minutes.",
            Severity.CRITICAL,
            IncidentState.NEW,
            "SVC-001",
            None,
            -180,
        ),
        (
            "INC-1002",
            "checkout-web 502 spike",
            "Elevated 502 rate (4.2%) from edge-gateway to checkout-web after deploy 9.3.0.",
            Severity.HIGH,
            IncidentState.INVESTIGATING,
            "SVC-002",
            "dev.okafor",
            -160,
        ),
        (
            "INC-1003",
            "auth-service token validation failures",
            "Intermittent 401s for valid sessions; JWKS refresh appears to be timing out.",
            Severity.HIGH,
            IncidentState.NEW,
            "SVC-003",
            None,
            -140,
        ),
        (
            "INC-1004",
            "inventory-db replication lag",
            "Replica lag at 14 minutes and climbing; reads served from stale replica.",
            Severity.MEDIUM,
            IncidentState.INVESTIGATING,
            "SVC-004",
            "sam.wexler",
            -120,
        ),
        (
            "INC-1005",
            "notifications-worker queue backlog",
            "Backlog of 92k notifications; consumer crash-looping on a malformed payload.",
            Severity.MEDIUM,
            IncidentState.NEW,
            "SVC-005",
            None,
            -100,
        ),
        (
            "INC-1006",
            "search-api stale results",
            "Index refresh job silently failing since 02:10 UTC; results up to 9h stale.",
            Severity.LOW,
            IncidentState.NEW,
            "SVC-006",
            None,
            -90,
        ),
        (
            "INC-1007",
            "billing-batch double-charge reports",
            "Three customer reports of duplicate invoices from last night's batch run.",
            Severity.CRITICAL,
            IncidentState.INVESTIGATING,
            "SVC-007",
            "maria.chen",
            -75,
        ),
        (
            "INC-1008",
            "edge-gateway TLS handshake errors",
            "0.8% of handshakes failing with cert chain errors from one PoP.",
            Severity.MEDIUM,
            IncidentState.NEW,
            "SVC-008",
            None,
            -60,
        ),
        (
            "INC-1009",
            "payments-api elevated declines",
            "Card decline rate 3x baseline; upstream PSP status page green.",
            Severity.HIGH,
            IncidentState.NEW,
            "SVC-001",
            None,
            -45,
        ),
        (
            "INC-1010",
            "auth-service CPU saturation",
            "CPU pinned at 97% on 2/6 pods; suspected regex backtracking in log filter.",
            Severity.MEDIUM,
            IncidentState.REMEDIATING,
            "SVC-003",
            "priya.nair",
            -30,
        ),
        (
            "INC-1011",
            "inventory-db disk usage warning",
            "Primary volume at 83%; growth rate suggests 6 days to exhaustion.",
            Severity.LOW,
            IncidentState.NEW,
            "SVC-004",
            None,
            -20,
        ),
        (
            "INC-1012",
            "search-api resolved: shard rebalance",
            "Hot shard rebalanced; latency back to baseline. Pending close-out review.",
            Severity.LOW,
            IncidentState.RESOLVED,
            "SVC-006",
            "lena.fischer",
            -15,
        ),
    ]
    return [
        Incident(
            id=iid,
            title=title,
            description=desc,
            severity=sev,
            state=state,
            service_id=svc,
            assigned_to=assignee,
            resolution_note="Hot shard rebalanced across nodes 3-5." if iid == "INC-1012" else None,
            created_at=_t(offset),
            updated_at=_t(offset + 5),
        )
        for iid, title, desc, sev, state, svc, assignee, offset in rows
    ]


def build_users() -> list[User]:
    return [
        User(
            id="USR-01",
            name="Maria Chen",
            email="maria.chen@example.test",
            roles=["sre", "incident-commander"],
        ),
        User(id="USR-02", name="Dev Okafor", email="dev.okafor@example.test", roles=["developer"]),
        User(
            id="USR-03",
            name="Priya Nair",
            email="priya.nair@example.test",
            roles=["security", "admin"],
        ),
        User(id="USR-04", name="Sam Wexler", email="sam.wexler@example.test", roles=["dba"]),
        User(
            id="USR-05",
            name="Lena Fischer",
            email="lena.fischer@example.test",
            roles=["developer"],
        ),
        User(id="USR-06", name="Ops Bot", email="ops-bot@example.test", roles=["automation"]),
    ]


def build_record_sets() -> list[RecordSet]:
    return [
        RecordSet(
            name="tmp_sessions",
            description="Expired session tokens eligible for cleanup.",
            record_count=48213,
            contains_pii=False,
        ),
        RecordSet(
            name="stale_builds",
            description="CI artifacts older than 90 days.",
            record_count=1874,
            contains_pii=False,
        ),
        RecordSet(
            name="audit_logs_2025",
            description="Prior-year audit trail under retention policy.",
            record_count=903331,
            contains_pii=True,
        ),
        RecordSet(
            name="customer_pii_archive",
            description="Archived customer profiles pending legal hold review.",
            record_count=52117,
            contains_pii=True,
        ),
    ]
