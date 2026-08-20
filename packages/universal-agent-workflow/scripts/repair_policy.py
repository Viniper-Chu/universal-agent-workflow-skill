#!/usr/bin/env python3
"""Code-backed repair completion policy.

Product root-cause closure and recovery of already affected state are separate
outcomes.  The workflow engine consumes this module instead of inferring repair
completion from prose reports or from a repaired data snapshot alone.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any, Iterable


class RepairPolicyError(ValueError):
    """Raised when a repair contract or its evidence is structurally invalid."""


ROOT_CAUSE_STAGES = (
    "original_failure_reproduced",
    "first_fault_layer_identified",
    "shared_root_cause_fixed",
    "root_cause_regression_red_green",
    "direct_consumers_passed",
)
RECOVERY_STAGES = (
    "isolated_production_rebuild",
    "identity_rebound",
    "shared_validators_recomputed",
    "non_target_state_conserved",
)


def _nonempty(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _string_list(value: Any, label: str, *, required: bool = False) -> list[str]:
    if not isinstance(value, list) or any(not _nonempty(item) for item in value):
        raise RepairPolicyError(f"{label} must be a list of non-empty strings")
    if required and not value:
        raise RepairPolicyError(f"{label} must not be empty")
    return list(value)


def build_repair_policy(
    *,
    data_recovery_required: bool = False,
    real_data_write: bool = False,
    identity_keys: Iterable[str] = (),
    validation_checks: Iterable[str] = (),
    conservation_scopes: Iterable[str] = (),
    preserve_external_call_ledger: bool = False,
) -> dict[str, Any]:
    """Build one project-neutral repair contract projection."""

    policy = {
        "schemaVersion": 1,
        "enabled": True,
        "productRootCauseRequired": True,
        "rootCauseStages": list(ROOT_CAUSE_STAGES),
        "dataRecoveryRequired": bool(data_recovery_required),
        "recovery": {
            "candidateMode": "isolated-production-chain" if data_recovery_required else "not-required",
            "realDataWrite": bool(real_data_write),
            "identityKeys": list(identity_keys),
            "sharedValidationChecks": list(validation_checks),
            "conservationScopes": list(conservation_scopes),
            "snapshotBeforeWrite": bool(real_data_write),
            "zeroWriteOnGuardFailure": bool(real_data_write),
            "externalCallLedger": "preserve" if preserve_external_call_ledger else "not-applicable",
        },
    }
    return validate_repair_policy(policy)


def validate_repair_policy(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise RepairPolicyError("repair_policy must be an object")
    if value.get("schemaVersion") != 1 or value.get("enabled") is not True:
        raise RepairPolicyError("repair_policy identity is invalid")
    if value.get("productRootCauseRequired") is not True:
        raise RepairPolicyError("repair_policy must require product root-cause closure")
    if value.get("rootCauseStages") != list(ROOT_CAUSE_STAGES):
        raise RepairPolicyError("repair_policy root-cause stages are incomplete or out of order")
    if not isinstance(value.get("dataRecoveryRequired"), bool):
        raise RepairPolicyError("repair_policy dataRecoveryRequired must be boolean")
    recovery = value.get("recovery")
    if not isinstance(recovery, dict):
        raise RepairPolicyError("repair_policy recovery must be an object")
    if not isinstance(recovery.get("realDataWrite"), bool):
        raise RepairPolicyError("repair_policy realDataWrite must be boolean")
    if not isinstance(recovery.get("snapshotBeforeWrite"), bool) or not isinstance(recovery.get("zeroWriteOnGuardFailure"), bool):
        raise RepairPolicyError("repair_policy write guards must be boolean")
    ledger = recovery.get("externalCallLedger")
    if ledger not in {"preserve", "not-applicable"}:
        raise RepairPolicyError("repair_policy externalCallLedger is invalid")
    required = value["dataRecoveryRequired"]
    identity_keys = _string_list(recovery.get("identityKeys"), "repair_policy identityKeys", required=required)
    checks = _string_list(recovery.get("sharedValidationChecks"), "repair_policy sharedValidationChecks", required=required)
    scopes = _string_list(recovery.get("conservationScopes"), "repair_policy conservationScopes", required=required)
    if required and recovery.get("candidateMode") != "isolated-production-chain":
        raise RepairPolicyError("data recovery requires an isolated production-chain candidate")
    if not required and recovery.get("candidateMode") != "not-required":
        raise RepairPolicyError("repair_policy candidateMode must be not-required when recovery is not required")
    if not required and (recovery["realDataWrite"] or identity_keys or checks or scopes or ledger == "preserve"):
        raise RepairPolicyError("recovery controls require dataRecoveryRequired=true")
    if recovery["realDataWrite"] and not (recovery["snapshotBeforeWrite"] and recovery["zeroWriteOnGuardFailure"]):
        raise RepairPolicyError("real-data recovery requires snapshot-first and zero-write guard failure")
    normalized = deepcopy(value)
    normalized["recovery"]["identityKeys"] = identity_keys
    normalized["recovery"]["sharedValidationChecks"] = checks
    normalized["recovery"]["conservationScopes"] = scopes
    return normalized


def evaluate_repair_evidence(policy_value: Any, evidence_value: Any) -> dict[str, Any]:
    """Validate evidence and derive separate product/data completion outcomes."""

    policy = validate_repair_policy(policy_value)
    if not isinstance(evidence_value, dict) or evidence_value.get("schemaVersion") != 1:
        raise RepairPolicyError("repair evidence identity is invalid")
    root = evidence_value.get("rootCause")
    if not isinstance(root, dict):
        raise RepairPolicyError("repair evidence rootCause must be an object")
    regression = root.get("regression")
    if not isinstance(regression, dict):
        raise RepairPolicyError("repair evidence rootCause.regression must be an object")
    for key in ("originalFailureReproduced", "sharedRootCauseFixed", "directConsumersPassed"):
        if not isinstance(root.get(key), bool):
            raise RepairPolicyError(f"repair evidence rootCause.{key} must be boolean")
    for key in ("redBeforeFix", "greenAfterFix"):
        if not isinstance(regression.get(key), bool):
            raise RepairPolicyError(f"repair evidence rootCause.regression.{key} must be boolean")
    first_fault_layer = root.get("firstFaultLayer")
    root_gates = {
        "original_failure_reproduced": root["originalFailureReproduced"],
        "first_fault_layer_identified": _nonempty(first_fault_layer),
        "shared_root_cause_fixed": root["sharedRootCauseFixed"],
        "root_cause_regression_red_green": regression["redBeforeFix"] and regression["greenAfterFix"],
        "direct_consumers_passed": root["directConsumersPassed"],
    }
    product_closed = all(root_gates.values())

    recovery_evidence = evidence_value.get("dataRecovery")
    if not isinstance(recovery_evidence, dict):
        raise RepairPolicyError("repair evidence dataRecovery must be an object")
    status = recovery_evidence.get("status")
    if status not in {"not-required", "pending", "recovered"}:
        raise RepairPolicyError("repair evidence dataRecovery.status is invalid")
    recovery_gates: dict[str, bool] = {}
    if policy["dataRecoveryRequired"]:
        for key in ("isolatedProductionRebuild", "identityRebound", "sharedValidatorsRecomputed"):
            if not isinstance(recovery_evidence.get(key), bool):
                raise RepairPolicyError(f"repair evidence dataRecovery.{key} must be boolean")
        observed_identity_keys = _string_list(recovery_evidence.get("identityKeys"), "repair evidence dataRecovery.identityKeys")
        observed_checks = _string_list(recovery_evidence.get("sharedValidationChecks"), "repair evidence dataRecovery.sharedValidationChecks")
        conservation = recovery_evidence.get("conservation")
        if not isinstance(conservation, dict) or not isinstance(conservation.get("passed"), bool):
            raise RepairPolicyError("repair evidence dataRecovery.conservation is invalid")
        observed_scopes = _string_list(conservation.get("scopes"), "repair evidence conservation.scopes")
        recovery_gates = {
            "isolated_production_rebuild": recovery_evidence["isolatedProductionRebuild"],
            "identity_rebound": recovery_evidence["identityRebound"] and set(policy["recovery"]["identityKeys"]).issubset(observed_identity_keys),
            "shared_validators_recomputed": recovery_evidence["sharedValidatorsRecomputed"] and set(policy["recovery"]["sharedValidationChecks"]).issubset(observed_checks),
            "non_target_state_conserved": conservation["passed"] and set(policy["recovery"]["conservationScopes"]).issubset(observed_scopes),
        }
        if policy["recovery"]["realDataWrite"]:
            for key in ("snapshotSucceeded", "guardFailuresZeroWrite"):
                if not isinstance(recovery_evidence.get(key), bool):
                    raise RepairPolicyError(f"repair evidence dataRecovery.{key} must be boolean")
            recovery_gates["snapshot_before_write"] = recovery_evidence["snapshotSucceeded"]
            recovery_gates["guard_failures_zero_write"] = recovery_evidence["guardFailuresZeroWrite"]
        if policy["recovery"]["externalCallLedger"] == "preserve":
            if not isinstance(conservation.get("externalCallLedgerPreserved"), bool):
                raise RepairPolicyError("repair evidence externalCallLedgerPreserved must be boolean")
            recovery_gates["external_call_ledger_preserved"] = conservation["externalCallLedgerPreserved"]
        data_recovered = status == "recovered" and all(recovery_gates.values())
    else:
        data_recovered = status == "not-required"

    complete = product_closed and data_recovered
    if complete:
        outcome = "complete"
    elif data_recovered and not product_closed:
        outcome = "data_recovered_product_root_cause_open"
    elif product_closed:
        outcome = "product_root_cause_closed_data_pending"
    else:
        outcome = "product_root_cause_open"
    return {
        "ok": True,
        "complete": complete,
        "outcome": outcome,
        "productRootCauseClosed": product_closed,
        "dataRecoveryRequired": policy["dataRecoveryRequired"],
        "dataRecovered": data_recovered,
        "rootCauseGates": root_gates,
        "recoveryGates": recovery_gates,
        "evidence": deepcopy(evidence_value),
    }


def repair_policy_projection() -> dict[str, Any]:
    return {
        "schemaVersion": 1,
        "rootCauseStages": list(ROOT_CAUSE_STAGES),
        "recoveryStages": list(RECOVERY_STAGES),
        "completionRule": "product-root-cause-closed-and-required-data-recovered",
        "dataRecoveryAloneIsCompletion": False,
    }


__all__ = [
    "RECOVERY_STAGES",
    "ROOT_CAUSE_STAGES",
    "RepairPolicyError",
    "build_repair_policy",
    "evaluate_repair_evidence",
    "repair_policy_projection",
    "validate_repair_policy",
]
