"""Run the mockops service: `uv run mockops` or `python -m agent_governor.mockops`."""

from __future__ import annotations

import uvicorn

from agent_governor.mockops.app import create_app
from agent_governor.mockops.config import MockOpsSettings


def main() -> None:
    settings = MockOpsSettings()
    uvicorn.run(create_app(settings), host=settings.host, port=settings.port)


if __name__ == "__main__":
    main()
