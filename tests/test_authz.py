from __future__ import annotations

import pytest

from agent_governor.warden.authz import AuthzPath, authorize
from agent_governor.warden.levels import AutonomyLevel
from agent_governor.warden.policy import ActionClass, ApprovalCategory, Classification, Risk


def cls(
    action_class: ActionClass,
    risk: Risk = Risk.MEDIUM,
    category: ApprovalCategory | None = None,
) -> Classification:
    return Classification(
        action_class=action_class, risk=risk, category=category, policy_version="t"
    )


READ = cls(ActionClass.READ_ONLY, Risk.LOW)
WRITE = cls(ActionClass.REVERSIBLE_WRITE, Risk.MEDIUM)
WRITE_HIGH = cls(ActionClass.REVERSIBLE_WRITE, Risk.HIGH)
GATED = cls(ActionClass.ALWAYS_APPROVE, Risk.HIGH, ApprovalCategory.DATA_DELETION)

MATRIX = [
    # (level, classification, expected path)
    (AutonomyLevel.L5_OBSERVER, READ, AuthzPath.AUTO),
    (AutonomyLevel.L5_OBSERVER, WRITE, AuthzPath.AUTO),
    (AutonomyLevel.L5_OBSERVER, WRITE_HIGH, AuthzPath.AUTO),
    (AutonomyLevel.L5_OBSERVER, GATED, AuthzPath.AUTO),  # demo-only level
    (AutonomyLevel.L4_APPROVER, READ, AuthzPath.AUTO),
    (AutonomyLevel.L4_APPROVER, WRITE, AuthzPath.AUTO),
    (AutonomyLevel.L4_APPROVER, WRITE_HIGH, AuthzPath.AUTO),
    (AutonomyLevel.L4_APPROVER, GATED, AuthzPath.APPROVAL_REQUIRED),
    (AutonomyLevel.L3_CONSULTANT, READ, AuthzPath.AUTO),
    (AutonomyLevel.L3_CONSULTANT, WRITE, AuthzPath.AUTO),
    (AutonomyLevel.L3_CONSULTANT, WRITE_HIGH, AuthzPath.APPROVAL_REQUIRED),
    (AutonomyLevel.L3_CONSULTANT, GATED, AuthzPath.APPROVAL_REQUIRED),
    (AutonomyLevel.L2_COLLABORATOR, READ, AuthzPath.AUTO),
    (AutonomyLevel.L2_COLLABORATOR, WRITE, AuthzPath.APPROVAL_REQUIRED),
    (AutonomyLevel.L2_COLLABORATOR, WRITE_HIGH, AuthzPath.APPROVAL_REQUIRED),
    (AutonomyLevel.L2_COLLABORATOR, GATED, AuthzPath.APPROVAL_REQUIRED),
    (AutonomyLevel.L1_OPERATOR, READ, AuthzPath.APPROVAL_REQUIRED),
    (AutonomyLevel.L1_OPERATOR, WRITE, AuthzPath.APPROVAL_REQUIRED),
    (AutonomyLevel.L1_OPERATOR, WRITE_HIGH, AuthzPath.APPROVAL_REQUIRED),
    (AutonomyLevel.L1_OPERATOR, GATED, AuthzPath.APPROVAL_REQUIRED),
]


@pytest.mark.parametrize(("level", "classification", "expected"), MATRIX)
def test_authorization_matrix(
    level: AutonomyLevel, classification: Classification, expected: AuthzPath
) -> None:
    decision = authorize(level, classification)
    assert decision.path is expected
    assert decision.level is level
    assert decision.reason  # every decision carries a human-readable reason


def test_demotion_changes_the_same_call() -> None:
    """The whole thesis in one test: identical action, different level, different path."""
    at_l4 = authorize(AutonomyLevel.L4_APPROVER, WRITE_HIGH)
    at_l3 = authorize(AutonomyLevel.L3_CONSULTANT, WRITE_HIGH)
    assert at_l4.path is AuthzPath.AUTO
    assert at_l3.path is AuthzPath.APPROVAL_REQUIRED
