"""M5 demo: the autonomy budget — earn it, burn it, lose everything.

Runs fully in-process (no server, no keys):

    uv run python examples/m5_budget_run.py
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from agent_governor.mockops.app import create_app
from agent_governor.mockops.config import MockOpsSettings
from agent_governor.warden.config import WardenSettings
from agent_governor.warden.gateway import AgentManifest, Gateway, ToolCall
from agent_governor.warden.guardrails.config import GuardrailConfig
from agent_governor.warden.guardrails.pipeline import build_pipeline
from agent_governor.warden.levels import AutonomyLevel

AGENT = "ops-remediator-01"


def main() -> None:
    client = TestClient(create_app(MockOpsSettings(seed=42)))
    gateway = Gateway(
        client,
        settings=WardenSettings(
            budget_enabled=True,
            budget_profile="policies/budget/demo.yaml",
            guardrails_mode="sequential",
        ),
    )
    # zero-latency guardrails so the demo is snappy; verdict logic unchanged
    gateway.guardrails = build_pipeline(
        GuardrailConfig.from_yaml("policies/guardrails.yaml").without_latency(), "sequential"
    )
    assert gateway.budget is not None
    events: list[str] = []
    gateway.budget._notify = events.append  # capture owner notifications

    profile = gateway.budget.profile
    print(f"⚠ active budget profile: {profile.name} — {profile.note}\n")

    session = gateway.register(
        AgentManifest(
            agent_id=AGENT,
            owner="maria.chen",
            purpose="incident remediation",
            max_autonomy_level=AutonomyLevel.L4_APPROVER,
            allowed_tools=("get_incident", "deploy_service", "delete_records"),
        )
    )

    def level() -> str:
        return f"L{int(gateway.current_level(AGENT))}"

    def read(incident: str, note: str) -> None:
        gateway.execute(
            session,
            ToolCall(tool="get_incident", params={"incident_id": incident}, reasoning=note),
        )

    print(f"registered at {level()} (owner maria.chen)")

    print("\nphase 1 — healthy operation:")
    for i in range(5):
        read("INC-1001", f"routine triage read #{i + 1}")
    print(f"  5 clean actions -> still {level()}")

    print("\nphase 2 — the agent starts failing (hallucinated incident ids):")
    for i in range(11):
        before = level()
        read("INC-9999", f"investigating INC-9999 (hallucinated) #{i + 1}")
        after = level()
        if before != after:
            print(f"  action {i + 6}: DEMOTED {before} -> {after}")
    print(f"  current level: {level()}")

    print("\nphase 3 — demoted agent reaches for a PII deletion:")
    result = gateway.execute(
        session,
        ToolCall(
            tool="delete_records",
            params={"record_set": "customer_pii_archive"},
            reasoning="cleanup",
        ),
    )
    print(f"  -> denied={result.denied}: {result.error}")

    print("\nphase 4 — kill-switch is total:")
    read_result = gateway.execute(
        session,
        ToolCall(tool="get_incident", params={"incident_id": "INC-1001"}, reasoning="read"),
    )
    print(f"  even a read: denied={read_result.denied} ({read_result.error})")

    print("\nowner notifications sent:")
    for event in events:
        print(f"  {event}")

    snapshot = gateway.budget.last_snapshot(AGENT)
    if snapshot:
        print("\nfinal SLO snapshot:")
        for dim in (
            "task_completion",
            "tool_call_success",
            "recovery_rate",
            "p99_latency_ms",
            "guardrail_trip_rate",
            "trace_grounded",
        ):
            value = getattr(snapshot, dim)
            print(f"  {dim:<20} {'-' if value is None else f'{value:g}'}")
        print(f"  breaches: {snapshot.breaches}")


if __name__ == "__main__":
    main()
