from __future__ import annotations

import json
import random

import pytest
from fastapi.testclient import TestClient

from agent_governor.mockops.app import create_app
from agent_governor.mockops.config import MockOpsSettings
from agent_governor.mockops.faults import (
    FAULT_HEADER,
    FaultConfig,
    FaultInjector,
    FaultKind,
    corrupt_value,
)


def make_client(rates: dict[FaultKind, float] | None = None, seed: int = 42) -> TestClient:
    faults = FaultConfig(rates=rates or {}, seed=seed)
    return TestClient(create_app(MockOpsSettings(seed=seed, faults=faults)))


class TestForcedFaults:
    def test_forced_rate_limit(self, client: TestClient) -> None:
        response = client.get("/incidents", headers={"X-Inject-Fault": "rate_limit"})
        assert response.status_code == 429
        assert response.headers[FAULT_HEADER] == "rate_limit"
        assert response.headers["Retry-After"] == "1"

    def test_forced_timeout(self, client: TestClient) -> None:
        response = client.get("/incidents", headers={"X-Inject-Fault": "timeout"})
        assert response.status_code == 504
        assert response.headers[FAULT_HEADER] == "timeout"

    def test_forced_malformed_body_is_invalid_json(self, client: TestClient) -> None:
        response = client.get("/incidents", headers={"X-Inject-Fault": "malformed"})
        assert response.status_code == 200
        assert response.headers[FAULT_HEADER] == "malformed"
        with pytest.raises(json.JSONDecodeError):
            response.json()

    def test_forced_wrong_data_mutates_exactly_one_field(self, client: TestClient) -> None:
        clean = client.get("/incidents/INC-1001", headers={"X-Inject-Fault": "none"}).json()
        faulty_response = client.get(
            "/incidents/INC-1001", headers={"X-Inject-Fault": "wrong_data"}
        )
        assert faulty_response.status_code == 200
        assert faulty_response.headers[FAULT_HEADER] == "wrong_data"
        faulty = faulty_response.json()
        diffs = [k for k in clean if clean[k] != faulty[k]]
        assert len(diffs) == 1

    def test_header_none_suppresses_injection(self) -> None:
        client = make_client(rates=dict.fromkeys(FaultKind, 0.25))
        response = client.get("/incidents", headers={"X-Inject-Fault": "none"})
        assert response.status_code == 200
        assert FAULT_HEADER not in response.headers


class TestProbabilisticInjection:
    def test_zero_rates_never_inject(self, client: TestClient) -> None:
        for _ in range(20):
            assert FAULT_HEADER not in client.get("/health").headers
            assert FAULT_HEADER not in client.get("/incidents").headers

    def test_rate_one_always_injects(self) -> None:
        client = make_client(rates={FaultKind.RATE_LIMIT: 1.0})
        for _ in range(10):
            assert client.get("/incidents").status_code == 429

    def test_same_seed_same_fault_sequence(self) -> None:
        rates = {FaultKind.RATE_LIMIT: 0.3, FaultKind.TIMEOUT: 0.2}

        def sequence(seed: int) -> list[str]:
            client = make_client(rates=rates, seed=seed)
            return [client.get("/incidents").headers.get(FAULT_HEADER, "none") for _ in range(40)]

        run_a, run_b = sequence(7), sequence(7)
        assert run_a == run_b
        assert set(run_a) > {"none"}  # some faults actually fired
        assert sequence(8) != run_a  # and the seed matters

    def test_admin_endpoints_exempt_from_faults(self) -> None:
        client = make_client(rates={FaultKind.RATE_LIMIT: 1.0})
        assert client.get("/health").status_code == 200
        assert client.get("/admin/faults").status_code == 200

    def test_reconfigure_rates_via_admin(self, client: TestClient) -> None:
        new_config = {
            "rates": {"rate_limit": 1.0},
            "seed": 42,
            "timeout_delay_s": 0.05,
        }
        assert client.put("/admin/faults", json=new_config).status_code == 200
        assert client.get("/incidents").status_code == 429


class TestChooser:
    def test_cumulative_walk_respects_rates(self) -> None:
        injector = FaultInjector(
            FaultConfig(rates={FaultKind.RATE_LIMIT: 0.5, FaultKind.TIMEOUT: 0.5}, seed=1)
        )
        draws = [injector.choose() for _ in range(500)]
        assert None not in draws  # total rate is 1.0
        counts = {k: draws.count(k) for k in (FaultKind.RATE_LIMIT, FaultKind.TIMEOUT)}
        assert 150 < counts[FaultKind.RATE_LIMIT] < 350  # roughly balanced

    def test_partial_rate_sometimes_passes_through(self) -> None:
        injector = FaultInjector(FaultConfig(rates={FaultKind.MALFORMED: 0.2}, seed=1))
        draws = [injector.choose() for _ in range(500)]
        injected = [d for d in draws if d is not None]
        assert 50 < len(injected) < 150  # ~20% of 500


class TestCorruption:
    def test_id_corruption_stays_plausible(self) -> None:
        payload = {"id": "INC-1001"}
        corrupt_value(payload, random.Random(1))
        assert payload["id"] == "INC-1002"

    def test_enum_corruption_picks_valid_sibling(self) -> None:
        payload = {"severity": "critical"}
        corrupt_value(payload, random.Random(1))
        assert payload["severity"] in {"high", "medium", "low"}

    def test_numeric_and_bool_corruption(self) -> None:
        numbers = {"record_count": 10}
        corrupt_value(numbers, random.Random(1))
        assert numbers["record_count"] == 11
        flags = {"healthy": True}
        corrupt_value(flags, random.Random(1))
        assert flags["healthy"] is False

    def test_detail_key_never_corrupted(self) -> None:
        payload = {"detail": "not found"}
        corrupt_value(payload, random.Random(1))
        assert payload["detail"] == "not found"
