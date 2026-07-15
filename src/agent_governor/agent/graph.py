"""LangGraph wiring for the remediation loop.

The graph is a single execute-step node with a conditional self-edge — the
ReAct skeleton without ceremony. The step function is injected, so sim mode
(deterministic policy) and live mode (LLM policy, later) share one runtime.
"""

from __future__ import annotations

from typing import Any, cast

from langgraph.graph import END, StateGraph
from langgraph.graph.state import CompiledStateGraph

from agent_governor.agent.models import AgentState, StepFn


def _route(state: AgentState) -> str:
    return "end" if state["idx"] >= len(state["plan"]) else "continue"


def build_agent_graph(step_fn: StepFn) -> CompiledStateGraph[AgentState, Any, Any, Any]:
    graph: StateGraph[AgentState, Any, Any, Any] = StateGraph(AgentState)
    # cast: langgraph's add_node overloads don't unify with a plain Callable alias
    graph.add_node("execute_step", cast(Any, step_fn))
    graph.set_entry_point("execute_step")
    graph.add_conditional_edges("execute_step", _route, {"continue": "execute_step", "end": END})
    return graph.compile()
