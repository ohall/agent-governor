"""Guardrail data models."""

from __future__ import annotations

import json
from typing import Any

from pydantic import BaseModel, Field


class GuardrailInput(BaseModel):
    """What the checks see: the action, plus the agent's stated reasoning."""

    agent_id: str
    tool: str
    params: dict[str, Any] = Field(default_factory=dict)
    reasoning: str | None = None

    def scan_text(self) -> str:
        """Flat text every check scans: tool + params + reasoning."""
        parts = [self.tool, json.dumps(self.params, sort_keys=True)]
        if self.reasoning:
            parts.append(self.reasoning)
        return " ".join(parts)


class GuardrailVerdict(BaseModel):
    check: str
    passed: bool
    score: float = 0.0
    reasons: list[str] = Field(default_factory=list)
    latency_ms: float = 0.0


class GuardrailReport(BaseModel):
    mode: str  # sequential | parallel
    passed: bool
    verdicts: list[GuardrailVerdict]
    total_latency_ms: float

    @property
    def tripped(self) -> list[GuardrailVerdict]:
        return [v for v in self.verdicts if not v.passed]
