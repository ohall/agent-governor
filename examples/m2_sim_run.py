"""M2 demo: the sim agent remediates an incident through the gateway.

Runs fully in-process (no server, no keys):

    uv run python examples/m2_sim_run.py
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from agent_governor.agent.models import SimConfig, TaskOutcome
from agent_governor.agent.runner import BENCHMARK_MANIFEST, AgentRunner
from agent_governor.mockops.app import create_app
from agent_governor.mockops.config import MockOpsSettings
from agent_governor.warden.gateway import Gateway, ToolCall


def print_outcome(title: str, outcome: TaskOutcome) -> None:
    print(f"\n=== {title} ===")
    for i, r in enumerate(outcome.records, 1):
        mark = "OK " if r.correct else "ERR"
        mode = f"  [{r.error_mode}]" if r.error_mode else ""
        retry = f"  (retries={r.retries})" if r.retries else ""
        print(f"  {i}. [{mark}] {r.step:<18} -> {r.call.tool:<18} {r.status_code}{mode}{retry}")
    print(
        f"  outcome: success={outcome.success}  "
        f"steps_correct={outcome.all_steps_correct}  final_state={outcome.final_state}"
    )


def main() -> None:
    client = TestClient(create_app(MockOpsSettings(seed=42)))
    gateway = Gateway(client)

    # benchmark manifest (L5): this example demonstrates agent RELIABILITY;
    # examples/m3_governed_run.py demonstrates governance at L4.
    flawless = AgentRunner(gateway, SimConfig(p=1.0, seed=1, plan_length=6), BENCHMARK_MANIFEST)
    print_outcome("Flawless agent (p=1.0) on INC-1001", flawless.run("INC-1001"))

    client.post("/admin/reset")
    sloppy = AgentRunner(gateway, SimConfig(p=0.6, seed=9, plan_length=6), BENCHMARK_MANIFEST)
    print_outcome("Sloppy agent (p=0.6) on INC-1001", sloppy.run("INC-1001"))

    print(f"\ngateway action log: {len(gateway.action_log)} actions recorded")
    # not in the manifest -> the gateway refuses to even forward it
    blocked = gateway.execute(
        sloppy.session,
        ToolCall(tool="delete_records", params={"record_set": "customer_pii_archive"}),
    )
    print(f"out-of-manifest call: denied={blocked.denied}  reason={blocked.error!r}")


if __name__ == "__main__":
    main()
