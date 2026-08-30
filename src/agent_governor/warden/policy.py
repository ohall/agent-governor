"""The three-tier trust model: declarative action classification from a
versioned YAML policy.

Every action is classified by blast radius (impact x reversibility) into
read_only / reversible_write / always_approve. Rules are evaluated
first-match-wins and may condition on call parameters, so the same tool can
classify differently by target (deploying a tier-1 service is a production
deployment; deploying a sandbox service is a reversible write). The policy
version is stamped into every decision.
"""

from __future__ import annotations

import re
from enum import StrEnum
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field


class ActionClass(StrEnum):
    """The three trust tiers, ordered by blast radius."""

    READ_ONLY = "read_only"
    REVERSIBLE_WRITE = "reversible_write"
    ALWAYS_APPROVE = "always_approve"


class Risk(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class ApprovalCategory(StrEnum):
    """The five always-approve categories: hard human gates at L4 and below."""

    PRODUCTION_DEPLOYMENT = "production_deployment"
    EXTERNAL_COMMUNICATION = "external_communication"
    FINANCIAL_TRANSACTION = "financial_transaction"
    DATA_DELETION = "data_deletion"
    PRIVILEGE_CHANGE = "privilege_change"


class Condition(BaseModel):
    """Single-parameter predicate: equals / in / regex."""

    model_config = ConfigDict(populate_by_name=True)

    param: str
    equals: Any | None = None
    in_: list[Any] | None = Field(default=None, alias="in")
    regex: str | None = None

    def matches(self, params: dict[str, Any]) -> bool:
        value = params.get(self.param)
        if value is None:
            return False
        if self.equals is not None:
            return bool(value == self.equals)
        if self.in_ is not None:
            return value in self.in_
        if self.regex is not None:
            return isinstance(value, str) and re.match(self.regex, value) is not None
        return False


class PolicyRule(BaseModel):
    tool: str
    when: Condition | None = None
    action_class: ActionClass
    risk: Risk = Risk.MEDIUM
    category: ApprovalCategory | None = None

    def matches(self, tool: str, params: dict[str, Any]) -> bool:
        if tool != self.tool:
            return False
        return self.when is None or self.when.matches(params)


class Classification(BaseModel):
    """The policy's verdict on one action, ready for the audit record."""

    action_class: ActionClass
    risk: Risk
    category: ApprovalCategory | None = None
    policy_version: str
    matched_rule: int | None = None  # index into the rule list; None = defaults


class PolicyEngine:
    def __init__(self, version: str, rules: list[PolicyRule], defaults: PolicyRule) -> None:
        self.version = version
        self.rules = rules
        self.defaults = defaults

    @classmethod
    def from_yaml(cls, path: str | Path) -> PolicyEngine:
        raw = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
        return cls.from_dict(raw)

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> PolicyEngine:
        version = str(raw["version"])
        defaults_raw = raw.get("defaults", {})
        defaults = PolicyRule(
            tool="*",
            action_class=ActionClass(defaults_raw.get("action_class", "reversible_write")),
            risk=Risk(defaults_raw.get("risk", "medium")),
        )
        rules = [PolicyRule.model_validate(r) for r in raw.get("rules", [])]
        return cls(version=version, rules=rules, defaults=defaults)

    def classify(self, tool: str, params: dict[str, Any]) -> Classification:
        for idx, rule in enumerate(self.rules):
            if rule.matches(tool, params):
                return Classification(
                    action_class=rule.action_class,
                    risk=rule.risk,
                    category=rule.category,
                    policy_version=self.version,
                    matched_rule=idx,
                )
        return Classification(
            action_class=self.defaults.action_class,
            risk=self.defaults.risk,
            category=None,
            policy_version=self.version,
            matched_rule=None,
        )
