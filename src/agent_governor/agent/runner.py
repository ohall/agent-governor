"""The agent runner: wires policy + executor into the graph and scores outcomes."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from agent_governor.agent.graph import build_agent_graph
from agent_governor.agent.models import (
    AgentState,
    SimConfig,
    StepKind,
    StepRecord,
    TaskOutcome,
)
from agent_governor.agent.policy import SimPolicy, build_plan
from agent_governor.agent.tools import ToolExecutor
from agent_governor.warden.gateway import (
    AgentManifest,
    Gateway,
    ToolCall,
)
from agent_governor.warden.levels import AutonomyLevel

if TYPE_CHECKING:
    from collections.abc import Callable

DEFAULT_MANIFEST = AgentManifest(
    agent_id="ops-remediator-01",
    owner="maria.chen",
    purpose="Multi-step incident remediation on the mock ops estate.",
    max_autonomy_level=AutonomyLevel.L4_APPROVER,
    allowed_tools=(
        "list_incidents",
        "get_incident",
        "update_incident",
        "resolve_incident",
        "list_services",
        "get_service",
        "deploy_service",
        "send_notification",
        "list_record_sets",
    ),
)
# Note: delete_records and change_privileges are deliberately absent — the
# manifest is the OUTER boundary; even a fully-trusted agent can't reach them.

BENCHMARK_MANIFEST = DEFAULT_MANIFEST.model_copy(
    update={
        "agent_id": "ops-remediator-bench",
        "max_autonomy_level": AutonomyLevel.L5_OBSERVER,
    }
)
# L5 exists here ONLY because reliability benchmarks (p^n) measure the agent,
# not governance friction: approval pauses would corrupt the measurement.
# L5 is never a production default — see AutonomyLevel.L5_OBSERVER.


class AgentRunner:
    """Runs remediation tasks in sim mode through the governance gateway."""

    def __init__(
        self,
        gateway: Gateway,
        config: SimConfig,
        manifest: AgentManifest = DEFAULT_MANIFEST,
    ) -> None:
        self.config = config
        self.session = gateway.register(manifest)
        self._gateway = gateway
        self._executor = ToolExecutor(gateway, self.session, config.max_retries)
        self._policy = SimPolicy(config)
        self._graph = build_agent_graph(self._step)

    def _step(self, state: AgentState) -> dict[str, Any]:
        kind = state["plan"][state["idx"]]
        memory = dict(state["memory"])
        call, error_mode = self._policy.emit(kind, memory)
        call = call.model_copy(
            update={
                "reasoning": f"step {state['idx'] + 1}/{len(state['plan'])} "
                f"{kind.value}: remediation of {memory['incident_id']}"
            }
        )
        exec_result = self._executor.execute(call)
        result = exec_result.result
        if self._gateway.budget is not None and (exec_result.retries > 0 or result.fault):
            self._gateway.budget.record_recovery(
                self.session.manifest.agent_id, exec_result.recovered or result.ok
            )

        if error_mode is None and result.ok and result.data is not None:
            self._update_memory(kind, memory, call, result.data)

        record = StepRecord(
            step=kind,
            call=call,
            error_mode=error_mode,
            status_code=result.status_code,
            ok=result.ok,
            fault=result.fault,
            retries=exec_result.retries,
            recovered=exec_result.recovered,
        )
        return {
            "idx": state["idx"] + 1,
            "memory": memory,
            "records": [*state["records"], record],
        }

    @staticmethod
    def _update_memory(kind: StepKind, memory: dict[str, Any], call: ToolCall, data: Any) -> None:
        if kind is StepKind.GET_INCIDENT and isinstance(data, dict):
            memory["incident"] = data
        elif kind is StepKind.GET_SERVICE and isinstance(data, dict):
            memory["service"] = data
        elif kind is StepKind.REMEDIATE:
            memory["remediation"] = "deploy" if call.tool == "deploy_service" else "notify"

    def run(self, incident_id: str) -> TaskOutcome:
        plan = build_plan(self.config.plan_length)
        initial: AgentState = {
            "plan": plan,
            "idx": 0,
            "memory": {"incident_id": incident_id},
            "records": [],
        }
        final = self._graph.invoke(initial, config={"recursion_limit": len(plan) * 2 + 10})
        records: list[StepRecord] = final["records"]
        all_correct = all(r.correct for r in records)
        final_state = self._ground_truth_state(incident_id)
        if self._gateway.budget is not None:
            self._gateway.budget.record_task(
                self.session.manifest.agent_id,
                all_correct and final_state == "resolved",
            )
        return TaskOutcome(
            incident_id=incident_id,
            plan_length=len(plan),
            success=all_correct and final_state == "resolved",
            all_steps_correct=all_correct,
            final_state=final_state,
            records=records,
        )

    def run_batch(
        self,
        incident_id: str,
        runs: int,
        reset_estate: Callable[[], None],
    ) -> list[TaskOutcome]:
        """Run the same task repeatedly against a freshly-reset estate each time."""
        outcomes: list[TaskOutcome] = []
        for _ in range(runs):
            reset_estate()
            outcomes.append(self.run(incident_id))
        return outcomes

    def _ground_truth_state(self, incident_id: str) -> str | None:
        """Read the incident's final state for scoring (not part of the task plan)."""
        result = self._gateway.execute(
            self.session, ToolCall(tool="get_incident", params={"incident_id": incident_id})
        )
        if result.ok and isinstance(result.data, dict):
            state = result.data.get("state")
            return state if isinstance(state, str) else None
        return None
