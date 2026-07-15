"""The L1-L5 autonomy spectrum, adapted from Feng/McDonald/Zhang's
"Levels of Autonomy for AI Agents".

Autonomy is a deployment property of the agent, decoupled from model
capability, and it is MUTABLE: the budget loop (M5) demotes and promotes
agents along this ladder at runtime.
"""

from __future__ import annotations

from enum import IntEnum


class AutonomyLevel(IntEnum):
    """Higher level = more autonomous. An agent never exceeds its registered maximum."""

    L1_OPERATOR = 1
    """Agent suggests; a human executes every action."""

    L2_COLLABORATOR = 2
    """Agent executes only pre-approved read-only actions."""

    L3_CONSULTANT = 3
    """Agent executes low/medium-risk actions; high-risk actions queue for human review."""

    L4_APPROVER = 4
    """Agent executes everything except the five always-approve categories."""

    L5_OBSERVER = 5
    """Fully autonomous; humans can only monitor or kill. Demo-only, never a default."""
