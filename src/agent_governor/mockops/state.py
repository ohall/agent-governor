"""In-memory store for the mock ops estate."""

from __future__ import annotations

from dataclasses import dataclass, field

from agent_governor.mockops import fixtures
from agent_governor.mockops.models import Incident, RecordSet, Service, User


@dataclass
class Store:
    """Mutable estate state. Rebuild via :func:`build_store` for a clean slate."""

    seed: int
    incidents: dict[str, Incident] = field(default_factory=dict)
    services: dict[str, Service] = field(default_factory=dict)
    users: dict[str, User] = field(default_factory=dict)
    record_sets: dict[str, RecordSet] = field(default_factory=dict)
    notifications_sent: int = 0
    deploys_performed: int = 0


def build_store(seed: int) -> Store:
    """Build a fully-populated store. Same seed -> identical store."""
    return Store(
        seed=seed,
        incidents={i.id: i for i in fixtures.build_incidents()},
        services={s.id: s for s in fixtures.build_services()},
        users={u.id: u for u in fixtures.build_users()},
        record_sets={r.name: r for r in fixtures.build_record_sets()},
    )
