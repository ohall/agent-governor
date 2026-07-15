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

DEFAULT_DB = ".governor/approvals.sqlite3"

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


if __name__ == "__main__":
    app()
