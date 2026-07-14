"""Settings for the mockops service (env-driven, MOCKOPS_ prefix)."""

from __future__ import annotations

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from agent_governor.mockops.faults import FaultConfig


class MockOpsSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="MOCKOPS_",
        env_nested_delimiter="__",
        env_file=".env",
        extra="ignore",
    )

    seed: int = 42
    host: str = "127.0.0.1"
    port: int = 8010
    faults: FaultConfig = Field(default_factory=FaultConfig)
