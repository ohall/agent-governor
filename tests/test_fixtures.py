from __future__ import annotations

from agent_governor.mockops.state import build_store


def test_same_seed_builds_identical_stores() -> None:
    a, b = build_store(42), build_store(42)
    assert a.incidents == b.incidents
    assert a.services == b.services
    assert a.users == b.users
    assert a.record_sets == b.record_sets


def test_referential_integrity() -> None:
    store = build_store(42)
    service_ids = set(store.services)
    for incident in store.incidents.values():
        assert incident.service_id in service_ids
    for service in store.services.values():
        for dep in service.dependencies:
            assert dep in service_ids, f"{service.id} depends on unknown {dep}"


def test_fixture_shape() -> None:
    store = build_store(42)
    assert len(store.incidents) == 12
    assert len(store.services) == 8
    assert len(store.users) == 6
    assert len(store.record_sets) == 4
    # the dangerous fixtures the governance demo relies on must exist
    assert store.record_sets["customer_pii_archive"].contains_pii
    assert any("automation" in u.roles for u in store.users.values())
