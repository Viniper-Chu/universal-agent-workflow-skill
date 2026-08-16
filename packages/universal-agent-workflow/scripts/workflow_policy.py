#!/usr/bin/env python3
"""Load and validate the executable workflow policy shipped with the Skill."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


MANDATORY_RULE_IDS = {
    "universal-project-scope",
    "code-authority",
    "source-migration-preservation",
    "plan-before-dispatch",
    "management-user-interface",
    "execution-management-interface",
    "execution-direct-user-redirect",
    "capability-probe",
    "handoff-intent-confirmation",
    "destination-skill-bootstrap",
    "code-handoff-bundle",
    "manual-collaboration-guidance",
    "management-supervision",
    "checkpoint-not-final",
    "source-session-removal",
    "artifact-layout",
    "retention-current-previous",
    "preserve-user-state",
    "secret-redaction",
    "release-batch",
    "semantic-versioning",
}
ROLES = {"management", "execution", "reviewer"}


class WorkflowPolicyError(ValueError):
    """Raised when the shipped runtime policy is incomplete or inconsistent."""


def _default_policy_path(skill_dir: str | Path | None = None) -> Path:
    root = Path(skill_dir).expanduser().resolve() if skill_dir else Path(__file__).resolve().parents[1]
    return root / "assets" / "workflow-policy.json"


def validate_policy(value: Any, expected_version: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise WorkflowPolicyError("workflow policy must be an object")
    if value.get("schemaVersion") != 1 or value.get("policyName") != "universal-agent-workflow-runtime-policy":
        raise WorkflowPolicyError("workflow policy identity is invalid")
    if value.get("policyVersion") != expected_version:
        raise WorkflowPolicyError("workflow policy version mismatch")
    if value.get("runtimeAuthority") != "code-state" or value.get("externalMarkdownRequired") is not False:
        raise WorkflowPolicyError("workflow policy must be code-state and Markdown-independent")
    rules = value.get("rules")
    if not isinstance(rules, list) or not rules:
        raise WorkflowPolicyError("workflow policy needs rules")
    ids: list[str] = []
    for rule in rules:
        if not isinstance(rule, dict):
            raise WorkflowPolicyError("workflow rule must be an object")
        rule_id = rule.get("id")
        applies_to = rule.get("appliesTo")
        if not isinstance(rule_id, str) or not rule_id or rule_id in ids:
            raise WorkflowPolicyError("workflow rule id is missing or duplicated")
        if not isinstance(applies_to, list) or not applies_to or any(role not in ROLES for role in applies_to):
            raise WorkflowPolicyError("workflow rule role scope is invalid")
        if not isinstance(rule.get("enforcement"), str) or not isinstance(rule.get("text"), str):
            raise WorkflowPolicyError("workflow rule enforcement or text is invalid")
        ids.append(rule_id)
    missing = sorted(MANDATORY_RULE_IDS - set(ids))
    if missing:
        raise WorkflowPolicyError("workflow policy missing mandatory rules: " + ", ".join(missing))
    return {
        "ok": True,
        "policyName": value["policyName"],
        "policyVersion": value["policyVersion"],
        "ruleCount": len(rules),
        "mandatoryRuleCount": len(MANDATORY_RULE_IDS),
        "externalMarkdownRequired": False,
    }


def load_policy(skill_dir: str | Path | None, expected_version: str) -> dict[str, Any]:
    path = _default_policy_path(skill_dir)
    if not path.is_file():
        raise WorkflowPolicyError("workflow policy file is missing")
    policy = json.loads(path.read_text(encoding="utf-8"))
    validate_policy(policy, expected_version)
    return policy


def policy_profile(role: str, skill_dir: str | Path | None, expected_version: str) -> dict[str, Any]:
    if role not in ROLES:
        raise WorkflowPolicyError("workflow policy role is invalid")
    policy = load_policy(skill_dir, expected_version)
    rules = [dict(rule) for rule in policy["rules"] if role in rule["appliesTo"]]
    return {
        "policyName": policy["policyName"],
        "policyVersion": policy["policyVersion"],
        "role": role,
        "runtimeAuthority": "code-state",
        "externalMarkdownRequired": False,
        "rules": rules,
    }


__all__ = [
    "MANDATORY_RULE_IDS",
    "WorkflowPolicyError",
    "load_policy",
    "policy_profile",
    "validate_policy",
]
