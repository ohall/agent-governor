"""Tiered authorization: the L1-L5 x classification decision matrix.

The matrix is the enforcement core of graduated autonomy:

    level            read_only   reversible   reversible   always_approve
                                 (low/med)    (high risk)
    L5 observer      auto        auto         auto         auto (demo-only!)
    L4 approver      auto        auto         auto         approval
    L3 consultant    auto        auto         approval     approval
    L2 collaborator  auto        approval     approval     approval
    L1 operator      approval    approval     approval     approval

Demotion moves an agent down this table at runtime — the same call that
executed autonomously an hour ago now waits for a human.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel

from agent_governor.warden.levels import AutonomyLevel
from agent_governor.warden.policy import ActionClass, Classification, Risk


class AuthzPath(StrEnum):
    AUTO = "auto"
    APPROVAL_REQUIRED = "approval_required"


class AuthzDecision(BaseModel):
    path: AuthzPath
    level: AutonomyLevel
    reason: str


def authorize(level: AutonomyLevel, classification: Classification) -> AuthzDecision:
    """Pure function of (current level, classification) — no hidden state."""
    cls_, risk = classification.action_class, classification.risk

    if level is AutonomyLevel.L5_OBSERVER:
        return _auto(level, "L5 observer executes everything autonomously (demo-only level)")

    if cls_ is ActionClass.ALWAYS_APPROVE:
        category = classification.category.value if classification.category else "uncategorized"
        return _approval(level, f"always-approve category {category!r} is a hard human gate")

    if level is AutonomyLevel.L4_APPROVER:
        return _auto(level, "L4 executes all non-always-approve actions")

    if level is AutonomyLevel.L3_CONSULTANT:
        if risk is Risk.HIGH:
            return _approval(level, "L3 queues high-risk actions for human review")
        return _auto(level, "L3 executes low/medium-risk actions")

    if level is AutonomyLevel.L2_COLLABORATOR:
        if cls_ is ActionClass.READ_ONLY:
            return _auto(level, "L2 executes pre-approved read-only actions")
        return _approval(level, "L2 requires approval for any write")

    return _approval(level, "L1 operator: agent suggests, human executes")


def _auto(level: AutonomyLevel, reason: str) -> AuthzDecision:
    return AuthzDecision(path=AuthzPath.AUTO, level=level, reason=reason)


def _approval(level: AutonomyLevel, reason: str) -> AuthzDecision:
    return AuthzDecision(path=AuthzPath.APPROVAL_REQUIRED, level=level, reason=reason)
