#!/usr/bin/env python3
"""Self-contained, code-consumed handoff bundles.

The bundle is the runtime continuity authority for a destination session.  It
must contain enough structured state to continue without asking the receiving
agent to read legacy or project Markdown files.
"""

from __future__ import annotations

from typing import Any


BUNDLE_SCHEMA_VERSION = 1
PACKET_TYPE = "uaw-code-handoff"
RECEIPT_TYPE = "uaw-code-handoff-receipt"
ROLES = {"management", "execution", "reviewer"}


class HandoffBundleError(ValueError):
    """Raised when a code handoff is incomplete or targets the wrong session."""


def _nonempty(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _string_list(value: Any, label: str) -> list[str]:
    if not isinstance(value, list) or any(not _nonempty(item) for item in value):
        raise HandoffBundleError(f"{label} must be a list of non-empty strings")
    return list(value)


def validate_continuity_context(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise HandoffBundleError("continuity context must be an object")
    required_text = ("project", "objective", "currentState")
    for key in required_text:
        if not _nonempty(value.get(key)):
            raise HandoffBundleError(f"continuity context missing {key}")
    next_action = value.get("nextAction")
    if not (_nonempty(next_action) or isinstance(next_action, dict)):
        raise HandoffBundleError("continuity context needs a nextAction")
    normalized = dict(value)
    for key in ("facts", "protectedBoundaries", "forbiddenActions", "pendingDecisions"):
        normalized[key] = _string_list(value.get(key, []), key)
    required_external = value.get("requiredExternalReads", [])
    if required_external != []:
        raise HandoffBundleError("runtime handoff cannot require external document reads")
    normalized["requiredExternalReads"] = []
    return normalized


def _validate_events(events: Any, task_id: str) -> list[dict[str, Any]]:
    if not isinstance(events, list) or not events:
        raise HandoffBundleError("handoff bundle needs a non-empty event history")
    normalized: list[dict[str, Any]] = []
    for position, event in enumerate(events, start=1):
        if not isinstance(event, dict):
            raise HandoffBundleError("handoff event must be an object")
        if event.get("id") != position or event.get("task_id") != task_id:
            raise HandoffBundleError("handoff events are not continuous or task-bound")
        if position == 1 and event.get("event") != "contract.created":
            raise HandoffBundleError("handoff history must start with contract.created")
        normalized.append(dict(event))
    return normalized


def build_code_handoff_bundle(
    *,
    skill_name: str,
    skill_version: str,
    contract: dict[str, Any],
    snapshot: dict[str, Any],
    events: list[dict[str, Any]],
    continuity: dict[str, Any],
    source_session_id: str,
    destination_session_id: str,
    source_role: str,
    destination_role: str,
    management_peer: str,
    execution_peer: str,
    capability_mode: str,
) -> dict[str, Any]:
    task_id = contract.get("task_id")
    if not all(_nonempty(value) for value in (skill_name, skill_version, task_id, source_session_id, destination_session_id, management_peer, execution_peer)):
        raise HandoffBundleError("handoff identity fields must be non-empty")
    if source_session_id == destination_session_id:
        raise HandoffBundleError("source and destination sessions must differ")
    if source_role not in ROLES or destination_role not in ROLES:
        raise HandoffBundleError("handoff role is invalid")
    if capability_mode not in {"native", "manual"}:
        raise HandoffBundleError("handoff capability mode is invalid")
    if snapshot.get("task_id") != task_id or snapshot.get("contract", {}).get("task_id") != task_id:
        raise HandoffBundleError("snapshot and contract task identities differ")
    normalized_continuity = validate_continuity_context(continuity)
    normalized_events = _validate_events(events, task_id)
    bundle = {
        "packetType": PACKET_TYPE,
        "schemaVersion": BUNDLE_SCHEMA_VERSION,
        "runtimeAuthority": "code-state",
        "markdownRuntimeDependency": False,
        "externalReadsRequired": False,
        "skillName": skill_name,
        "skillVersion": skill_version,
        "taskId": task_id,
        "source": {"sessionId": source_session_id, "role": source_role},
        "destination": {"sessionId": destination_session_id, "role": destination_role},
        "peers": {"management": management_peer, "execution": execution_peer},
        "capabilityMode": capability_mode,
        "contract": dict(contract),
        "workflowState": dict(snapshot),
        "events": normalized_events,
        "continuity": normalized_continuity,
        "rolePolicy": {
            "userFacingRole": "management",
            "executionDirectUserRoute": "REDIRECT_TO_MANAGEMENT",
            "manualRelayAcceptedWhenValidated": True,
            "managementAudience": "plain-language",
            "executionAudience": "technical",
        },
        "requiredExternalReads": [],
        "redacted": True,
    }
    validate_code_handoff_bundle(bundle)
    return bundle


def validate_code_handoff_bundle(
    value: Any,
    *,
    expected_skill_name: str | None = None,
    expected_skill_version: str | None = None,
    expected_destination_id: str | None = None,
    expected_role: str | None = None,
) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise HandoffBundleError("handoff bundle must be an object")
    if value.get("packetType") != PACKET_TYPE or value.get("schemaVersion") != BUNDLE_SCHEMA_VERSION:
        raise HandoffBundleError("handoff bundle type or schema is invalid")
    if value.get("runtimeAuthority") != "code-state" or value.get("markdownRuntimeDependency") is not False:
        raise HandoffBundleError("handoff runtime authority must be code-state")
    if value.get("externalReadsRequired") is not False or value.get("requiredExternalReads") != []:
        raise HandoffBundleError("handoff cannot depend on external document reads")
    if value.get("redacted") is not True:
        raise HandoffBundleError("handoff bundle must be redacted")
    skill_name = value.get("skillName")
    skill_version = value.get("skillVersion")
    if not _nonempty(skill_name) or not _nonempty(skill_version):
        raise HandoffBundleError("handoff Skill identity is missing")
    if expected_skill_name and skill_name != expected_skill_name:
        raise HandoffBundleError("handoff Skill name mismatch")
    if expected_skill_version and skill_version != expected_skill_version:
        raise HandoffBundleError("handoff Skill version mismatch")
    task_id = value.get("taskId")
    contract = value.get("contract")
    snapshot = value.get("workflowState")
    if not _nonempty(task_id) or not isinstance(contract, dict) or not isinstance(snapshot, dict):
        raise HandoffBundleError("handoff task state is incomplete")
    if contract.get("task_id") != task_id or snapshot.get("task_id") != task_id:
        raise HandoffBundleError("handoff task identities differ")
    if snapshot.get("contract") != contract:
        raise HandoffBundleError("handoff snapshot contract differs from bundled contract")
    source = value.get("source")
    destination = value.get("destination")
    if not isinstance(source, dict) or not isinstance(destination, dict):
        raise HandoffBundleError("handoff session identities are missing")
    source_id = source.get("sessionId")
    destination_id = destination.get("sessionId")
    role = destination.get("role")
    if not _nonempty(source_id) or not _nonempty(destination_id) or source_id == destination_id:
        raise HandoffBundleError("handoff source/destination identity is invalid")
    if role not in ROLES or (expected_role and role != expected_role):
        raise HandoffBundleError("handoff destination role mismatch")
    if expected_destination_id and destination_id != expected_destination_id:
        raise HandoffBundleError("handoff destination session mismatch")
    validate_continuity_context(value.get("continuity"))
    _validate_events(value.get("events"), task_id)
    role_policy = value.get("rolePolicy")
    if not isinstance(role_policy, dict) or role_policy.get("userFacingRole") != "management" or role_policy.get("executionDirectUserRoute") != "REDIRECT_TO_MANAGEMENT":
        raise HandoffBundleError("handoff role policy is invalid")
    return {
        "ok": True,
        "taskId": task_id,
        "destinationId": destination_id,
        "role": role,
        "skillName": skill_name,
        "skillVersion": skill_version,
        "externalReadsRequired": False,
    }


def receive_code_handoff_bundle(
    value: Any,
    *,
    expected_skill_name: str,
    expected_skill_version: str,
    expected_destination_id: str,
    expected_role: str,
) -> dict[str, Any]:
    validation = validate_code_handoff_bundle(
        value,
        expected_skill_name=expected_skill_name,
        expected_skill_version=expected_skill_version,
        expected_destination_id=expected_destination_id,
        expected_role=expected_role,
    )
    return {
        "ok": True,
        "packetType": RECEIPT_TYPE,
        "status": "CODE_HANDOFF_ACCEPTED",
        "taskId": validation["taskId"],
        "destinationId": validation["destinationId"],
        "role": validation["role"],
        "skillName": validation["skillName"],
        "skillVersion": validation["skillVersion"],
        "runtimeAuthority": "code-state",
        "externalReadsRequired": False,
        "context": {
            "contract": value["contract"],
            "workflowState": value["workflowState"],
            "continuity": value["continuity"],
            "rolePolicy": value["rolePolicy"],
            "nextAction": value["continuity"]["nextAction"],
        },
        "accepted": True,
    }


__all__ = [
    "BUNDLE_SCHEMA_VERSION",
    "HandoffBundleError",
    "PACKET_TYPE",
    "RECEIPT_TYPE",
    "build_code_handoff_bundle",
    "receive_code_handoff_bundle",
    "validate_code_handoff_bundle",
    "validate_continuity_context",
]
