"""The warden CLI: human-side controls for the governance gateway.

    warden approvals list [--status pending]
    warden approvals approve APR-xxxx --by maria.chen
    warden approvals deny APR-xxxx --by maria.chen --reason "..."

Point --db at the same SQLite file the gateway uses (cross-process approval
is the point: the agent blocks while a human decides from another terminal).
"""

from __future__ import annotations

import typer

from agent_governor.warden.approvals import ApprovalQueue, ApprovalStatus
from agent_governor.warden.audit import AuditStore, render_audit

DEFAULT_DB = ".governor/approvals.sqlite3"
DEFAULT_AUDIT_DB = ".governor/audit.sqlite3"

app = typer.Typer(no_args_is_help=True, help="Governor: human-side governance controls.")
approvals_app = typer.Typer(no_args_is_help=True, help="Manage the pending-approvals queue.")
app.add_typer(approvals_app, name="approvals")


def _queue(db: str) -> ApprovalQueue:
    return ApprovalQueue(db)


@approvals_app.command("list")
def list_approvals(
    status: str | None = typer.Option(None, help="pending | approved | denied | expired"),
    db: str = typer.Option(DEFAULT_DB, help="Path to the approvals SQLite database."),
) -> None:
    """List approval requests (newest last)."""
    queue = _queue(db)
    requests = queue.list(ApprovalStatus(status) if status else None)
    if not requests:
        typer.echo("no approval requests")
        raise typer.Exit()
    for r in requests:
        decided = f"  decided_by={r.decided_by}" if r.decided_by else ""
        typer.echo(
            f"{r.id}  [{r.status.value:^8}]  {r.agent_id}  {r.tool}"
            f"  category={r.category or '-'}  params={r.params}{decided}"
        )


@approvals_app.command("approve")
def approve(
    approval_id: str,
    by: str = typer.Option(..., help="Name of the approving human (audited)."),
    db: str = typer.Option(DEFAULT_DB, help="Path to the approvals SQLite database."),
) -> None:
    """Approve a pending request; the waiting agent proceeds."""
    request = _queue(db).decide(approval_id, approve=True, decided_by=by)
    typer.echo(f"approved {request.id}: {request.tool} {request.params} (by {by})")


@approvals_app.command("deny")
def deny(
    approval_id: str,
    by: str = typer.Option(..., help="Name of the denying human (audited)."),
    reason: str = typer.Option("denied by operator", help="Recorded denial reason."),
    db: str = typer.Option(DEFAULT_DB, help="Path to the approvals SQLite database."),
) -> None:
    """Deny a pending request; the waiting agent receives the denial."""
    request = _queue(db).decide(approval_id, approve=False, decided_by=by, reason=reason)
    typer.echo(f"denied {request.id}: {request.tool} (by {by}: {reason})")


@app.command("audit")
def audit(
    action_id: str,
    db: str = typer.Option(DEFAULT_AUDIT_DB, help="Path to the audit SQLite database."),
) -> None:
    """Reconstruct the full authorization story for one action.

    This is the auditor's question, answered from the trace store alone:
    which agent, at which autonomy level, under which policy version, via
    which authorization path, approved by whom.
    """
    store = AuditStore(db)
    action = store.get_action(action_id)
    if action is None:
        typer.echo(f"action {action_id!r} not found in {db}")
        raise typer.Exit(code=1)
    typer.echo(render_audit(action, store.spans_for(action_id)))


@app.command("actions")
def actions(
    agent: str | None = typer.Option(None, help="Filter by agent id."),
    limit: int = typer.Option(20, help="Max rows."),
    db: str = typer.Option(DEFAULT_AUDIT_DB, help="Path to the audit SQLite database."),
) -> None:
    """List recent governed actions (newest first)."""
    store = AuditStore(db)
    rows = store.list_actions(agent_id=agent, limit=limit)
    if not rows:
        typer.echo("no recorded actions")
        raise typer.Exit()
    for a in rows:
        outcome = "DENIED" if a.denied else ("PENDING" if a.path == "queued" else a.status_code)
        typer.echo(
            f"{a.action_id}  {a.timestamp.strftime('%H:%M:%S')}  L{a.level or '?'}"
            f"  {a.agent_id:<20} {a.tool:<18} path={a.path or '-':<16} {outcome}"
        )


if __name__ == "__main__":
    app()
