"""Settings for the warden gateway (env-driven, WARDEN_ prefix)."""

from __future__ import annotations

import secrets
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class WardenSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="WARDEN_",
        env_file=".env",
        extra="ignore",
    )

    jwt_secret: str = Field(
        default_factory=lambda: secrets.token_urlsafe(32),
        description="HS256 signing secret. Random per-process unless pinned via env "
        "(pin it when the CLI and gateway run in separate processes).",
    )
    token_ttl_s: int = Field(default=600, description="JWT lifetime: 10 minutes.")
    approval_timeout_s: int = Field(
        default=1800, description="Pending approvals auto-deny after 30 minutes."
    )
    approvals_db: str = Field(
        default=":memory:",
        description="SQLite path for the approval queue. Use a real file for "
        "cross-process approval (demo: .governor/approvals.sqlite3).",
    )
    policy_path: str = "policies/classification.yaml"
    audit_db: str = Field(
        default=":memory:",
        description="SQLite path for the audit/trace store. Use a real file so "
        "`warden audit` can read it from another process.",
    )
    guardrails_mode: Literal["off", "sequential", "parallel"] = Field(
        default="off",
        description="Guardrail orchestration for the gateway's auto path. Off by "
        "default because check latencies are calibrated synthetics; the demo and "
        "benchmarks enable it explicitly.",
    )
    guardrails_path: str = "policies/guardrails.yaml"
    guardrails_seed: int = 42
    budget_enabled: bool = Field(
        default=False,
        description="Enable the autonomy-budget loop (synchronous per-action "
        "evaluation). Off by default; the demo and Experiment 3 enable it.",
    )
    budget_profile: str = "policies/budget/realistic.yaml"
