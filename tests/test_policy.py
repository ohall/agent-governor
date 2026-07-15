from __future__ import annotations

import pytest

from agent_governor.warden.policy import (
    ActionClass,
    ApprovalCategory,
    PolicyEngine,
    Risk,
)


@pytest.fixture(scope="module")
def engine() -> PolicyEngine:
    """The real shipped policy — tests double as policy validation."""
    return PolicyEngine.from_yaml("policies/classification.yaml")


class TestShippedPolicy:
    def test_version_is_stamped(self, engine: PolicyEngine) -> None:
        assert engine.version == "2026.07.14-1"
        assert engine.classify("get_incident", {}).policy_version == engine.version

    @pytest.mark.parametrize(
        "tool",
        ["list_incidents", "get_incident", "list_services", "get_service", "list_record_sets"],
    )
    def test_reads_are_read_only_low_risk(self, engine: PolicyEngine, tool: str) -> None:
        c = engine.classify(tool, {})
        assert c.action_class is ActionClass.READ_ONLY
        assert c.risk is Risk.LOW

    def test_tier1_deploy_is_production_deployment(self, engine: PolicyEngine) -> None:
        c = engine.classify("deploy_service", {"service_id": "SVC-001", "version": "5.0.0"})
        assert c.action_class is ActionClass.ALWAYS_APPROVE
        assert c.category is ApprovalCategory.PRODUCTION_DEPLOYMENT
        assert c.risk is Risk.HIGH

    def test_tier2_deploy_is_reversible_but_high_risk(self, engine: PolicyEngine) -> None:
        c = engine.classify("deploy_service", {"service_id": "SVC-004", "version": "16.0"})
        assert c.action_class is ActionClass.REVERSIBLE_WRITE
        assert c.risk is Risk.HIGH
        assert c.category is None

    def test_internal_notification_is_benign(self, engine: PolicyEngine) -> None:
        c = engine.classify("send_notification", {"recipient": "#inc-1001", "channel": "slack"})
        assert c.action_class is ActionClass.REVERSIBLE_WRITE
        assert c.risk is Risk.LOW

    def test_external_notification_is_gated(self, engine: PolicyEngine) -> None:
        c = engine.classify(
            "send_notification", {"recipient": "customer@example.com", "channel": "email"}
        )
        assert c.action_class is ActionClass.ALWAYS_APPROVE
        assert c.category is ApprovalCategory.EXTERNAL_COMMUNICATION

    def test_deletion_and_privileges_always_gated(self, engine: PolicyEngine) -> None:
        deletion = engine.classify("delete_records", {"record_set": "tmp_sessions"})
        assert deletion.category is ApprovalCategory.DATA_DELETION
        privileges = engine.classify("change_privileges", {"user_id": "USR-02", "role": "admin"})
        assert privileges.category is ApprovalCategory.PRIVILEGE_CHANGE
        for c in (deletion, privileges):
            assert c.action_class is ActionClass.ALWAYS_APPROVE
            assert c.risk is Risk.HIGH

    def test_unknown_tool_falls_to_defaults(self, engine: PolicyEngine) -> None:
        c = engine.classify("mystery_tool", {})
        assert c.action_class is ActionClass.REVERSIBLE_WRITE
        assert c.risk is Risk.MEDIUM
        assert c.matched_rule is None


class TestRuleMatching:
    def test_first_match_wins(self) -> None:
        engine = PolicyEngine.from_dict(
            {
                "version": "t1",
                "rules": [
                    {"tool": "x", "action_class": "read_only", "risk": "low"},
                    {"tool": "x", "action_class": "always_approve", "risk": "high"},
                ],
            }
        )
        assert engine.classify("x", {}).action_class is ActionClass.READ_ONLY

    def test_equals_condition(self) -> None:
        engine = PolicyEngine.from_dict(
            {
                "version": "t2",
                "rules": [
                    {
                        "tool": "x",
                        "when": {"param": "target", "equals": "prod"},
                        "action_class": "always_approve",
                        "risk": "high",
                    },
                    {"tool": "x", "action_class": "read_only", "risk": "low"},
                ],
            }
        )
        assert engine.classify("x", {"target": "prod"}).action_class is ActionClass.ALWAYS_APPROVE
        assert engine.classify("x", {"target": "dev"}).action_class is ActionClass.READ_ONLY
        assert engine.classify("x", {}).action_class is ActionClass.READ_ONLY

    def test_missing_param_never_matches_condition(self) -> None:
        engine = PolicyEngine.from_dict(
            {
                "version": "t3",
                "rules": [
                    {
                        "tool": "x",
                        "when": {"param": "id", "regex": "^SVC-"},
                        "action_class": "always_approve",
                        "risk": "high",
                    },
                ],
                "defaults": {"action_class": "read_only", "risk": "low"},
            }
        )
        assert engine.classify("x", {}).action_class is ActionClass.READ_ONLY
