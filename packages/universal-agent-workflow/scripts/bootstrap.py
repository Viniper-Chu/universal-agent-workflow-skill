#!/usr/bin/env python3
"""Destination Skill deployment and readiness-receipt helpers."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any


SKILL_NAME = "universal-agent-workflow"
SKILL_VERSION = "0.0.1"


class BootstrapError(ValueError):
    """Raised when a destination cannot prove the exact Skill is ready."""


def make_bootstrap_packet(
    role: str,
    destination_id: str,
    peer_identity: str,
    install_source: str,
    capability_mode: str,
) -> dict[str, Any]:
    if role not in {"management", "execution", "reviewer"}:
        raise BootstrapError("invalid destination role")
    if not destination_id or not peer_identity or not install_source:
        raise BootstrapError("bootstrap packet requires destination, peer, and install source")
    if capability_mode not in {"native", "manual"}:
        raise BootstrapError("capability mode must be native or manual")
    return {
        "packetType": "skill-bootstrap",
        "skillName": SKILL_NAME,
        "skillVersion": SKILL_VERSION,
        "role": role,
        "destinationId": destination_id,
        "peerIdentity": peer_identity,
        "installSource": install_source,
        "capabilityMode": capability_mode,
        "verificationCommand": (
            "python scripts/uaw.py destination-bootstrap --skill-dir <installed-skill> "
            f"--role {role} --destination-id {destination_id} --stable-session-id {destination_id} "
            f"--peer-identity {peer_identity} --inventory-file <tool-inventory.json>"
        ),
        "runtimeAuthority": "code-state",
        "externalReadsRequired": False,
        "readyRequired": True,
    }


def _nonempty(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _status_passed(value: Any) -> bool:
    return value is True or (isinstance(value, str) and value.lower() in {"pass", "passed", "ok", "green"})


def validate_readiness_receipt(
    receipt: Any,
    expected_role: str | None = None,
    expected_destination_id: str | None = None,
) -> dict[str, Any]:
    if not isinstance(receipt, dict):
        raise BootstrapError("readiness receipt must be an object")
    if receipt.get("skillName") != SKILL_NAME or receipt.get("skillVersion") != SKILL_VERSION:
        raise BootstrapError("readiness receipt Skill name or version mismatch")
    if receipt.get("runtimeAuthority") != "code-state" or receipt.get("externalReadsRequired") is not False:
        raise BootstrapError("readiness receipt must come from the code-state bootstrap path")
    if receipt.get("validationSource") != "destination-bootstrap":
        raise BootstrapError("readiness receipt validation source is invalid")
    if not _status_passed(receipt.get("policyStatus")) or receipt.get("policyVersion") != SKILL_VERSION:
        raise BootstrapError("readiness receipt runtime policy did not pass")
    if not isinstance(receipt.get("policyRuleCount"), int) or receipt["policyRuleCount"] <= 0:
        raise BootstrapError("readiness receipt runtime policy profile is empty")
    role = receipt.get("role")
    if role not in {"management", "execution", "reviewer"} or (expected_role and role != expected_role):
        raise BootstrapError("readiness receipt role mismatch")
    if not (_nonempty(receipt.get("installPath")) or _nonempty(receipt.get("resolvePath")) or _nonempty(receipt.get("provider"))):
        raise BootstrapError("readiness receipt needs an install/resolve path or provider")
    if not _status_passed(receipt.get("selftestStatus")) or not _status_passed(receipt.get("quickValidateStatus")):
        raise BootstrapError("readiness receipt validation did not pass")
    if receipt.get("capabilityMode") not in {"native", "manual"}:
        raise BootstrapError("readiness receipt capability mode is invalid")
    destination_id = receipt.get("destinationId") or receipt.get("stableSessionId")
    if not _nonempty(destination_id) or (expected_destination_id and destination_id != expected_destination_id):
        raise BootstrapError("readiness receipt destination identity mismatch")
    if receipt["capabilityMode"] == "native" and not _nonempty(receipt.get("stableSessionId")):
        raise BootstrapError("native readiness receipt needs a stable session identity")
    if receipt["capabilityMode"] == "manual":
        if not _nonempty(receipt.get("stableSessionId")) and receipt.get("stableSessionIdUnavailable") is not True:
            raise BootstrapError("manual receipt must declare stableSessionIdUnavailable when no stable ID is available")
    if not _nonempty(receipt.get("peerIdentity")):
        raise BootstrapError("readiness receipt needs a peer identity")
    if receipt.get("ready") is not True:
        raise BootstrapError("readiness receipt must explicitly set ready=true")
    return {"ok": True, "ready": True, "skillName": SKILL_NAME, "skillVersion": SKILL_VERSION, "role": role, "destinationId": destination_id, "capabilityMode": receipt["capabilityMode"]}


def installation_plan(target: str | Path, source: str | Path, expected_version: str = SKILL_VERSION) -> dict[str, Any]:
    """Return a non-destructive deployment plan; never silently overwrite."""
    target_path = Path(target).expanduser()
    source_path = Path(source).expanduser()
    if not source_path.exists():
        raise BootstrapError("Skill install source does not exist")
    existing_version = None
    version_file = target_path / "VERSION"
    skill_file = target_path / "SKILL.md"
    if version_file.exists():
        existing_version = version_file.read_text(encoding="utf-8").strip()
    elif skill_file.exists():
        match = re.search(r"skillVersion\s*[:=]\s*['\"]?([0-9]+\.[0-9]+\.[0-9]+)", skill_file.read_text(encoding="utf-8"))
        existing_version = match.group(1) if match else "unknown"
    if existing_version and existing_version != expected_version:
        return {"ok": False, "action": "update_required", "target": str(target_path), "existingVersion": existing_version, "expectedVersion": expected_version, "source": str(source_path)}
    action = "already_exact" if existing_version == expected_version else "install_or_link"
    return {"ok": True, "action": action, "target": str(target_path), "existingVersion": existing_version, "expectedVersion": expected_version, "source": str(source_path), "overwrite": False}


def make_readiness_receipt(
    role: str,
    destination_id: str,
    stable_session_id: str | None,
    install_path: str,
    capability_mode: str,
    peer_identity: str,
) -> dict[str, Any]:
    receipt = {
        "skillName": SKILL_NAME,
        "skillVersion": SKILL_VERSION,
        "role": role,
        "installPath": install_path,
        "selftestStatus": "passed",
        "quickValidateStatus": "passed",
        "capabilityMode": capability_mode,
        "destinationId": destination_id,
        "peerIdentity": peer_identity,
        "runtimeAuthority": "code-state",
        "externalReadsRequired": False,
        "validationSource": "destination-bootstrap",
        "ready": True,
    }
    if stable_session_id:
        receipt["stableSessionId"] = stable_session_id
    elif capability_mode == "manual":
        receipt["stableSessionIdUnavailable"] = True
    return receipt


def make_verified_readiness_receipt(
    *,
    role: str,
    destination_id: str,
    stable_session_id: str | None,
    install_path: str,
    peer_identity: str,
    install_validation: dict[str, Any],
    selftest: dict[str, Any],
    capability_probe: dict[str, Any],
    policy_validation: dict[str, Any],
    runtime_policy: dict[str, Any],
) -> dict[str, Any]:
    """Build a receipt only from successful code-produced validation results."""
    if install_validation.get("ok") is not True:
        raise BootstrapError("destination install validation failed")
    if selftest.get("ok") is not True:
        raise BootstrapError("destination selftest failed")
    capability_mode = capability_probe.get("mode")
    if capability_mode not in {"native", "manual"}:
        raise BootstrapError("destination capability probe is invalid")
    if policy_validation.get("ok") is not True or policy_validation.get("policyVersion") != SKILL_VERSION:
        raise BootstrapError("destination runtime policy validation failed")
    if runtime_policy.get("role") != role or runtime_policy.get("externalMarkdownRequired") is not False:
        raise BootstrapError("destination runtime policy profile is invalid")
    receipt = make_readiness_receipt(
        role=role,
        destination_id=destination_id,
        stable_session_id=stable_session_id,
        install_path=install_path,
        capability_mode=capability_mode,
        peer_identity=peer_identity,
    )
    receipt["missingCapabilities"] = list(capability_probe.get("missing", []))
    receipt["sourceSessionRemoval"] = dict(capability_probe.get("sourceSessionRemoval", {}))
    receipt["policyStatus"] = "passed"
    receipt["policyVersion"] = policy_validation["policyVersion"]
    receipt["policyRuleCount"] = len(runtime_policy.get("rules", []))
    validate_readiness_receipt(receipt, expected_role=role, expected_destination_id=destination_id)
    return receipt


__all__ = [
    "BootstrapError", "SKILL_NAME", "SKILL_VERSION", "installation_plan", "make_bootstrap_packet",
    "make_readiness_receipt", "make_verified_readiness_receipt", "validate_readiness_receipt",
]
