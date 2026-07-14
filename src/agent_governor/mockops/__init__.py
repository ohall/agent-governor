"""mockops: a deterministic mock ITSM/ops estate the agent operates on.

Every endpoint is fixture-backed and seeded, and every request passes through
a fault-injection layer, so agent benchmarks against this estate are
controllable and reproducible.
"""

from agent_governor.mockops.app import create_app

__all__ = ["create_app"]
