"""The three guardrail checks.

Each check simulates a calibrated latency (seeded jitter within the
configured range — see policies/guardrails.yaml for the honesty note), then
evaluates synchronously. Evaluation logic is pure and separately testable.
"""

from __future__ import annotations

import asyncio
import random
import re
import time
from typing import TYPE_CHECKING

from agent_governor.warden.guardrails.models import GuardrailInput, GuardrailVerdict

if TYPE_CHECKING:
    from agent_governor.warden.guardrails.config import GuardrailConfig

_PII_TARGET = re.compile(r"(?i)(pii|customer|audit_log|gdpr|personal)")
_EXTERNAL_RECIPIENT = re.compile(r"[^@\s]+@[^@\s]+\.[^@\s]+")
_MASS_SCOPE = re.compile(r"(?i)(\ball\b|\*|everything)")


class BaseCheck:
    name: str = "base"

    def __init__(self, config: GuardrailConfig, rng: random.Random) -> None:
        self.config = config
        self._rng = rng

    async def run(self, action: GuardrailInput) -> GuardrailVerdict:
        start = time.perf_counter()
        low, high = self.config.latency_range(self.name)
        if high > 0:
            await asyncio.sleep(self._rng.uniform(low, high) / 1000.0)
        passed, score, reasons = self.evaluate(action)
        return GuardrailVerdict(
            check=self.name,
            passed=passed,
            score=round(score, 3),
            reasons=reasons,
            latency_ms=round((time.perf_counter() - start) * 1000.0, 2),
        )

    def evaluate(self, action: GuardrailInput) -> tuple[bool, float, list[str]]:
        raise NotImplementedError


class RuleCheck(BaseCheck):
    """Regex filter: secrets, prompt-injection markers, denied verbs."""

    name = "rules"

    def evaluate(self, action: GuardrailInput) -> tuple[bool, float, list[str]]:
        text = action.scan_text()
        reasons: list[str] = []
        rule_sets = {
            "secret": self.config.rules.secret_patterns,
            "injection": self.config.rules.injection_markers,
            "denied-verb": self.config.rules.denied_verbs,
        }
        for label, patterns in rule_sets.items():
            for pattern in patterns:
                if re.search(pattern, text):
                    reasons.append(f"{label} pattern matched: {pattern!r}")
        return (not reasons, 1.0 if reasons else 0.0, reasons)


class ClassifierCheck(BaseCheck):
    """Keyword-scoring stand-in for a lightweight ML risk classifier."""

    name = "classifier"

    def evaluate(self, action: GuardrailInput) -> tuple[bool, float, list[str]]:
        text = action.scan_text().lower()
        hits = {
            word: weight
            for word, weight in self.config.classifier.keywords.items()
            if re.search(rf"\b{re.escape(word)}\b", text)
        }
        score = min(1.0, sum(hits.values()))
        tripped = score >= self.config.classifier.threshold
        reasons = (
            [f"risk score {score:.2f} >= {self.config.classifier.threshold} ({hits})"]
            if tripped
            else []
        )
        return (not tripped, score, reasons)


class HeuristicJudgeCheck(BaseCheck):
    """Judge over the action + reasoning. This heuristic fallback runs with
    zero API keys; an LLM-as-judge implements the same interface (seam for
    live mode)."""

    name = "judge"

    def evaluate(self, action: GuardrailInput) -> tuple[bool, float, list[str]]:
        text = action.scan_text()
        signals = self.config.judge.signals
        score = 0.0
        reasons: list[str] = []

        if action.tool == "delete_records" and _PII_TARGET.search(text):
            score += signals.get("pii_target", 0.0)
            reasons.append("deletion targets a PII-looking dataset")
        if action.tool == "send_notification" and _EXTERNAL_RECIPIENT.search(text):
            score += signals.get("external_recipient", 0.0)
            reasons.append("notification to an external address")
        if action.tool == "change_privileges" and "admin" in text.lower():
            score += signals.get("admin_grant", 0.0)
            reasons.append("grants an admin-level role")
        if _MASS_SCOPE.search(text):
            score += signals.get("mass_scope", 0.0)
            reasons.append("mass/wildcard scope in action or reasoning")

        score = min(1.0, score)
        tripped = score >= self.config.judge.threshold
        return (not tripped, score, reasons if tripped else [])
