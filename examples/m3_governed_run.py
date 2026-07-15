"""M3 demo: tiered authorization and the human approval gate.

Runs fully in-process (no server, no keys):

    uv run python examples/m3_governed_run.py

For the cross-process version, pass a file-backed queue (see WARDEN_APPROVALS_DB)
and approve from another terminal with:

    uv run warden approvals list --db .governor/approvals.sqlite3
    uv run warden approvals approve APR-xxxx --by maria.chen --db .governor/approvals.sqlite3
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from agent_governor.mockops.app import create_app
from agent_governor.mockops.config import MockOpsSettings
from agent_governor.warden.gateway import AgentManifest, Gateway, ToolCall, ToolResult
from agent_governor.warden.levels import AutonomyLevel


def show(label: str, result: ToolResult) -> None:
    authz = result.authz
    detail = (
        f"path={authz.path} class={authz.action_class} category={authz.category}"
        f" policy={authz.policy_version} level=L{authz.level}"
        if authz
        else ""
    )
    status = "PENDING" if result.pending else ("DENIED" if result.denied else result.status_code)
    print(f"  {label:<38} -> {status!s:<8} {detail}")
    if result.error:
        print(f"      {result.error}")


def main() -> None:
    client = TestClient(create_app(MockOpsSettings(seed=42)))
    gateway = Gateway(client)

    manifest = AgentManifest(
        agent_id="ops-remediator-01",
        owner="maria.chen",
        purpose="incident remediation",
        max_autonomy_level=AutonomyLevel.L4_APPROVER,
        allowed_tools=(
            "get_incident",
            "update_incident",
            "deploy_service",
            "send_notification",
            "delete_records",
        ),
    )
    session = gateway.register(manifest)
    print(f"registered {manifest.agent_id} (owner: {manifest.owner}) at L4\n")

    print("L4: benign operations execute autonomously")
    show(
        "read incident INC-1004",
        gateway.execute(session, ToolCall(tool="get_incident", params={"incident_id": "INC-1004"})),
    )
    show(
        "deploy tier-2 service SVC-004",
        gateway.execute(
            session,
            ToolCall(tool="deploy_service", params={"service_id": "SVC-004", "version": "15.7"}),
        ),
    )
    show(
        "notify internal channel",
        gateway.execute(
            session,
            ToolCall(
                tool="send_notification",
                params={"channel": "slack", "recipient": "#inc-1004", "message": "mitigating"},
            ),
        ),
    )

    print("\nL4: the five always-approve categories hit the human gate")
    deletion = gateway.execute(
        session, ToolCall(tool="delete_records", params={"record_set": "customer_pii_archive"})
    )
    show("delete customer_pii_archive", deletion)
    deploy = gateway.execute(
        session,
        ToolCall(tool="deploy_service", params={"service_id": "SVC-001", "version": "4.13.0"}),
    )
    show("deploy TIER-1 service SVC-001", deploy)

    print("\nhuman decisions (normally via `warden approvals` CLI):")
    assert deletion.approval_id is not None
    assert deploy.approval_id is not None
    gateway.queue.decide(deletion.approval_id, approve=True, decided_by="maria.chen")
    print("  maria.chen APPROVED the deletion")
    gateway.queue.decide(
        deploy.approval_id, approve=False, decided_by="priya.nair", reason="change freeze"
    )
    print("  priya.nair DENIED the tier-1 deploy (change freeze)")

    print("\nthe agent resumes:")
    show("deletion after approval", gateway.await_approval(session, deletion.approval_id, 0.01))
    show("tier-1 deploy after denial", gateway.await_approval(session, deploy.approval_id, 0.01))

    print("\ndemotion: same call, lower level, different path")
    call = ToolCall(tool="deploy_service", params={"service_id": "SVC-004", "version": "15.8"})
    show("deploy tier-2 at L4", gateway.execute(session, call))
    gateway.set_level(manifest.agent_id, AutonomyLevel.L3_CONSULTANT)
    show("deploy tier-2 at L3 (demoted)", gateway.execute(session, call))

    print(f"\naction log: {len(gateway.action_log)} governed actions recorded")


if __name__ == "__main__":
    main()
