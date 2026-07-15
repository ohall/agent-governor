"""The ops-remediation agent.

Sim mode (this milestone) is a deterministic scripted policy with a
configurable per-step success probability ``p`` — the engine behind the
p^n reliability benchmarks. Live mode (Anthropic reasoning) plugs into the
same LangGraph step loop and gateway tool interface later.
"""

from agent_governor.agent.models import SimConfig, StepRecord, TaskOutcome
from agent_governor.agent.runner import BENCHMARK_MANIFEST, DEFAULT_MANIFEST, AgentRunner

__all__ = [
    "BENCHMARK_MANIFEST",
    "DEFAULT_MANIFEST",
    "AgentRunner",
    "SimConfig",
    "StepRecord",
    "TaskOutcome",
]
