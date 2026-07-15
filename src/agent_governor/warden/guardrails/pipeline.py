"""Sequential vs parallel guardrail orchestration.

Sequential pays the SUM of check latencies (optionally short-circuiting on
the first trip); parallel pays roughly the MAX. That difference — governance
overhead measured in milliseconds — is Experiment 2.
"""

from __future__ import annotations

import asyncio
import random
import time
import uuid
from typing import TYPE_CHECKING, Literal

from agent_governor.warden.guardrails.checks import (
    BaseCheck,
    ClassifierCheck,
    HeuristicJudgeCheck,
    RuleCheck,
)
from agent_governor.warden.guardrails.models import GuardrailInput, GuardrailReport
from agent_governor.warden.guardrails.state import InMemorySharedState, SharedState

if TYPE_CHECKING:
    from agent_governor.warden.guardrails.config import GuardrailConfig

PipelineMode = Literal["sequential", "parallel"]


class GuardrailPipeline:
    def __init__(
        self,
        checks: list[BaseCheck],
        mode: PipelineMode,
        state: SharedState,
        short_circuit: bool = True,
    ) -> None:
        self.checks = checks
        self.mode = mode
        self.state = state
        self.short_circuit = short_circuit

    async def run(self, action: GuardrailInput, action_id: str | None = None) -> GuardrailReport:
        action_id = action_id or f"GRD-{uuid.uuid4().hex[:10]}"
        start = time.perf_counter()

        if self.mode == "sequential":
            verdicts = []
            for check in self.checks:
                verdict = await check.run(action)
                verdicts.append(verdict)
                await self.state.record_verdict(action_id, verdict)
                if not verdict.passed:
                    await self.state.record_trip(action.agent_id, verdict.check)
                    if self.short_circuit:
                        break
        else:
            verdicts = list(await asyncio.gather(*(c.run(action) for c in self.checks)))
            for verdict in verdicts:
                await self.state.record_verdict(action_id, verdict)
                if not verdict.passed:
                    await self.state.record_trip(action.agent_id, verdict.check)

        return GuardrailReport(
            mode=self.mode,
            passed=all(v.passed for v in verdicts),
            verdicts=verdicts,
            total_latency_ms=round((time.perf_counter() - start) * 1000.0, 2),
        )

    def run_sync(self, action: GuardrailInput, action_id: str | None = None) -> GuardrailReport:
        """Convenience for sync callers (the gateway's execute path)."""
        return asyncio.run(self.run(action, action_id))


def build_pipeline(
    config: GuardrailConfig,
    mode: PipelineMode,
    state: SharedState | None = None,
    seed: int = 42,
    short_circuit: bool = True,
) -> GuardrailPipeline:
    rng = random.Random(seed)
    checks: list[BaseCheck] = [
        RuleCheck(config, rng),
        ClassifierCheck(config, rng),
        HeuristicJudgeCheck(config, rng),
    ]
    return GuardrailPipeline(
        checks=checks,
        mode=mode,
        state=state or InMemorySharedState(),
        short_circuit=short_circuit,
    )
