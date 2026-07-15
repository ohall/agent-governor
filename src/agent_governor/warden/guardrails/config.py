"""Guardrail configuration loaded from policies/guardrails.yaml."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field


class RulesConfig(BaseModel):
    secret_patterns: list[str] = Field(default_factory=list)
    injection_markers: list[str] = Field(default_factory=list)
    denied_verbs: list[str] = Field(default_factory=list)


class ClassifierConfig(BaseModel):
    threshold: float = 0.65
    keywords: dict[str, float] = Field(default_factory=dict)


class JudgeConfig(BaseModel):
    threshold: float = 0.7
    signals: dict[str, float] = Field(default_factory=dict)


class GuardrailConfig(BaseModel):
    version: str
    latencies_ms: dict[str, tuple[float, float]] = Field(default_factory=dict)
    rules: RulesConfig = Field(default_factory=RulesConfig)
    classifier: ClassifierConfig = Field(default_factory=ClassifierConfig)
    judge: JudgeConfig = Field(default_factory=JudgeConfig)

    @classmethod
    def from_yaml(cls, path: str | Path) -> GuardrailConfig:
        raw: dict[str, Any] = yaml.safe_load(Path(path).read_text())
        return cls.model_validate(raw)

    def latency_range(self, check: str) -> tuple[float, float]:
        return self.latencies_ms.get(check, (0.0, 0.0))

    def without_latency(self) -> GuardrailConfig:
        """A copy with zero synthetic latency — for tests and fast paths."""
        return self.model_copy(update={"latencies_ms": {}})
