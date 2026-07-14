from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from agent_governor.mockops.app import create_app
from agent_governor.mockops.config import MockOpsSettings


@pytest.fixture
def client() -> TestClient:
    """Fresh, fault-free mockops app per test."""
    return TestClient(create_app(MockOpsSettings(seed=42)))
