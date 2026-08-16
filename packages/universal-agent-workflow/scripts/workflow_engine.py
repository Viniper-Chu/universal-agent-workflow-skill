#!/usr/bin/env python3
"""Contract-first workflow engine for the universal-agent-workflow skill.

The module is deliberately standard-library-only.  The event log is the
authoritative state source; JSON state files and Markdown are projections.
"""

from __future__ import annotations

import json
import re
import shutil
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from bootstrap import SKILL_NAME, SKILL_VERSION, make_bootstrap_packet, validate_readiness_receipt
from handoff_bundle import (
    HandoffBundleError,
    build_code_handoff_bundle,
    receive_code_handoff_bundle,
    validate_code_handoff_bundle,
    validate_continuity_context,
)
from session_migration import build_delete_action, classify_delete_capability, inventory_tools, validate_delete_target
from retention import (
    cleanup_artifacts,
    register_artifact as register_retention_artifact,
    retention_summary,
    rotate_generations,
)
from workflow_policy import WorkflowPolicyError, load_policy, validate_policy


STATES = {
    "intake",
    "planning",
    "bootstrap_pending",
    "destination_ready",
    "dispatched",
    "executing",
    "reviewing",
    "correction",
    "accepted",
    "complete",
    "handoff_pending",
    "handoff_complete",
    "removed",
    "deleted",
    "blocked",
}
ROLES = {"management", "execution", "reviewer"}
EVENTS = {
    "contract.created",
    "plan.created",
    "migration.authorized",
    "bootstrap.requested",
    "destination.ready",
    "handoff.requested",
    "handoff.bundle_received",
    "handoff.accepted",
    "handoff.completed",
    "source-session.removal_requested",
    "source-session.removal_blocked",
    "source-session.removal_failed",
    "source-session.removed",
    "dispatch.requested",
    "execution.started",
    "execution.reported",
    "review.correction_requested",
    "review.accepted",
    "completion.requested",
    "blocked",
    "unblocked",
    "retention.dry_run",
    "retention.applied",
}
EVENT_ACTOR_ROLES: dict[str, set[str]] = {
    "contract.created": {"management"},
    "plan.created": {"management"},
    "migration.authorized": {"management"},
    "bootstrap.requested": {"management"},
    "destination.ready": {"execution"},
    "handoff.requested": {"management"},
    "handoff.bundle_received": {"execution"},
    "handoff.accepted": {"execution"},
    "handoff.completed": {"management"},
    "source-session.removal_requested": {"management"},
    "source-session.removal_blocked": {"management"},
    "source-session.removal_failed": {"host", "system"},
    "source-session.removed": {"host", "system"},
    "dispatch.requested": {"management"},
    "execution.started": {"execution"},
    "execution.reported": {"execution"},
    "review.correction_requested": {"management"},
    "review.accepted": {"management"},
    "completion.requested": {"management"},
    "blocked": {"management"},
    "unblocked": {"management"},
    "retention.dry_run": {"management", "system"},
    "retention.applied": {"management", "system"},
}
ACTORS = {"management", "execution", "reviewer", "host", "system"}


class WorkflowError(RuntimeError):
    """Raised for a contract, state, path, or receipt violation."""


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _as_dict(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise WorkflowError(f"{label} must be an object")
    return value


def _nonempty(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


_SECRET_PATTERNS = [
    re.compile(r"(?i)(authorization\s*[:=]\s*(?:bearer\s+)?)([^\s,;]+)"),
    re.compile(r"(?i)(bearer\s+)([^\s,;]+)"),
    re.compile(r"(?i)((?:api[_-]?key|token|password|secret|cookie)\s*[:=]\s*)([^\s,;]+)"),
    re.compile(r"(?i)(\b(?:sk|rk|pk)_[A-Za-z0-9_-]{8,}\b)"),
    re.compile(r"(?i)(eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,})"),
]


def redact_text(value: str) -> str:
    """Redact common credential-shaped values without returning the match."""
    text = str(value)
    for pattern in _SECRET_PATTERNS:
        if pattern.groups >= 2 and "(?:sk|rk|pk)" not in pattern.pattern:
            text = pattern.sub(lambda match: match.group(1) + "[REDACTED]", text)
        else:
            text = pattern.sub("[REDACTED]", text)
    return text


def sensitive_match_count(value: str) -> int:
    return sum(len(pattern.findall(str(value))) for pattern in _SECRET_PATTERNS)


def _json_write(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _json_read(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _inside(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def resolve_output_root(project_root: str | Path, output_root: str | Path) -> Path:
    project = Path(project_root).expanduser().resolve()
    output = Path(output_root).expanduser()
    if not output.is_absolute():
        output = project / output
    output = output.resolve()
    home = Path.home().resolve()
    desktop = (home / "Desktop").resolve()
    if output == project or output == home or output == desktop:
        raise WorkflowError("output root must be a dedicated controlled directory")
    if not _inside(output, project):
        raise WorkflowError("output root must be inside the project root")
    return output


def initialize_project(project_root: str | Path, output_root: str | Path = ".agent-workflow") -> dict[str, Any]:
    project = Path(project_root).expanduser().resolve()
    root = resolve_output_root(project, output_root)
    for name in ("contracts", "state/tasks", "reports", "handoffs", "evidence", "tmp"):
        (root / name).mkdir(parents=True, exist_ok=True)
    config_path = root / "config.json"
    config = {
        "schemaVersion": 1,
        "skillName": SKILL_NAME,
        "skillVersion": SKILL_VERSION,
        "projectRoot": str(project),
        "outputRoot": str(root),
        "retentionGenerations": 2,
        "generation": 1,
    }
    if config_path.exists():
        existing = _json_read(config_path)
        if existing.get("skillName") != SKILL_NAME:
            raise WorkflowError("controlled output root belongs to another workflow")
        config["retentionGenerations"] = int(existing.get("retentionGenerations", 2))
        config["generation"] = int(existing.get("generation", 1))
    _json_write(config_path, config)
    return {"ok": True, "skillName": SKILL_NAME, "skillVersion": SKILL_VERSION, "outputRoot": str(root)}


def validate_contract(contract: dict[str, Any]) -> dict[str, Any]:
    required = ("task_id", "title", "objective", "role", "complexity", "acceptance", "allowed_actions", "forbidden_actions")
    for key in required:
        if key not in contract:
            raise WorkflowError(f"contract missing {key}")
    if not _nonempty(contract["task_id"]) or not _nonempty(contract["title"]) or not _nonempty(contract["objective"]):
        raise WorkflowError("contract identity fields must be non-empty")
    if contract["role"] not in ROLES:
        raise WorkflowError("contract role is invalid")
    if contract["complexity"] not in {"simple", "complex"}:
        raise WorkflowError("contract complexity is invalid")
    for key in ("acceptance", "allowed_actions", "forbidden_actions"):
        if not isinstance(contract[key], list):
            raise WorkflowError(f"contract {key} must be a list")
    if "plan_steps" in contract and not isinstance(contract["plan_steps"], list):
        raise WorkflowError("contract plan_steps must be a list")
    if "migration_policy" in contract:
        policy = contract["migration_policy"]
        if not isinstance(policy, dict) or not isinstance(policy.get("enabled", False), bool):
            raise WorkflowError("migration_policy must be an object with boolean enabled")
    contract.setdefault("skill_name", SKILL_NAME)
    contract.setdefault("skill_version", SKILL_VERSION)
    if contract["skill_name"] != SKILL_NAME or contract["skill_version"] != SKILL_VERSION:
        raise WorkflowError("contract skill version mismatch")
    return contract


def make_contract(
    task_id: str,
    title: str,
    objective: str,
    role: str = "management",
    complexity: str = "simple",
    acceptance: Iterable[str] | None = None,
    allowed_actions: Iterable[str] | None = None,
    forbidden_actions: Iterable[str] | None = None,
    plan_steps: Iterable[str] | None = None,
    destination_role: str = "execution",
    migration_policy: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if plan_steps is None:
        plan_steps = (
            ["plan", "dispatch", "execute", "report", "review", "accept"]
            if complexity == "complex"
            else ["plan", "execute", "review"]
        )
    else:
        plan_steps = list(plan_steps)
        if not plan_steps:
            raise WorkflowError("plan_steps must contain at least one step")
    contract = {
        "task_id": task_id,
        "title": title,
        "objective": objective,
        "role": role,
        "complexity": complexity,
        "acceptance": list(acceptance or ["independent acceptance"]),
        "allowed_actions": list(allowed_actions or []),
        "forbidden_actions": list(forbidden_actions or []),
        "plan_steps": list(plan_steps),
        "destination_role": destination_role,
        "migration_policy": dict(migration_policy or {"enabled": False}),
        "skill_name": SKILL_NAME,
        "skill_version": SKILL_VERSION,
    }
    return validate_contract(contract)


def _tool_names(inventory: Any) -> set[str]:
    return set(inventory_tools(inventory))


def probe_capabilities(inventory: Any) -> dict[str, Any]:
    tools = inventory_tools(inventory)
    names = set(tools)
    aliases = {
        "create": {"create_thread", "create_task", "create_session"},
        "send": {"send_message_to_thread", "send_message", "send_task"},
        "wait": {"wait_thread", "wait_threads", "wait_task", "wait"},
        "read": {"read_thread", "read_task", "read"},
        "handoff": {"handoff_thread", "handoff_task", "transfer_thread"},
    }
    selected: dict[str, str | None] = {}
    missing: list[str] = []
    for capability, options in aliases.items():
        found = next((tools[name] for name in options if name in names), None)
        selected[capability] = found
        if capability != "handoff" and found is None:
            missing.append(capability)
    native = not missing
    removal = classify_delete_capability(inventory)
    selected["thread_delete"] = removal.get("deleteTool")
    selected["thread_archive"] = removal.get("archiveTool")
    return {
        "ok": True,
        "mode": "native" if native else "manual",
        "native": native,
        "missing": missing,
        "selected": selected,
        "sourceSessionRemoval": removal,
        "provider": "host-tool-inventory",
    }


def _state_from_events(events: list[dict[str, Any]], contract: dict[str, Any]) -> dict[str, Any]:
    state: dict[str, Any] = {
        "task_id": contract["task_id"],
        "status": "intake",
        "contract": contract,
        "plan_steps": list(contract.get("plan_steps", [])),
        "destination_ready": False,
        "readiness_receipt": None,
        "destination_id": None,
        "handoff_requested": False,
        "handoff_bundle_received": False,
        "handoff_bundle_receipt": None,
        "handoff_accepted": False,
        "handoff_complete": False,
        "source_delete_requested": False,
        "source_session_id": None,
        "source_delete_result": None,
        "source_removal_requested": False,
        "source_removal_result": None,
        "source_removal_mode": None,
        "migration_policy_enabled": bool(contract.get("migration_policy", {}).get("enabled", False)),
        "migration_policy_authorized": False,
        "session_deleted": False,
        "session_removed": False,
        "execution_report_ref": None,
        "independent_acceptance": False,
        "checkpoint": False,
        "last_reason": None,
        "last_event": None,
        "event_count": 0,
        "retention_dry_run_done": False,
        "retention_applied": False,
    }
    for event in events:
        event_name = event["event"]
        payload = event.get("payload") or {}
        state["last_event"] = event_name
        state["event_count"] += 1
        if event_name == "plan.created":
            state["status"] = "planning"
            state["plan_steps"] = payload.get("steps", state["plan_steps"])
        elif event_name == "migration.authorized":
            state["migration_policy_authorized"] = True
            state["migration_policy_enabled"] = True
        elif event_name == "bootstrap.requested":
            state["status"] = "bootstrap_pending"
        elif event_name == "destination.ready":
            state["status"] = "destination_ready"
            state["destination_ready"] = True
            state["readiness_receipt"] = payload.get("receipt")
            state["destination_id"] = payload.get("receipt", {}).get("destinationId") or payload.get("receipt", {}).get("stableSessionId")
        elif event_name == "handoff.requested":
            state["status"] = "handoff_pending"
            state["handoff_requested"] = True
        elif event_name == "handoff.bundle_received":
            state["status"] = "handoff_pending"
            state["handoff_bundle_received"] = True
            state["handoff_bundle_receipt"] = payload.get("receipt")
        elif event_name == "handoff.accepted":
            state["status"] = "destination_ready"
            state["handoff_accepted"] = True
        elif event_name == "handoff.completed":
            state["status"] = "handoff_complete"
            state["handoff_complete"] = True
        elif event_name in {"source-session.delete_requested", "source-session.removal_requested"}:
            state["source_delete_requested"] = True
            state["source_removal_requested"] = True
            state["source_session_id"] = payload.get("sourceSessionId")
            state["source_delete_result"] = "requested"
            state["source_removal_result"] = "requested"
            state["source_removal_mode"] = payload.get("removalMode")
        elif event_name == "source-session.removal_blocked":
            state["source_removal_result"] = payload.get("result", "manual_required")
            state["source_session_id"] = payload.get("sourceSessionId")
        elif event_name == "source-session.removal_failed":
            state["source_removal_result"] = "failed"
        elif event_name in {"source-session.deleted", "source-session.removed"}:
            state["status"] = "removed"
            state["session_deleted"] = payload.get("removalMode") == "delete"
            state["session_removed"] = True
            state["source_delete_result"] = "deleted"
            state["source_removal_result"] = "removed"
            state["source_removal_mode"] = payload.get("removalMode")
        elif event_name == "dispatch.requested":
            state["status"] = "dispatched"
        elif event_name == "execution.started":
            state["status"] = "executing"
        elif event_name == "execution.reported":
            state["status"] = "reviewing"
            state["execution_report_ref"] = payload.get("report_ref")
        elif event_name == "review.correction_requested":
            state["status"] = "correction"
            state["last_reason"] = payload.get("reason")
            state["checkpoint"] = bool(payload.get("checkpoint", False))
        elif event_name == "review.accepted":
            state["status"] = "accepted"
            state["independent_acceptance"] = bool(payload.get("independent", False))
            state["checkpoint"] = bool(payload.get("checkpoint", False))
        elif event_name == "completion.requested":
            state["status"] = "complete"
        elif event_name == "blocked":
            state["status"] = "blocked"
            state["last_reason"] = payload.get("reason")
        elif event_name == "unblocked":
            state["status"] = payload.get("resume_status", "planning")
        elif event_name == "retention.dry_run":
            state["retention_dry_run_done"] = True
        elif event_name == "retention.applied":
            state["retention_dry_run_done"] = True
            state["retention_applied"] = True
    return state


def _validate_event(state: dict[str, Any], event: str, payload: dict[str, Any], actor: str | None = None) -> None:
    if event not in EVENTS:
        raise WorkflowError(f"unknown event: {event}")
    if actor is not None:
        if actor not in ACTORS:
            raise WorkflowError("event actor is invalid")
        allowed = EVENT_ACTOR_ROLES.get(event, set())
        if actor not in allowed:
            raise WorkflowError(f"actor {actor} is not allowed for {event}")
    status = state["status"]
    if event == "contract.created":
        if state.get("event_count"):
            raise WorkflowError("contract already created")
        return
    if event == "plan.created":
        if status != "intake":
            raise WorkflowError("plan must start from intake")
        if not isinstance(payload.get("steps"), list) or not payload["steps"]:
            raise WorkflowError("plan requires at least one step")
        return
    if event == "migration.authorized":
        if status != "handoff_complete" or not state.get("handoff_complete"):
            raise WorkflowError("migration authorization requires management-confirmed handoff")
        if payload.get("userConfirmed") is not True or payload.get("policyEnabled") is not True:
            raise WorkflowError("migration authorization requires explicit user confirmation")
        validate_delete_target(payload.get("sourceSessionId"), payload.get("targetSessionId"), payload.get("currentSessionId"))
        return
    if event == "bootstrap.requested":
        if status not in {"planning", "correction", "reviewing", "accepted", "intake", "destination_ready"}:
            raise WorkflowError("bootstrap cannot start from current state")
        return
    if event == "destination.ready":
        if status != "bootstrap_pending":
            raise WorkflowError("readiness receipt is only accepted after bootstrap request")
        receipt = validate_readiness_receipt(payload.get("receipt"), expected_role=payload.get("expected_role"), expected_destination_id=payload.get("expected_destination_id"))
        if not receipt["ready"]:
            raise WorkflowError("destination receipt is not ready")
        return
    if event == "handoff.requested":
        if not state.get("destination_ready"):
            raise WorkflowError("handoff requires a validated destination receipt")
        if status not in {"destination_ready", "accepted", "reviewing", "correction", "executing"}:
            raise WorkflowError("handoff cannot start from current state")
        try:
            validate_code_handoff_bundle(
                payload.get("packet"),
                expected_skill_name=SKILL_NAME,
                expected_skill_version=SKILL_VERSION,
                expected_destination_id=state.get("destination_id"),
                expected_role=state.get("contract", {}).get("destination_role", "execution"),
            )
        except HandoffBundleError as exc:
            raise WorkflowError(str(exc)) from exc
        return
    if event == "handoff.bundle_received":
        if status != "handoff_pending" or not state.get("handoff_requested"):
            raise WorkflowError("code handoff receipt requires a pending handoff bundle")
        receipt = payload.get("receipt")
        if not isinstance(receipt, dict) or receipt.get("packetType") != "uaw-code-handoff-receipt":
            raise WorkflowError("code handoff receipt is invalid")
        if receipt.get("accepted") is not True or receipt.get("externalReadsRequired") is not False:
            raise WorkflowError("destination did not accept the code-state handoff")
        if receipt.get("taskId") != state.get("task_id") or receipt.get("destinationId") != state.get("destination_id"):
            raise WorkflowError("code handoff receipt identity mismatch")
        return
    if event == "handoff.accepted":
        if not state.get("destination_ready") or status != "handoff_pending":
            raise WorkflowError("handoff acceptance requires destination readiness")
        if not state.get("handoff_bundle_received"):
            raise WorkflowError("handoff acceptance requires a code-consumed bundle receipt")
        if not payload.get("peer_identity") or not payload.get("role"):
            raise WorkflowError("handoff acceptance requires role and peer identity")
        return
    if event == "handoff.completed":
        if status not in {"destination_ready", "handoff_pending"} or not state.get("destination_ready") or not state.get("handoff_accepted"):
            raise WorkflowError("management handoff completion requires target acceptance and readiness")
        if payload.get("management_confirmed") is not True:
            raise WorkflowError("handoff completion requires management confirmation")
        return
    if event in {"source-session.delete_requested", "source-session.removal_requested"}:
        if status != "handoff_complete" or not state.get("handoff_complete"):
            raise WorkflowError("source removal requires management-confirmed handoff completion")
        validate_delete_target(payload.get("sourceSessionId"), payload.get("targetSessionId"), payload.get("currentSessionId"))
        if payload.get("policyEnabled") is not True or not state.get("migration_policy_authorized") or payload.get("removalTool") is None:
            raise WorkflowError("source removal requires enabled policy and a real delete/archive capability")
        if payload.get("capabilityMode") not in {"native_delete", "native_archive"}:
            raise WorkflowError("manual removal cannot be recorded as automatic removal")
        if payload.get("removalMode") not in {"delete", "archive"}:
            raise WorkflowError("source removal mode is invalid")
        return
    if event == "source-session.removal_failed":
        if status != "handoff_complete" or not state.get("source_removal_requested"):
            raise WorkflowError("source removal failure requires a precise prior request")
        if payload.get("success") is not False or payload.get("sourceSessionId") != state.get("source_session_id"):
            raise WorkflowError("source removal failure identity or result is invalid")
        return
    if event == "source-session.removal_blocked":
        if status != "handoff_complete" or not state.get("handoff_complete"):
            raise WorkflowError("manual source-removal guidance requires management-confirmed handoff")
        validate_delete_target(payload.get("sourceSessionId"), payload.get("targetSessionId"), payload.get("currentSessionId"))
        if not payload.get("result"):
            raise WorkflowError("manual source-removal result is required")
        return
    if event in {"source-session.deleted", "source-session.removed"}:
        if status != "handoff_complete" or not state.get("source_removal_requested"):
            raise WorkflowError("source removal result requires a precise prior request")
        if payload.get("success") is not True or payload.get("sourceSessionId") != state.get("source_session_id"):
            raise WorkflowError("source removal failed or target identity changed")
        if payload.get("removalMode") not in {"delete", "archive"}:
            raise WorkflowError("source removal result needs delete or archive mode")
        return
    if event == "dispatch.requested":
        if not state.get("destination_ready"):
            raise WorkflowError("dispatch requires a validated destination readiness receipt")
        if status not in {"planning", "destination_ready", "accepted", "correction"}:
            raise WorkflowError("dispatch cannot start from current state")
        return
    if event == "execution.started":
        if status not in {"dispatched", "correction"}:
            raise WorkflowError("execution must start after dispatch")
        return
    if event == "execution.reported":
        if status != "executing" or not _nonempty(payload.get("report_ref")):
            raise WorkflowError("execution report requires executing state and report_ref")
        return
    if event == "review.correction_requested":
        if status != "reviewing":
            raise WorkflowError("correction requires a report under review")
        return
    if event == "review.accepted":
        if status != "reviewing" or not state.get("execution_report_ref"):
            raise WorkflowError("independent review requires an execution report")
        if not payload.get("independent") or payload.get("checkpoint"):
            raise WorkflowError("checkpoint cannot be accepted as final")
        return
    if event == "completion.requested":
        if status != "accepted" or not state.get("independent_acceptance"):
            raise WorkflowError("completion requires independent acceptance")
        if not state.get("destination_ready"):
            raise WorkflowError("completion requires destination readiness")
        return
    if event == "blocked":
        if status == "complete":
            raise WorkflowError("complete tasks cannot be blocked")
        return
    if event == "unblocked":
        if status != "blocked":
            raise WorkflowError("only blocked tasks can be unblocked")
        return
    if event == "retention.dry_run":
        eligible = state.get("handoff_complete") or (status == "complete" and state.get("independent_acceptance"))
        if not eligible or state.get("retention_dry_run_done"):
            raise WorkflowError("retention dry-run is not currently eligible")
        if not isinstance(payload.get("candidates"), list):
            raise WorkflowError("retention dry-run requires a candidate list")
        return
    if event == "retention.applied":
        eligible = state.get("handoff_complete") or (status == "complete" and state.get("independent_acceptance"))
        if not eligible or not state.get("retention_dry_run_done") or state.get("retention_applied"):
            raise WorkflowError("retention apply is not currently eligible")
        if not isinstance(payload.get("deleted"), list):
            raise WorkflowError("retention apply requires a deletion list")
        return


class WorkflowStore:
    def __init__(self, project_root: str | Path, output_root: str | Path = ".agent-workflow") -> None:
        self.project_root = Path(project_root).expanduser().resolve()
        self.root = resolve_output_root(self.project_root, output_root)
        self.events_path = self.root / "state" / "events.jsonl"

    def _ensure_initialized(self) -> None:
        if not (self.root / "config.json").exists() or not self.events_path.parent.exists():
            raise WorkflowError("workflow root is not initialized; run init first")

    def _contract_path(self, task_id: str) -> Path:
        return self.root / "contracts" / f"{task_id}.json"

    def _task_state_path(self, task_id: str) -> Path:
        return self.root / "state" / "tasks" / f"{task_id}.json"

    def generation(self) -> int:
        config = _json_read(self.root / "config.json")
        return int(config.get("generation", 1))

    def contract(self, task_id: str) -> dict[str, Any]:
        self._ensure_initialized()
        path = self._contract_path(task_id)
        if not path.exists():
            raise WorkflowError(f"unknown task: {task_id}")
        return validate_contract(_json_read(path))

    def events(self, task_id: str) -> list[dict[str, Any]]:
        self._ensure_initialized()
        if not self.events_path.exists():
            return []
        result: list[dict[str, Any]] = []
        for line in self.events_path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            event = _json_read_line(line)
            if event.get("task_id") == task_id:
                result.append(event)
        return result

    def _replay(self, task_id: str, events: list[dict[str, Any]] | None = None) -> dict[str, Any]:
        contract = self.contract(task_id)
        records = self.events(task_id) if events is None else events
        if not records:
            raise WorkflowError("task has no event log")
        state = _state_from_events([], contract)
        for position, event_record in enumerate(records, start=1):
            if not isinstance(event_record, dict):
                raise WorkflowError("event record is not an object")
            if event_record.get("task_id") != task_id:
                raise WorkflowError("event task_id does not match task")
            if event_record.get("id") != position:
                raise WorkflowError("event IDs are not continuous")
            if position == 1 and event_record.get("event") != "contract.created":
                raise WorkflowError("contract.created must be first")
            payload = event_record.get("payload") or {}
            if not isinstance(payload, dict):
                raise WorkflowError("event payload must be an object")
            _validate_event(state, event_record.get("event"), payload, event_record.get("actor"))
            state = _state_from_events(records[:position], contract)
        return state

    def state(self, task_id: str) -> dict[str, Any]:
        return self._replay(task_id)

    def _append(self, task_id: str, event: str, payload: dict[str, Any], actor: str) -> dict[str, Any]:
        contract = self.contract(task_id)
        state = self.state(task_id)
        _validate_event(state, event, payload, actor)
        self.events_path.parent.mkdir(parents=True, exist_ok=True)
        record = {
            "id": state["event_count"] + 1,
            "timestamp": utc_now(),
            "task_id": task_id,
            "event": event,
            "actor": actor,
            "payload": redact_json(payload),
        }
        with self.events_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")
        snapshot = self.state(task_id)
        state_path = self._task_state_path(task_id)
        _json_write(state_path, snapshot)
        self.register_artifact(task_id, state_path, "status-snapshot", self.generation(), canonical=True)
        self.register_artifact(task_id, self.events_path, "event-log", self.generation(), canonical=True)
        return snapshot

    def create_contract(self, contract: dict[str, Any], actor: str = "management") -> dict[str, Any]:
        self._ensure_initialized()
        contract = validate_contract(dict(contract))
        path = self._contract_path(contract["task_id"])
        if path.exists():
            raise WorkflowError("task contract already exists")
        # Validate the first event before persisting the immutable contract.
        # Otherwise an invalid actor can leave an unrecoverable half-created
        # task with a contract but no authoritative event stream.
        _validate_event(_state_from_events([], contract), "contract.created", {}, actor)
        _json_write(path, contract)
        self._append_new(contract["task_id"], actor)
        self.register_artifact(contract["task_id"], path, "contract", self.generation(), canonical=True)
        return self.state(contract["task_id"])

    def _append_new(self, task_id: str, actor: str) -> None:
        self.events_path.parent.mkdir(parents=True, exist_ok=True)
        contract = self.contract(task_id)
        _validate_event(_state_from_events([], contract), "contract.created", {}, actor)
        record = {"id": 1, "timestamp": utc_now(), "task_id": task_id, "event": "contract.created", "actor": actor, "payload": {}}
        with self.events_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")
        state_path = self._task_state_path(task_id)
        _json_write(state_path, self.state(task_id))
        self.register_artifact(task_id, state_path, "status-snapshot", self.generation(), canonical=True)
        self.register_artifact(task_id, self.events_path, "event-log", self.generation(), canonical=True)

    def plan(self, task_id: str, steps: list[str], actor: str = "management") -> dict[str, Any]:
        if not steps or any(not _nonempty(step) for step in steps):
            raise WorkflowError("plan steps must be non-empty")
        return self._append(task_id, "plan.created", {"steps": steps}, actor)

    def authorize_migration(self, task_id: str, source_session_id: str, target_session_id: str, current_session_id: str, actor: str = "management") -> dict[str, Any]:
        return self._append(task_id, "migration.authorized", {
            "userConfirmed": True,
            "policyEnabled": True,
            "sourceSessionId": source_session_id,
            "targetSessionId": target_session_id,
            "currentSessionId": current_session_id,
        }, actor)

    def bootstrap(self, task_id: str, destination_role: str, destination_id: str, peer_identity: str, install_source: str, capability_mode: str, actor: str = "management") -> dict[str, Any]:
        if destination_role not in ROLES or not _nonempty(destination_id) or not _nonempty(peer_identity):
            raise WorkflowError("bootstrap requires destination role, destination id, and peer identity")
        packet = make_bootstrap_packet(destination_role, destination_id, peer_identity, install_source, capability_mode)
        return self._append(task_id, "bootstrap.requested", {"packet": packet}, actor)

    def destination_ready(self, task_id: str, receipt: dict[str, Any], expected_role: str | None = None, expected_destination_id: str | None = None, actor: str = "execution") -> dict[str, Any]:
        return self._append(task_id, "destination.ready", {"receipt": receipt, "expected_role": expected_role, "expected_destination_id": expected_destination_id}, actor)

    def handoff_request(self, task_id: str, packet: dict[str, Any], actor: str = "management") -> dict[str, Any]:
        return self._append(task_id, "handoff.requested", {"packet": packet}, actor)

    def export_code_handoff(
        self,
        task_id: str,
        continuity: dict[str, Any],
        source_session_id: str,
        destination_session_id: str,
        source_role: str,
        destination_role: str,
        management_peer: str,
        execution_peer: str,
        capability_mode: str,
        actor: str = "management",
    ) -> dict[str, Any]:
        state = self.snapshot(task_id)
        if not state.get("destination_ready"):
            raise WorkflowError("code handoff export requires destination readiness")
        if state.get("destination_id") != destination_session_id:
            raise WorkflowError("code handoff destination differs from readiness receipt")
        try:
            normalized_continuity = validate_continuity_context(redact_json(continuity))
            bundle = build_code_handoff_bundle(
                skill_name=SKILL_NAME,
                skill_version=SKILL_VERSION,
                contract=redact_json(self.contract(task_id)),
                snapshot=redact_json(state),
                events=redact_json(self.events(task_id)),
                continuity=normalized_continuity,
                source_session_id=source_session_id,
                destination_session_id=destination_session_id,
                source_role=source_role,
                destination_role=destination_role,
                management_peer=management_peer,
                execution_peer=execution_peer,
                capability_mode=capability_mode,
            )
        except HandoffBundleError as exc:
            raise WorkflowError(str(exc)) from exc
        path = self.root / "handoffs" / f"{task_id}-code-handoff.json"
        _validate_ref(path, self.root)
        _json_write(path, bundle)
        self.register_artifact(task_id, path, "code-handoff", self.generation(), canonical=True)
        manual_relay_path: Path | None = None
        if capability_mode == "manual":
            manual_relay_path = self.root / "handoffs" / f"{task_id}-manual-code-relay.md"
            _validate_ref(manual_relay_path, self.root)
            manual_relay_path.write_text(
                "\n".join(
                    [
                        "# Manual code-handoff transport",
                        "",
                        "This file is transport guidance, not workflow authority. Do not reconstruct state by reading legacy or project Markdown.",
                        "Install and validate the exact Skill version first, then run the code receiver below in the destination session:",
                        "",
                        "```text",
                        f"python scripts/uaw.py handoff-receive --project-root <project> --output-root <controlled-root> --task-id {task_id} --bundle-file \"{path}\" --expected-destination-id {destination_session_id} --expected-role {destination_role}",
                        "```",
                        "",
                        "Return the JSON receipt with status CODE_HANDOFF_ACCEPTED. The receiver must report externalReadsRequired=false.",
                    ]
                )
                + "\n",
                encoding="utf-8",
            )
            self.register_artifact(task_id, manual_relay_path, "manual-code-relay", self.generation(), canonical=True)
        updated = self.handoff_request(task_id, bundle, actor=actor)
        result = {
            "ok": True,
            "packetType": "uaw-code-handoff-export",
            "bundlePath": str(path),
            "bundle": bundle,
            "snapshot": updated,
            "externalReadsRequired": False,
        }
        if manual_relay_path is not None:
            result["manualRelayPath"] = str(manual_relay_path)
        return result

    def receive_code_handoff(
        self,
        task_id: str,
        bundle_file: str | Path,
        expected_destination_id: str,
        expected_role: str,
        actor: str = "execution",
    ) -> dict[str, Any]:
        bundle_path = Path(bundle_file).expanduser().resolve()
        _validate_ref(bundle_path, self.root)
        if not bundle_path.is_file():
            raise WorkflowError("code handoff bundle does not exist")
        try:
            bundle = _json_read(bundle_path)
            receipt = receive_code_handoff_bundle(
                bundle,
                expected_skill_name=SKILL_NAME,
                expected_skill_version=SKILL_VERSION,
                expected_destination_id=expected_destination_id,
                expected_role=expected_role,
            )
        except (HandoffBundleError, json.JSONDecodeError) as exc:
            raise WorkflowError(str(exc)) from exc
        if receipt.get("taskId") != task_id:
            raise WorkflowError("received code handoff task mismatch")
        if bundle.get("contract") != self.contract(task_id):
            raise WorkflowError("received code handoff contract differs from local task contract")
        local_events = self.events(task_id)
        if not local_events or local_events[-1].get("event") != "handoff.requested":
            raise WorkflowError("received code handoff has no matching local request")
        if bundle.get("events") != local_events[:-1]:
            raise WorkflowError("received code handoff history differs from local event history")
        receipt_path = self.root / "handoffs" / f"{task_id}-code-handoff-receipt-{expected_destination_id}.json"
        _validate_ref(receipt_path, self.root)
        _json_write(receipt_path, receipt)
        self.register_artifact(task_id, receipt_path, "code-handoff-receipt", self.generation(), canonical=True)
        updated = self._append(task_id, "handoff.bundle_received", {"receipt": receipt}, actor)
        return {
            "ok": True,
            "packetType": receipt["packetType"],
            "receiptPath": str(receipt_path),
            "receipt": receipt,
            "snapshot": updated,
            "externalReadsRequired": False,
        }

    def handoff_accept(self, task_id: str, role: str, peer_identity: str, actor: str = "execution") -> dict[str, Any]:
        return self._append(task_id, "handoff.accepted", {"role": role, "peer_identity": peer_identity}, actor)

    def handoff_complete(self, task_id: str, actor: str = "management") -> dict[str, Any]:
        return self._append(task_id, "handoff.completed", {"management_confirmed": True}, actor)

    def request_source_delete(self, task_id: str, source_session_id: str, target_session_id: str, current_session_id: str, capability_inventory: Any, policy_enabled: bool, actor: str = "management") -> dict[str, Any]:
        state = self.state(task_id)
        if state["status"] != "handoff_complete":
            raise WorkflowError("source removal requires management-confirmed handoff completion")
        contract_policy_enabled = bool(self.contract(task_id).get("migration_policy", {}).get("enabled", False))
        capability = classify_delete_capability(capability_inventory)
        effective_policy = bool(state.get("migration_policy_authorized") or (policy_enabled is True and contract_policy_enabled))
        try:
            action = build_delete_action(source_session_id, target_session_id, current_session_id, capability, effective_policy)
        except ValueError as exc:
            raise WorkflowError(str(exc)) from exc
        if not action["ok"]:
            if action.get("status") == "MANUAL_SESSION_REMOVAL_REQUIRED":
                action["auditState"] = self._append(task_id, "source-session.removal_blocked", {
                    "sourceSessionId": source_session_id,
                    "targetSessionId": target_session_id,
                    "currentSessionId": current_session_id,
                    "result": action["status"],
                    "capabilityMode": capability["mode"],
                    "archiveTool": capability.get("archiveTool"),
                }, actor)
            return action
        updated = self._append(task_id, "source-session.removal_requested", {
            "sourceSessionId": source_session_id,
            "targetSessionId": target_session_id,
            "currentSessionId": current_session_id,
            "policyEnabled": effective_policy,
            "capabilityMode": capability["mode"],
            "removalTool": action["tool"],
            "removalMode": action["removalMode"],
            "args": action.get("args", {}),
        }, actor)
        updated["hostAction"] = action
        updated["result"] = "host_action_ready"
        return updated

    def record_source_deleted(self, task_id: str, source_session_id: str, success: bool, actor: str = "host") -> dict[str, Any]:
        state = self.state(task_id)
        if not state.get("source_removal_requested"):
            raise WorkflowError("source removal was not requested")
        if not success:
            return self._append(task_id, "source-session.removal_failed", {"sourceSessionId": source_session_id, "success": False}, actor)
        return self._append(task_id, "source-session.removed", {"sourceSessionId": source_session_id, "success": True, "removalMode": state.get("source_removal_mode")}, actor)

    def request_source_removal(self, task_id: str, source_session_id: str, target_session_id: str, current_session_id: str, capability_inventory: Any, policy_enabled: bool, actor: str = "management") -> dict[str, Any]:
        return self.request_source_delete(task_id, source_session_id, target_session_id, current_session_id, capability_inventory, policy_enabled, actor)

    def record_source_removed(self, task_id: str, source_session_id: str, success: bool, actor: str = "host") -> dict[str, Any]:
        return self.record_source_deleted(task_id, source_session_id, success, actor)

    def dispatch(self, task_id: str, actor: str = "management", packet_ref: str | None = None) -> dict[str, Any]:
        return self._append(task_id, "dispatch.requested", {"packet_ref": packet_ref}, actor)

    def start_execution(self, task_id: str, actor: str = "execution") -> dict[str, Any]:
        return self._append(task_id, "execution.started", {}, actor)

    def report(self, task_id: str, report_ref: str | None = None, report_text: str | None = None, actor: str = "execution") -> dict[str, Any]:
        if report_text and not report_ref:
            report_path = self.root / "reports" / f"{task_id}-execution.md"
            _validate_ref(report_path, self.root)
            report_path.write_text(redact_text(report_text), encoding="utf-8")
            report_ref = str(report_path.relative_to(self.root))
            self.register_artifact(task_id, report_path, "report", self.generation(), canonical=True)
        if not report_ref:
            raise WorkflowError("report requires report_ref or report_text")
        _validate_ref(self.root / report_ref, self.root)
        return self._append(task_id, "execution.reported", {"report_ref": report_ref}, actor)

    def review(self, task_id: str, decision: str, actor: str = "management", independent: bool = False, checkpoint: bool = False, reason: str | None = None) -> dict[str, Any]:
        if decision == "accepted":
            return self._append(task_id, "review.accepted", {"independent": independent, "checkpoint": checkpoint}, actor)
        if decision == "correction":
            return self._append(task_id, "review.correction_requested", {"reason": reason or "correction requested", "checkpoint": checkpoint}, actor)
        if decision == "blocked":
            return self._append(task_id, "blocked", {"reason": reason or "blocked"}, actor)
        raise WorkflowError("unknown review decision")

    def complete(self, task_id: str, actor: str = "management") -> dict[str, Any]:
        return self._append(task_id, "completion.requested", {}, actor)

    def transition(self, task_id: str, event: str, payload: dict[str, Any], actor: str = "system") -> dict[str, Any]:
        return self._append(task_id, event, payload, actor)

    def snapshot(self, task_id: str) -> dict[str, Any]:
        state = self.state(task_id)
        state["retention"] = retention_summary(self.root)
        return state

    def register_artifact(self, task_id: str, path: str | Path, kind: str, generation: int, canonical: bool = False, previous: bool = False, ephemeral: bool = False, retained: bool = False) -> dict[str, Any]:
        self.contract(task_id)
        return register_retention_artifact(self.root, path, kind, generation, canonical=canonical, previous=previous, ephemeral=ephemeral, retained=retained)

    def cleanup(self, task_id: str, git_confirmed: bool, apply: bool = False) -> dict[str, Any]:
        state = self.state(task_id)
        if not (state.get("handoff_complete") or (state["status"] == "complete" and state.get("independent_acceptance"))):
            raise WorkflowError("cleanup requires management-confirmed handoff or independently accepted complete task")
        return cleanup_artifacts(self.root, git_confirmed=git_confirmed, apply=apply)

    def retention_dry_run(self, task_id: str, git_confirmed: bool, actor: str = "management") -> dict[str, Any]:
        state = self.state(task_id)
        if not (state.get("handoff_complete") or (state["status"] == "complete" and state.get("independent_acceptance"))):
            raise WorkflowError("retention requires management-confirmed handoff or independently accepted complete task")
        result = cleanup_artifacts(self.root, git_confirmed=git_confirmed, apply=False)
        updated = self._append(task_id, "retention.dry_run", {"candidates": result["delete"], "keep": result["keep"]}, actor)
        updated["retentionPlan"] = result
        return updated

    def retention_apply(self, task_id: str, git_confirmed: bool, actor: str = "management") -> dict[str, Any]:
        state = self.state(task_id)
        if not state.get("retention_dry_run_done"):
            raise WorkflowError("retention apply requires a preceding dry-run")
        result = cleanup_artifacts(self.root, git_confirmed=git_confirmed, apply=True)
        updated = self._append(task_id, "retention.applied", {"deleted": result["deleted"], "keep": result["keep"]}, actor)
        updated["retentionResult"] = result
        return updated

    def rotate_retention(self, task_id: str, generation: int) -> dict[str, Any]:
        self.contract(task_id)
        result = rotate_generations(self.root, generation)
        config_path = self.root / "config.json"
        config = _json_read(config_path)
        config["generation"] = generation
        _json_write(config_path, config)
        return result

    def migration_delete_capability(self, inventory: Any) -> dict[str, Any]:
        return classify_delete_capability(inventory)

    def audit(self, task_id: str) -> dict[str, Any]:
        events = self.events(task_id)
        errors: list[str] = []
        state: dict[str, Any] = {"status": "intake", "event_count": 0, "destination_ready": False, "handoff_complete": False, "handoff_accepted": False, "source_removal_requested": False, "independent_acceptance": False, "retention_dry_run_done": False, "retention_applied": False, "contract": self.contract(task_id)}
        for position, event in enumerate(events, start=1):
            try:
                if event.get("task_id") != task_id:
                    raise WorkflowError("event task_id does not match task")
                if event.get("id") != position:
                    raise WorkflowError("event IDs are not continuous")
                if position == 1 and event.get("event") != "contract.created":
                    raise WorkflowError("contract.created must be first")
                payload = event.get("payload") or {}
                _validate_event(state, event.get("event"), payload, event.get("actor"))
                state = _state_from_events(events[:position], state["contract"])
                for key in ("report_ref", "packet_ref"):
                    if payload.get(key):
                        _validate_ref(self.root / payload[key], self.root)
            except WorkflowError as exc:
                errors.append(f"event {position}: {exc}")
                break
        if not events:
            errors.append("contract.created must be first")
        if state.get("status") == "complete" and not state.get("independent_acceptance"):
            errors.append("complete without independent acceptance")
        return {"ok": not errors, "task_id": task_id, "status": state["status"], "event_count": len(events), "errors": errors, "retention": retention_summary(self.root)}


def _json_read_line(line: str) -> dict[str, Any]:
    return _as_dict(json.loads(line), "event")


def redact_json(value: Any) -> Any:
    if isinstance(value, str):
        return redact_text(value)
    if isinstance(value, list):
        return [redact_json(item) for item in value]
    if isinstance(value, dict):
        return {key: redact_json(item) for key, item in value.items()}
    return value


def _validate_ref(path: Path, root: Path) -> None:
    resolved = path.resolve()
    if not _inside(resolved, root.resolve()):
        raise WorkflowError("artifact reference escapes controlled output root")


def detect_handoff_intent(message: str) -> bool:
    text = str(message).lower()
    if "继续下一节" in text or "continue next section" in text:
        return False
    patterns = ("交接", "迁移", "换会话", "新会话", "换个对话", "handoff", "transfer", "switch conversation", "new session")
    return any(pattern in text for pattern in patterns)


def _valid_relay(relay: Any) -> bool:
    if not isinstance(relay, dict):
        return False
    required = ("packetType", "taskId", "contractRef", "objective", "currentState", "nextAction", "managementPeer", "executionPeer")
    if any(not _nonempty(relay.get(key)) for key in required):
        return False
    if relay.get("packetType") != "management-manual-relay":
        return False
    if relay.get("authorization") is not True or relay.get("redacted") is not True:
        return False
    if relay.get("skillName") != SKILL_NAME or relay.get("skillVersion") != SKILL_VERSION:
        return False
    return True


def route_request(role: str, message: str, relay: dict[str, Any] | None = None) -> dict[str, Any]:
    if role not in ROLES:
        raise WorkflowError("invalid role")
    if role == "execution" and relay is None:
        return {"ok": True, "route": "REDIRECT_TO_MANAGEMENT", "message": "请将业务目标交给管理层，由管理层形成有效任务合同后再转发。"}
    if role == "execution" and isinstance(relay, dict) and relay.get("packetType") == "uaw-code-handoff":
        try:
            validation = validate_code_handoff_bundle(relay, expected_skill_name=SKILL_NAME, expected_skill_version=SKILL_VERSION, expected_role="execution")
        except HandoffBundleError as exc:
            return {"ok": False, "route": "REJECT_INVALID_CODE_HANDOFF", "reason": str(exc)}
        return {"ok": True, "route": "ACCEPT_CODE_HANDOFF", "task_id": validation["taskId"], "peer": relay["peers"]["management"], "externalReadsRequired": False}
    if role == "execution" and not _valid_relay(relay):
        return {"ok": False, "route": "REJECT_INVALID_MANAGEMENT_RELAY", "reason": "management manual relay packet is incomplete or unauthorized"}
    if role == "execution":
        return {"ok": True, "route": "ACCEPT_MANAGEMENT_RELAY", "task_id": relay["taskId"], "peer": relay["managementPeer"]}
    return {"ok": True, "route": "DIRECT_MANAGEMENT_INTAKE"}


def next_action(snapshot: dict[str, Any], capability_inventory: Any | None = None) -> dict[str, Any]:
    status = snapshot.get("status")
    if status == "blocked":
        return {"action": "stop", "reason": status}
    if status in {"complete", "deleted", "removed"}:
        retention = snapshot.get("retention", {})
        if not snapshot.get("retention_dry_run_done"):
            if retention.get("candidates"):
                return {"action": "retention_dry_run", "sequence": ["retention_dry_run", "retention_apply", "stop"], "candidates": retention["candidates"]}
            return {"action": "stop", "reason": "retention has no deletable candidates"}
        if not snapshot.get("retention_applied") and retention.get("candidates"):
            return {"action": "retention_apply", "sequence": ["retention_apply", "stop"]}
        return {"action": "stop", "reason": status}
    if not snapshot.get("destination_ready"):
        probe = probe_capabilities(capability_inventory or [])
        return {
            "action": "bootstrap_destination",
            "mode": probe["mode"],
            "sequence": ["create_or_select_destination", "send_bootstrap_packet", "wait_readiness_receipt", "validate_receipt", "send_handoff_task"],
            "missing": probe["missing"],
        }
    if status == "bootstrap_pending":
        return {"action": "wait_readiness_receipt", "sequence": ["wait_readiness_receipt", "validate_receipt", "send_handoff_task"]}
    if status == "handoff_pending":
        return {"action": "validate_handoff_acceptance", "sequence": ["validate_handoff_acceptance", "dispatch_business_task"]}
    if status == "handoff_complete":
        if snapshot.get("migration_policy_authorized"):
            probe = probe_capabilities(capability_inventory or [])
            removal = probe.get("sourceSessionRemoval", {})
            return {
                "action": "remove_source_session_if_authorized",
                "sequence": ["probe_thread_delete_or_archive", "validate_source_target_ids", "execute_one_host_action", "record_result"],
                "capabilityMode": removal.get("mode"),
                "tool": removal.get("deleteTool") or removal.get("archiveTool"),
                "archiveFallback": removal.get("mode") == "native_archive",
            }
        if snapshot.get("retention", {}).get("candidates"):
            return {"action": "retention_dry_run", "sequence": ["retention_dry_run", "retention_apply", "stop"], "candidates": snapshot["retention"]["candidates"]}
        return {"action": "stop", "reason": "retention has no deletable candidates"}
    if status in {"planning", "destination_ready", "correction"}:
        return {"action": "dispatch_business_task"}
    if status == "dispatched":
        return {"action": "start_execution"}
    if status == "executing":
        return {"action": "submit_execution_report"}
    if status == "reviewing":
        return {"action": "independent_review"}
    if status == "accepted":
        return {"action": "complete_task"}
    return {"action": "inspect", "status": status}


def render_status(snapshot: dict[str, Any], audience: str = "management") -> str:
    status = snapshot.get("status", "unknown")
    task_id = snapshot.get("task_id", "unknown")
    retention = snapshot.get("retention", {})
    if audience == "user":
        return f"任务 {task_id} 当前状态：{status}。"
    return "\n".join(
        [
            f"task: {task_id}",
            f"status: {status}",
            f"destination_ready: {bool(snapshot.get('destination_ready'))}",
            f"execution_report: {snapshot.get('execution_report_ref') or 'none'}",
            f"independent_acceptance: {bool(snapshot.get('independent_acceptance'))}",
            f"retention: {retention.get('ok', True)}; candidates={len(retention.get('candidates', []))}",
        ]
    )


def build_handoff_packet(
    snapshot: dict[str, Any],
    message: str,
    mode: str,
    receipt: dict[str, Any] | None = None,
    capability_inventory: Any | None = None,
) -> dict[str, Any]:
    contract = snapshot.get("contract", {})
    packet = {
        "packetType": "management-manual-relay" if mode == "manual" else "native-handoff-plan",
        "skillName": SKILL_NAME,
        "skillVersion": SKILL_VERSION,
        "taskId": snapshot.get("task_id"),
        "contractRef": f"contracts/{snapshot.get('task_id')}.json",
        "role": contract.get("destination_role", "execution"),
        "managementPeer": contract.get("management_peer", "management"),
        "executionPeer": contract.get("execution_peer", "execution"),
        "currentState": snapshot.get("status"),
        "objective": contract.get("objective"),
        "nextAction": next_action(snapshot, capability_inventory),
        "message": redact_text(message),
        "authorization": True,
        "redacted": True,
        "migrationPolicy": contract.get("migration_policy", {"enabled": False}),
    }
    if receipt:
        packet["destinationReceipt"] = {key: receipt.get(key) for key in ("skillName", "skillVersion", "role", "capabilityMode", "destinationId", "stableSessionId", "ready")}
    return packet


def write_manual_handoff(store: WorkflowStore, snapshot: dict[str, Any], message: str) -> dict[str, Any]:
    task_id = snapshot["task_id"]
    path = store.root / "handoffs" / f"{task_id}-handoff.md"
    packet = build_handoff_packet(snapshot, message, "manual")
    content = "\n".join(
        [
            "# Managed handoff",
            "",
            "This Markdown is only user-forwardable transport guidance. The workflow authority is the Skill code, runtime policy, event state and later JSON handoff bundle.",
            "Do not ask the destination to reconstruct project state from Markdown files.",
            "",
            "## Step 0: run code-backed destination bootstrap",
            f"- Skill: `{SKILL_NAME}` version `{SKILL_VERSION}`",
            "- Install or resolve the exact version in the destination session; do not assume a machine-global install is active.",
            f"- Run: `python scripts/uaw.py destination-bootstrap --skill-dir <installed-skill> --role {packet['role']} --destination-id <destination-id> --peer-identity {packet['managementPeer']} --inventory-file <tool-inventory.json>`.",
            "- Return a readiness receipt containing skillName, skillVersion, role, install/resolve path or provider, selftest status, quick-validate status, capability mode, stable session ID when available, peer identity, and ready=true.",
            "- Automatic cross-session tools are unavailable in this manual packet; do not claim that a session was created, messaged, waited on, or verified.",
            "",
            "## Step 1: bind roles and peers",
            f"- management peer: `{packet['managementPeer']}`; execution peer: `{packet['executionPeer']}`; destination role: `{packet['role']}`",
            "",
            "## Step 2: return the code-generated receipt",
            "Management will then export a self-contained JSON handoff bundle and generate a second relay containing the exact handoff-receive command.",
            "Do not accept a free-form business prompt or external Markdown reading list in place of that bundle.",
        ]
    )
    _validate_ref(path, store.root)
    path.write_text(content, encoding="utf-8")
    store.register_artifact(task_id, path, "handoff", store.generation(), canonical=True)
    return {"ok": True, "mode": "manual", "path": str(path), "packet": packet}


def prepare_handoff(store: WorkflowStore, task_id: str, message: str, confirmed: bool = False, capabilities: Any | None = None) -> dict[str, Any]:
    if not detect_handoff_intent(message):
        return {"ok": True, "needed": False, "message": "ordinary continuation does not trigger migration"}
    snapshot = store.snapshot(task_id)
    if not confirmed:
        return {"ok": True, "needed": True, "confirmation_required": True, "message": "请确认是否创建/选定新会话并部署同版本 Skill。"}
    probe = probe_capabilities(capabilities or [])
    if probe["native"]:
        packet = build_handoff_packet(snapshot, message, "native", capability_inventory=capabilities)
        return {
            "ok": True,
            "needed": True,
            "mode": "native",
            "destination_ready": False,
            "host_actions": [
                "create_or_select_destination",
                "send_bootstrap_packet",
                "wait_readiness_receipt",
                "validate_readiness_receipt",
                "export_code_handoff_bundle",
                "send_handoff_receive_command",
                "wait_code_handoff_receipt",
            ],
            "bootstrap_packet": make_bootstrap_packet(snapshot["contract"].get("destination_role", "execution"), "destination-session", "management", "configured-install", "native"),
            "packet": packet,
            "missing": [],
            "externalReadsRequired": False,
        }
    manual = write_manual_handoff(store, snapshot, message)
    manual["missing"] = probe["missing"]
    manual["host_actions"] = ["user_deploys_skill", "user_runs_validation", "user_returns_readiness_receipt", "management_accepts_handoff"]
    return manual


def validate_install(skill_dir: str | Path) -> dict[str, Any]:
    root = Path(skill_dir).expanduser().resolve()
    required = [
        root / "VERSION",
        root / "SKILL.md",
        root / "agents" / "openai.yaml",
        root / "scripts" / "workflow_engine.py",
        root / "scripts" / "bootstrap.py",
        root / "scripts" / "retention.py",
        root / "scripts" / "session_migration.py",
        root / "scripts" / "handoff_bundle.py",
        root / "scripts" / "workflow_policy.py",
        root / "scripts" / "source_policy_compiler.py",
        root / "assets" / "workflow-policy.json",
        root / "scripts" / "uaw.py",
        root / "references" / "contract-schema.md",
        root / "references" / "protocol.md",
    ]
    missing = [str(path) for path in required if not path.exists()]
    errors: list[str] = []
    skill_text = (root / "SKILL.md").read_text(encoding="utf-8") if (root / "SKILL.md").exists() else ""
    if (root / "VERSION").exists() and (root / "VERSION").read_text(encoding="utf-8").strip() != SKILL_VERSION:
        errors.append("package VERSION does not match runtime Skill version")
    if skill_text.count("\n") + 1 > 500:
        errors.append("SKILL.md exceeds 500 lines")
    if "[TODO" in skill_text:
        errors.append("SKILL.md still contains TODO")
    if not skill_text.startswith("---\nname: universal-agent-workflow"):
        errors.append("SKILL.md frontmatter name is invalid")
    if (root / "agents" / "openai.yaml").exists():
        yaml_text = (root / "agents" / "openai.yaml").read_text(encoding="utf-8")
        if "$universal-agent-workflow" not in yaml_text:
            errors.append("openai.yaml default_prompt does not name the skill")
    policy_path = root / "assets" / "workflow-policy.json"
    if policy_path.exists():
        try:
            validate_policy(_json_read(policy_path), SKILL_VERSION)
        except (WorkflowPolicyError, json.JSONDecodeError) as exc:
            errors.append(str(exc))
    return {"ok": not missing and not errors, "skillVersion": SKILL_VERSION, "missing": missing, "errors": errors}


def run_selftest() -> dict[str, Any]:
    checks: dict[str, bool] = {}
    packaged_policy = load_policy(Path(__file__).resolve().parents[1], SKILL_VERSION)
    policy_validation = validate_policy(packaged_policy, SKILL_VERSION)
    checks["runtime_policy"] = policy_validation["ok"] and policy_validation["externalMarkdownRequired"] is False
    with tempfile.TemporaryDirectory(prefix="uaw-selftest-") as temp:
        project = Path(temp) / "project"
        project.mkdir()
        root = project / ".agent-workflow"
        initialize_project(project, root)
        store = WorkflowStore(project, root)
        contract = make_contract("demo", "Demo", "exercise the governed workflow", complexity="complex", plan_steps=["plan", "execute", "review"])
        store.create_contract(contract)
        store.plan("demo", contract["plan_steps"])
        try:
            store.dispatch("demo")
            checks["dispatch_without_receipt_rejected"] = False
        except WorkflowError:
            checks["dispatch_without_receipt_rejected"] = True
        store.bootstrap("demo", "execution", "dest-1", "management", "local-install", "native")
        receipt = {
            "skillName": SKILL_NAME,
            "skillVersion": SKILL_VERSION,
            "role": "execution",
            "installPath": "local-install",
            "selftestStatus": "passed",
            "quickValidateStatus": "passed",
            "capabilityMode": "native",
            "destinationId": "dest-1",
            "stableSessionId": "session-1",
            "peerIdentity": "execution",
            "runtimeAuthority": "code-state",
            "externalReadsRequired": False,
            "validationSource": "destination-bootstrap",
            "policyStatus": "passed",
            "policyVersion": SKILL_VERSION,
            "policyRuleCount": 1,
            "ready": True,
        }
        store.destination_ready("demo", receipt, expected_role="execution", expected_destination_id="dest-1")
        store.dispatch("demo")
        store.start_execution("demo")
        store.report("demo", report_ref="reports/demo.md")
        try:
            store.review("demo", "accepted", independent=True, checkpoint=True)
            checks["checkpoint_not_final"] = False
        except WorkflowError:
            checks["checkpoint_not_final"] = True
        store.review("demo", "accepted", independent=True)
        store.complete("demo")
        checks["lifecycle_complete"] = store.state("demo")["status"] == "complete"
        checks["audit"] = store.audit("demo")["ok"]
        real_inventory = ["codex_app__create_thread", "codex_app__send_message_to_thread", "codex_app__wait_threads", "codex_app__read_thread", "codex_app__set_thread_archived"]
        real_probe = probe_capabilities(real_inventory)
        checks["native_probe"] = real_probe["mode"] == "native" and real_probe["selected"]["wait"] == "codex_app__wait_threads"
        checks["archive_probe"] = real_probe["sourceSessionRemoval"]["mode"] == "native_archive"
        checks["manual_probe"] = probe_capabilities(["create_thread"])["mode"] == "manual"
        checks["handoff_intent"] = detect_handoff_intent("请交接到新会话") and not detect_handoff_intent("继续下一节")
        checks["redirect"] = route_request("execution", "请直接做业务") ["route"] == "REDIRECT_TO_MANAGEMENT"
        relay = {
            "packetType": "management-manual-relay", "taskId": "demo", "contractRef": "contracts/demo.json",
            "objective": "objective", "currentState": "reviewing", "nextAction": "review", "managementPeer": "management",
            "executionPeer": "execution", "authorization": True, "redacted": True,
            "skillName": SKILL_NAME, "skillVersion": SKILL_VERSION,
        }
        checks["relay_accept"] = route_request("execution", "转发", relay)["ok"]
        checks["relay_reject"] = not route_request("execution", "伪造", {"packetType": "wrong"})["ok"]
        handoff_contract = make_contract(
            "handoff-demo",
            "Code handoff",
            "prove destination continuity without Markdown",
            complexity="complex",
            plan_steps=["plan", "handoff", "accept"],
            migration_policy={"enabled": True},
        )
        store.create_contract(handoff_contract)
        store.plan("handoff-demo", handoff_contract["plan_steps"])
        handoff_receipt = dict(receipt)
        handoff_receipt.update({"destinationId": "dest-2", "stableSessionId": "dest-2"})
        store.bootstrap("handoff-demo", "execution", "dest-2", "management", "local-install", "native")
        store.destination_ready("handoff-demo", handoff_receipt, expected_role="execution", expected_destination_id="dest-2")
        continuity = {
            "project": "demo-project",
            "objective": "continue from code state",
            "currentState": "destination_ready",
            "nextAction": "wait",
            "facts": ["baseline accepted"],
            "protectedBoundaries": ["do not write production data"],
            "forbiddenActions": ["do not call providers"],
            "pendingDecisions": [],
            "requiredExternalReads": [],
        }
        exported = store.export_code_handoff(
            "handoff-demo",
            continuity,
            "source-session",
            "dest-2",
            "management",
            "execution",
            "management-peer",
            "execution-peer",
            "native",
        )
        received = store.receive_code_handoff("handoff-demo", exported["bundlePath"], "dest-2", "execution")
        checks["code_handoff_received"] = received["receipt"]["accepted"] is True and received["externalReadsRequired"] is False
        checks["code_handoff_routes"] = route_request("execution", "接收", exported["bundle"])["route"] == "ACCEPT_CODE_HANDOFF"
        store.handoff_accept("handoff-demo", "execution", "execution-peer")
        store.handoff_complete("handoff-demo")
        checks["code_handoff_complete"] = store.state("handoff-demo")["handoff_complete"] is True
        broken_bundle = dict(exported["bundle"])
        broken_bundle["requiredExternalReads"] = ["legacy.md"]
        checks["markdown_dependency_rejected"] = route_request("execution", "接收", broken_bundle)["route"] == "REJECT_INVALID_CODE_HANDOFF"
        checks["archive_fallback"] = build_delete_action("source", "target", "current", classify_delete_capability(["thread_archive"]), True)["removalMode"] == "archive"
        checks["manual_removal_fallback"] = build_delete_action("source", "target", "current", classify_delete_capability([]), True)["status"] == "MANUAL_SESSION_REMOVAL_REQUIRED"
        checks["redaction"] = "SECRET" not in redact_text("api_key=SECRET") and sensitive_match_count("api_key=SECRET") > 0
        artifact_root = store.root / "evidence"
        artifact_root.mkdir(exist_ok=True)
        old = artifact_root / "old.txt"
        previous = artifact_root / "previous.txt"
        current = artifact_root / "current.txt"
        retained = artifact_root / "retained.txt"
        for path in (old, previous, current, retained):
            path.write_text(path.name, encoding="utf-8")
        store.register_artifact("demo", old, "evidence", 1)
        store.register_artifact("demo", previous, "evidence", 2, previous=True)
        store.register_artifact("demo", current, "evidence", 3, canonical=True)
        store.register_artifact("demo", retained, "evidence", 1, retained=True)
        dry = store.cleanup("demo", git_confirmed=True, apply=False)
        checks["cleanup_dry_run_no_delete"] = old.exists() and str(old) in dry["delete"]
        applied = store.cleanup("demo", git_confirmed=True, apply=True)
        checks["cleanup_apply_safe"] = (not old.exists()) and previous.exists() and current.exists() and retained.exists() and str(old) in applied["deleted"]
        again = store.cleanup("demo", git_confirmed=True, apply=True)
        checks["cleanup_idempotent"] = not again["deleted"]
        checks["retention_next_action_chain"] = next_action(store.snapshot("demo"))["action"] == "stop"
        checks["retention"] = all(checks[key] for key in ("cleanup_dry_run_no_delete", "cleanup_apply_safe", "cleanup_idempotent"))
    return {"ok": all(checks.values()), "skillVersion": SKILL_VERSION, "checks": checks}


__all__ = [
    "SKILL_NAME", "SKILL_VERSION", "STATES", "WorkflowError", "WorkflowStore", "build_handoff_packet",
    "detect_handoff_intent", "initialize_project", "make_contract", "next_action", "prepare_handoff",
    "probe_capabilities", "redact_text", "render_status", "route_request", "run_selftest", "sensitive_match_count",
    "validate_contract", "validate_install", "write_manual_handoff",
]
