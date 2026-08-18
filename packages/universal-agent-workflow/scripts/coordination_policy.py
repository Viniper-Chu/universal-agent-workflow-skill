#!/usr/bin/env python3
"""Code-backed coordination contracts for the 0.0.3 release batch.

The module owns the native host-action argument schemas, migration identity
chain, settings policy, supervision fallback, and role delegation rules.
JSON policy, the CLI, and the workflow engine only project or consume these
contracts; they do not reimplement them.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any, Iterable


class CoordinationPolicyError(ValueError):
    """Raised when a coordination contract would violate a code-backed gate."""


HOST_ACTION_STATUSES = frozenset({"planned", "sent", "observed", "failed"})
HOST_ACTION_TRANSITIONS = {
    "planned": frozenset({"sent", "failed"}),
    "sent": frozenset({"observed", "failed"}),
    "observed": frozenset(),
    "failed": frozenset(),
}
FORBIDDEN_OVERRIDE_KEYS = frozenset({"model", "thinking", "reasoning"})

ACTION_TO_TOOL = {
    "send_message": "codex_app__send_message_to_thread",
    "wait_threads": "codex_app__wait_threads",
    "read_thread": "codex_app__read_thread",
    "create_thread": "codex_app__create_thread",
}
DEFAULT_WAIT_TIMEOUT_MS = 120000
HOST_ARG_SCHEMAS = {
    "send_message": {
        "required": frozenset({"threadId", "prompt"}),
        "optional": frozenset({"hostId"}),
    },
    "wait_threads": {
        "required": frozenset({"targets"}),
        "optional": frozenset({"timeoutMs"}),
    },
    "read_thread": {
        "required": frozenset({"threadId"}),
        "optional": frozenset({
            "hostId",
            "cursor",
            "includeOutputs",
            "maxOutputCharsPerItem",
            "turnLimit",
        }),
    },
    "create_thread": {
        "required": frozenset({"prompt", "target"}),
        "optional": frozenset({"title"}),
    },
}

MIGRATION_PHASES = (
    "old_management_to_new_management_message",
    "new_management_accepted",
    "new_management_creates_execution",
    "new_management_messages_execution",
)
DELEGATION_POLICY = {
    "management": {
        "outline": frozenset({"management", "reviewer"}),
        "contract": frozenset({"management", "reviewer"}),
        "review": frozenset({"management", "reviewer"}),
    },
    "execution": {"parallel_implementation": frozenset({"execution"})},
    "reviewer": {"review": frozenset({"reviewer"})},
}
SETTINGS_POLICY = {
    "inherit_management": True,
    "preserve_user_settings": True,
    "omit_overrides": sorted(FORBIDDEN_OVERRIDE_KEYS),
}

_MIGRATION_STEP_CONTRACT = {
    0: {"phase": MIGRATION_PHASES[0], "action": "send_message", "targetRole": "management"},
    2: {"phase": MIGRATION_PHASES[2], "action": "create_thread", "targetRole": "execution"},
    3: {"phase": MIGRATION_PHASES[3], "action": "send_message", "targetRole": "execution"},
}


def _nonempty(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _contains_forbidden_key(value: Any) -> str | None:
    if isinstance(value, dict):
        for key, child in value.items():
            if key in FORBIDDEN_OVERRIDE_KEYS:
                return key
            found = _contains_forbidden_key(child)
            if found:
                return found
    elif isinstance(value, list):
        for child in value:
            found = _contains_forbidden_key(child)
            if found:
                return found
    return None


def _assert_exact_keys(value: dict[str, Any], required: set[str] | frozenset[str], optional: set[str] | frozenset[str], label: str) -> None:
    keys = set(value)
    missing = sorted(required - keys)
    unknown = sorted(keys - set(required) - set(optional))
    if missing:
        raise CoordinationPolicyError(f"{label} missing " + ", ".join(missing))
    if unknown:
        raise CoordinationPolicyError(f"{label} has unsupported keys: " + ", ".join(unknown))


def validate_create_target(target: Any) -> dict[str, Any]:
    """Validate the exact target union accepted by codex_app__create_thread."""
    if not isinstance(target, dict):
        raise CoordinationPolicyError("create_thread target must be an object")
    target_type = target.get("type")
    if target_type == "project":
        _assert_exact_keys(target, {"type", "projectId", "environment"}, set(), "project target")
        if not _nonempty(target.get("projectId")):
            raise CoordinationPolicyError("project target projectId is required")
        environment = target.get("environment")
        if not isinstance(environment, dict) or environment.get("type") not in {"local", "worktree"}:
            raise CoordinationPolicyError("project target environment must be local or worktree")
        if environment.get("type") == "local":
            _assert_exact_keys(environment, {"type"}, set(), "local environment")
        else:
            _assert_exact_keys(environment, {"type"}, {"startingState"}, "worktree environment")
            starting = environment.get("startingState")
            if starting is not None:
                if not isinstance(starting, dict) or starting.get("type") not in {"working-tree", "branch"}:
                    raise CoordinationPolicyError("worktree startingState is invalid")
                if starting.get("type") == "working-tree":
                    _assert_exact_keys(starting, {"type"}, set(), "working-tree startingState")
                else:
                    _assert_exact_keys(starting, {"type", "branchName"}, {"onMissing"}, "branch startingState")
                    if not _nonempty(starting.get("branchName")):
                        raise CoordinationPolicyError("branch startingState branchName is required")
                    if starting.get("onMissing", "error") not in {"error", "create-branch"}:
                        raise CoordinationPolicyError("branch startingState onMissing is invalid")
    elif target_type == "projectless":
        _assert_exact_keys(target, {"type"}, {"directoryName"}, "projectless target")
        if "directoryName" in target and not _nonempty(target.get("directoryName")):
            raise CoordinationPolicyError("projectless directoryName must be non-empty")
    elif target_type == "chatgptWorkCloud":
        _assert_exact_keys(target, {"type"}, {"projectId"}, "chatgptWorkCloud target")
        if "projectId" in target and not _nonempty(target.get("projectId")):
            raise CoordinationPolicyError("chatgptWorkCloud projectId must be non-empty")
    else:
        raise CoordinationPolicyError("create_thread target type must be project, projectless, or chatgptWorkCloud")
    return deepcopy(target)


def _validate_wait_target(target: Any) -> dict[str, Any]:
    if not isinstance(target, dict):
        raise CoordinationPolicyError("wait_threads targets must be objects")
    _assert_exact_keys(target, {"threadId"}, {"hostId", "afterCursor"}, "wait target")
    if not _nonempty(target.get("threadId")):
        raise CoordinationPolicyError("wait target threadId is required")
    for key in ("hostId", "afterCursor"):
        if key in target and not _nonempty(target.get(key)):
            raise CoordinationPolicyError(f"wait target {key} must be non-empty")
    return deepcopy(target)


def validate_host_args(action: str, args: Any) -> dict[str, Any]:
    if action not in HOST_ARG_SCHEMAS:
        raise CoordinationPolicyError(f"unsupported host action: {action}")
    if not isinstance(args, dict):
        raise CoordinationPolicyError("host action args must be an object")
    schema = HOST_ARG_SCHEMAS[action]
    _assert_exact_keys(args, schema["required"], schema["optional"], f"{action} args")
    forbidden = _contains_forbidden_key(args)
    if forbidden:
        raise CoordinationPolicyError(f"host action args cannot override {forbidden}")
    if action == "send_message":
        if not _nonempty(args.get("threadId")) or not _nonempty(args.get("prompt")):
            raise CoordinationPolicyError("send_message args require threadId and prompt")
        if "hostId" in args and not _nonempty(args.get("hostId")):
            raise CoordinationPolicyError("send_message hostId must be non-empty")
    elif action == "wait_threads":
        if not isinstance(args.get("targets"), list) or not args["targets"]:
            raise CoordinationPolicyError("wait_threads targets must be a non-empty list")
        normalized_targets = [_validate_wait_target(target) for target in args["targets"]]
        if "timeoutMs" in args and (not isinstance(args["timeoutMs"], int) or isinstance(args["timeoutMs"], bool) or args["timeoutMs"] < 0):
            raise CoordinationPolicyError("wait_threads timeoutMs must be a non-negative integer")
        result = {"targets": normalized_targets}
        if "timeoutMs" in args:
            result["timeoutMs"] = args["timeoutMs"]
        return result
    elif action == "read_thread":
        if not _nonempty(args.get("threadId")):
            raise CoordinationPolicyError("read_thread threadId is required")
        for key in ("hostId", "cursor"):
            if key in args and not _nonempty(args.get(key)):
                raise CoordinationPolicyError(f"read_thread {key} must be non-empty")
        for key in ("includeOutputs",):
            if key in args and not isinstance(args[key], bool):
                raise CoordinationPolicyError(f"read_thread {key} must be boolean")
        for key in ("maxOutputCharsPerItem", "turnLimit"):
            if key in args and (not isinstance(args[key], int) or isinstance(args[key], bool) or args[key] < 0):
                raise CoordinationPolicyError(f"read_thread {key} must be a non-negative integer")
    elif action == "create_thread":
        if not _nonempty(args.get("prompt")):
            raise CoordinationPolicyError("create_thread prompt is required")
        if "title" in args and not _nonempty(args.get("title")):
            raise CoordinationPolicyError("create_thread title must be non-empty")
        target = validate_create_target(args.get("target"))
        result = {"prompt": args["prompt"], "target": target}
        if "title" in args:
            result["title"] = args["title"]
        return result
    return deepcopy(args)


def sanitize_host_args(action: str, args: Any) -> dict[str, Any]:
    """Validate and copy the single native host-argument schema."""
    return validate_host_args(action, args)


def build_host_action(
    action: str,
    tool: str,
    args: dict[str, Any] | None = None,
    *,
    actor_role: str,
    phase: str,
    target_role: str | None = None,
    action_id: str | None = None,
    dispatch_id: str | None = None,
    actor_session_id: str | None = None,
    chain_id: str | None = None,
) -> dict[str, Any]:
    """Build a planned host action without claiming host execution."""
    if action not in ACTION_TO_TOOL:
        raise CoordinationPolicyError(f"unsupported host action: {action}")
    if tool != ACTION_TO_TOOL[action]:
        raise CoordinationPolicyError(f"{action} must use {ACTION_TO_TOOL[action]}")
    if not _nonempty(actor_role) or not _nonempty(phase):
        raise CoordinationPolicyError("host action identity is incomplete")
    if action in {"create_thread", "send_message"} and phase in {
        MIGRATION_PHASES[0], MIGRATION_PHASES[2], MIGRATION_PHASES[3]
    } and actor_role != "management":
        raise CoordinationPolicyError("management migration create/send actions must be planned by management")
    normalized_args = sanitize_host_args(action, args or {})
    result = {
        "schemaVersion": 1,
        "actionId": action_id or f"{phase}:{action}:{tool}",
        "action": action,
        "tool": tool,
        "actorRole": actor_role,
        "phase": phase,
        "status": "planned",
        "args": normalized_args,
        "result": None,
        "writeAllowed": action not in {"read_thread", "wait_threads"},
    }
    for key, value in (
        ("targetRole", target_role),
        ("dispatchId", dispatch_id),
        ("actorSessionId", actor_session_id),
        ("chainId", chain_id),
    ):
        if value is not None:
            result[key] = value
    return result


def validate_host_action(action: Any) -> dict[str, Any]:
    if not isinstance(action, dict):
        raise CoordinationPolicyError("host action must be an object")
    required = ("schemaVersion", "actionId", "action", "tool", "actorRole", "phase", "status", "args")
    missing = [key for key in required if key not in action]
    if missing:
        raise CoordinationPolicyError("host action missing " + ", ".join(missing))
    if action.get("schemaVersion") != 1 or not _nonempty(action.get("actionId")):
        raise CoordinationPolicyError("host action schema or identity is invalid")
    if action.get("status") not in HOST_ACTION_STATUSES:
        raise CoordinationPolicyError("host action status is invalid")
    if action.get("action") in {"create_thread", "send_message"} and action.get("phase") in {
        MIGRATION_PHASES[0], MIGRATION_PHASES[2], MIGRATION_PHASES[3]
    } and action.get("actorRole") != "management":
        raise CoordinationPolicyError("management migration create/send actions must be planned by management")
    if action.get("actorSessionId") is not None and not _nonempty(action.get("actorSessionId")):
        raise CoordinationPolicyError("actorSessionId must be non-empty")
    normalized = deepcopy(action)
    normalized["args"] = sanitize_host_args(normalized["action"], normalized["args"])
    return normalized


def _validate_chain_result(action: dict[str, Any], status: str, result: Any) -> None:
    if action.get("chainId") is None:
        return
    if not isinstance(result, dict):
        raise CoordinationPolicyError("migration host result must be an object")
    if result.get("chainId") != action.get("chainId"):
        raise CoordinationPolicyError("migration result chainId does not match action")
    if result.get("actorSessionId") != action.get("actorSessionId"):
        raise CoordinationPolicyError("migration result actorSessionId does not match action")
    if action.get("action") == "send_message":
        if not _nonempty(result.get("threadId")) or result.get("threadId") != action["args"].get("threadId"):
            raise CoordinationPolicyError("migration send result threadId does not match target")
    if action.get("action") == "create_thread":
        if "executionId" in result:
            raise CoordinationPolicyError("create_thread result must use the real threadId")
        if status == "observed" and not _nonempty(result.get("threadId")):
            raise CoordinationPolicyError("observed create_thread result requires threadId")


def record_host_action(
    action: dict[str, Any],
    status: str,
    result: Any = None,
    *,
    allow_legacy_wait_shape: bool = False,
) -> dict[str, Any]:
    """Record a host result while keeping planned/sent/observed/failed explicit."""
    current = validate_host_action(action)
    if status not in HOST_ACTION_STATUSES:
        raise CoordinationPolicyError("host action status is invalid")
    direct_observation = status == "observed" and current["status"] == "planned" and current.get("action") in {"wait_threads", "read_thread"}
    if status != current["status"] and status not in HOST_ACTION_TRANSITIONS[current["status"]] and not direct_observation:
        raise CoordinationPolicyError(f"host action cannot transition {current['status']} -> {status}")
    if status in {"sent", "observed"} and not isinstance(result, dict):
        raise CoordinationPolicyError(f"host action {status} requires an object host result")
    _validate_chain_result(current, status, result)
    if current.get("action") == "wait_threads" and status in {"failed", "observed"}:
        wait_result = classify_wait_result(result, allow_legacy_shape=allow_legacy_wait_shape)
        if status == "observed" and wait_result["kind"] != "observed":
            raise CoordinationPolicyError("wait_threads observed requires a non-timeout wake result")
        if status == "failed" and wait_result["kind"] != "tool_error":
            raise CoordinationPolicyError("wait_threads failed requires an explicit tool error")
    current["status"] = status
    current["result"] = deepcopy(result)
    return current


def derive_execution_settings(
    management_settings: dict[str, Any],
    user_settings: dict[str, Any] | None = None,
    *,
    inheritance_evidence: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Inherit management settings by default, but never overwrite user choice."""
    if not isinstance(management_settings, dict):
        raise CoordinationPolicyError("management settings must be an object")
    if user_settings is not None and not isinstance(user_settings, dict):
        raise CoordinationPolicyError("user settings must be an object")
    def merge(defaults: dict[str, Any], overrides: dict[str, Any]) -> dict[str, Any]:
        merged = deepcopy(defaults)
        for key, value in overrides.items():
            if isinstance(merged.get(key), dict) and isinstance(value, dict):
                merged[key] = merge(merged[key], value)
            else:
                merged[key] = deepcopy(value)
        return merged

    source = "user" if user_settings is not None else "management"
    evidence_proven = isinstance(inheritance_evidence, dict) and inheritance_evidence.get("proven") is True
    return {
        "settings": merge(management_settings, user_settings or {}),
        "managementSnapshot": deepcopy(management_settings),
        "userOverrides": deepcopy(user_settings or {}),
        "source": source,
        "inherited": bool(management_settings),
        "preserveUserSettings": True,
        "evidenceRequired": not evidence_proven,
        "evidenceProven": evidence_proven,
    }


def build_execution_creation_action(
    management_settings: dict[str, Any],
    target: dict[str, Any],
    user_settings: dict[str, Any] | None = None,
    *,
    actor_session_id: str | None = None,
    chain_id: str | None = None,
    action_id: str | None = None,
    inheritance_evidence: dict[str, Any] | None = None,
) -> dict[str, Any]:
    settings = derive_execution_settings(
        management_settings,
        user_settings,
        inheritance_evidence=inheritance_evidence,
    )
    action = build_host_action(
        "create_thread",
        ACTION_TO_TOOL["create_thread"],
        {"prompt": "Create a governed execution thread", "target": target, "title": "Universal Agent Workflow execution"},
        actor_role="management",
        target_role="execution",
        actor_session_id=actor_session_id,
        chain_id=chain_id,
        phase=MIGRATION_PHASES[2],
        action_id=action_id,
    )
    action["settingsPolicy"] = deepcopy(SETTINGS_POLICY)
    action["settingsPolicy"]["evidenceRequired"] = settings["evidenceRequired"]
    action["settingsPolicy"]["evidenceStatus"] = "proven" if settings["evidenceProven"] else "UNPROVEN"
    action["settings"] = settings
    action["requiresObservedThreadId"] = True
    return action


def _deferred_execution_send(new_management_id: str, chain_id: str) -> dict[str, Any]:
    return {
        "schemaVersion": 1,
        "actionId": f"{new_management_id}:dispatch-execution",
        "action": "send_message",
        "tool": ACTION_TO_TOOL["send_message"],
        "actorRole": "management",
        "targetRole": "execution",
        "actorSessionId": new_management_id,
        "chainId": chain_id,
        "phase": MIGRATION_PHASES[3],
        "status": "planned",
        "args": {"prompt": "execution dispatch"},
        "result": None,
        "writeAllowed": True,
        "deferred": True,
        "completed": False,
    }


def build_migration_sequence(
    old_management_id: str,
    new_management_id: str,
    target: dict[str, Any],
    *,
    chain_id: str | None = None,
    management_settings: dict[str, Any] | None = None,
    user_settings: dict[str, Any] | None = None,
    inheritance_evidence: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Build four planned migration stages without a guessed execution ID."""
    if not all(_nonempty(value) for value in (old_management_id, new_management_id)):
        raise CoordinationPolicyError("migration management identities are incomplete")
    target_value = validate_create_target(target)
    chain = chain_id or f"migration:{old_management_id}->{new_management_id}"
    if not _nonempty(chain):
        raise CoordinationPolicyError("migration chain identity is incomplete")
    first = build_host_action(
        "send_message",
        ACTION_TO_TOOL["send_message"],
        {"threadId": new_management_id, "prompt": "management handoff"},
        actor_role="management",
        target_role="management",
        actor_session_id=old_management_id,
        chain_id=chain,
        phase=MIGRATION_PHASES[0],
        action_id=f"{chain}:handoff",
    )
    first["completed"] = False
    accepted = {
        "schemaVersion": 1,
        "phase": MIGRATION_PHASES[1],
        "kind": "state_gate",
        "action": "accept_management",
        "actorRole": "management",
        "actorSessionId": new_management_id,
        "threadId": new_management_id,
        "chainId": chain,
        "status": "pending",
        "accepted": False,
        "result": None,
        "completed": False,
    }
    create = build_execution_creation_action(
        management_settings if management_settings is not None else {},
        target_value,
        user_settings,
        actor_session_id=new_management_id,
        chain_id=chain,
        action_id=f"{chain}:create-execution",
        inheritance_evidence=inheritance_evidence,
    )
    create["completed"] = False
    send = _deferred_execution_send(new_management_id, chain)
    return [first, accepted, create, send]


def _validate_migration_chain_identity(step: dict[str, Any], chain_id: str | None) -> None:
    if chain_id is not None and step.get("chainId") != chain_id:
        raise CoordinationPolicyError("migration step chainId does not match")
    if not _nonempty(step.get("actorSessionId")):
        raise CoordinationPolicyError("migration step actorSessionId is required")


def validate_migration_step(step: Any, index: int | None = None, *, chain_id: str | None = None) -> dict[str, Any]:
    """Validate one migration step, including actor, action and gate semantics."""
    if not isinstance(step, dict):
        raise CoordinationPolicyError("migration step must be an object")
    phase = step.get("phase")
    if index is None:
        try:
            index = MIGRATION_PHASES.index(phase)
        except ValueError as exc:
            raise CoordinationPolicyError("migration phase is invalid") from exc
    if index < 0 or index >= len(MIGRATION_PHASES) or phase != MIGRATION_PHASES[index]:
        raise CoordinationPolicyError("migration phase order is invalid")
    if step.get("completed") is not False:
        raise CoordinationPolicyError("planned migration steps must have completed=false")
    _validate_migration_chain_identity(step, chain_id)
    if index in _MIGRATION_STEP_CONTRACT:
        contract = _MIGRATION_STEP_CONTRACT[index]
        if index == 3 and step.get("deferred") is True:
            if step.get("action") != "send_message" or step.get("tool") != ACTION_TO_TOOL["send_message"]:
                raise CoordinationPolicyError("deferred execution dispatch must be a send_message action")
            if step.get("actorRole") != "management" or step.get("targetRole") != "execution":
                raise CoordinationPolicyError("deferred execution dispatch roles are invalid")
            if step.get("status") != "planned" or set(step.get("args", {})) != {"prompt"} or not _nonempty(step.get("args", {}).get("prompt")):
                raise CoordinationPolicyError("deferred execution dispatch must wait for a real threadId")
            return deepcopy(step)
        action = validate_host_action(deepcopy(step))
        if action.get("action") != contract["action"] or action.get("actorRole") != "management" or action.get("targetRole") != contract["targetRole"]:
            raise CoordinationPolicyError(f"migration phase {phase} action/roles are invalid")
        if index == 0 and action["args"].get("threadId") == action.get("actorSessionId"):
            raise CoordinationPolicyError("old management cannot target itself")
        if index == 2:
            policy = action.get("settingsPolicy")
            if not isinstance(policy, dict) or any(policy.get(key) != value for key, value in SETTINGS_POLICY.items()):
                raise CoordinationPolicyError("execution creation requires explicit settingsPolicy")
            settings = action.get("settings")
            if not isinstance(settings, dict) or not isinstance(settings.get("managementSnapshot"), dict) or not isinstance(settings.get("userOverrides"), dict):
                raise CoordinationPolicyError("execution creation requires a management settings snapshot and user override map")
            if "target" not in action.get("args", {}):
                raise CoordinationPolicyError("execution creation requires a caller-supplied target")
            if action.get("result") and "executionId" in action["result"]:
                raise CoordinationPolicyError("execution creation result cannot use executionId")
        if action.get("result") is not None:
            _validate_chain_result(action, action.get("status"), action.get("result"))
        return action
    if step.get("kind") != "state_gate" or step.get("action") != "accept_management":
        raise CoordinationPolicyError("new management acceptance gate is invalid")
    if step.get("actorRole") != "management" or step.get("threadId") != step.get("actorSessionId"):
        raise CoordinationPolicyError("new management acceptance must be recorded by new management")
    if step.get("status") not in {"pending", "accepted"} or step.get("accepted") is not (step.get("status") == "accepted"):
        raise CoordinationPolicyError("acceptance gate status is invalid")
    return deepcopy(step)


def _migration_summary(normalized: list[dict[str, Any]]) -> dict[str, Any]:
    first, accepted, create, send = normalized
    host_result = create.get("result") if isinstance(create.get("result"), dict) else None
    created_thread_id = host_result.get("threadId") if host_result else None
    completed = bool(
        first.get("status") in {"sent", "observed"}
        and accepted.get("status") == "accepted"
        and create.get("status") == "observed"
        and _nonempty(created_thread_id)
        and send.get("deferred") is not True
        and send.get("args", {}).get("threadId") == created_thread_id
        and send.get("status") in {"sent", "observed"}
    )
    return {
        "chainId": first.get("chainId"),
        "completed": completed,
        "evidenceRequired": not completed,
        "requiredPhases": list(MIGRATION_PHASES),
        "observedThreadId": created_thread_id,
        "stepsObserved": len(normalized),
    }


def validate_migration_sequence(steps: Iterable[dict[str, Any]]) -> dict[str, Any]:
    normalized = list(steps)
    if len(normalized) != len(MIGRATION_PHASES):
        raise CoordinationPolicyError("management migration must contain the four code-backed phases")
    chain_id = normalized[0].get("chainId") if isinstance(normalized[0], dict) else None
    if not _nonempty(chain_id):
        raise CoordinationPolicyError("migration chain identity is required")
    checked = [validate_migration_step(step, index, chain_id=chain_id) for index, step in enumerate(normalized)]
    first, accepted, create, send = checked
    if first.get("args", {}).get("threadId") != accepted.get("threadId"):
        raise CoordinationPolicyError("handoff message must target the accepted new management")
    if first.get("actorSessionId") == accepted.get("actorSessionId"):
        raise CoordinationPolicyError("old and new management identities must differ")
    if create.get("actorSessionId") != accepted.get("actorSessionId"):
        raise CoordinationPolicyError("execution creation actorSessionId must match accepted management")
    if send.get("actorSessionId") != accepted.get("actorSessionId"):
        raise CoordinationPolicyError("execution dispatch actorSessionId must match accepted management")
    if accepted.get("status") == "accepted" and first.get("status") not in {"sent", "observed"}:
        raise CoordinationPolicyError("new management cannot accept before the old management send")
    if create.get("status") != "planned" and accepted.get("status") != "accepted":
        raise CoordinationPolicyError("execution creation requires accepted new management")
    if create.get("status") == "observed":
        result = create.get("result")
        if not isinstance(result, dict) or not _nonempty(result.get("threadId")):
            raise CoordinationPolicyError("observed create action requires the real threadId")
        if send.get("deferred") is True:
            raise CoordinationPolicyError("execution dispatch must bind the observed create threadId")
    if send.get("deferred") is not True and create.get("status") == "observed":
        if send.get("args", {}).get("threadId") != create.get("result", {}).get("threadId"):
            raise CoordinationPolicyError("execution dispatch target does not match the create result threadId")
    summary = _migration_summary(checked)
    return {
        "ok": True,
        "phases": [step.get("phase") for step in checked],
        "writeAllowed": False,
        **summary,
    }


def bind_migration_execution_send(steps: Iterable[dict[str, Any]], host_result: dict[str, Any]) -> list[dict[str, Any]]:
    """Bind the final send action to the real create-thread result identity."""
    normalized = [deepcopy(step) for step in steps]
    if len(normalized) != len(MIGRATION_PHASES) or not isinstance(host_result, dict) or not _nonempty(host_result.get("threadId")):
        raise CoordinationPolicyError("a real create_thread result threadId is required")
    create = normalized[2]
    if create.get("status") != "observed":
        raise CoordinationPolicyError("create_thread must be observed before binding execution dispatch")
    create["result"] = deepcopy(host_result)
    target = host_result["threadId"]
    deferred = normalized[3]
    normalized[3] = build_host_action(
        "send_message",
        ACTION_TO_TOOL["send_message"],
        {"threadId": target, "prompt": "execution dispatch"},
        actor_role="management",
        target_role="execution",
        actor_session_id=deferred.get("actorSessionId"),
        chain_id=deferred.get("chainId"),
        phase=MIGRATION_PHASES[3],
        action_id=deferred.get("actionId"),
    )
    normalized[3]["completed"] = False
    validate_migration_sequence(normalized)
    return normalized


def summarize_migration_steps(steps: Iterable[dict[str, Any]]) -> dict[str, Any]:
    """Project migration completion/evidence without hiding an incomplete chain."""
    normalized = list(steps)
    if not normalized:
        return {
            "chainId": None,
            "completed": False,
            "evidenceRequired": True,
            "requiredPhases": list(MIGRATION_PHASES),
            "observedThreadId": None,
            "stepsObserved": 0,
        }
    chain_id = normalized[0].get("chainId") if isinstance(normalized[0], dict) else None
    if len(normalized) != len(MIGRATION_PHASES):
        return {
            "chainId": chain_id,
            "completed": False,
            "evidenceRequired": True,
            "requiredPhases": list(MIGRATION_PHASES),
            "observedThreadId": None,
            "stepsObserved": len(normalized),
        }
    try:
        validation = validate_migration_sequence(normalized)
    except CoordinationPolicyError:
        return {
            "chainId": chain_id,
            "completed": False,
            "evidenceRequired": True,
            "requiredPhases": list(MIGRATION_PHASES),
            "observedThreadId": None,
            "stepsObserved": len(normalized),
        }
    return {key: validation[key] for key in ("chainId", "completed", "evidenceRequired", "requiredPhases", "observedThreadId", "stepsObserved")}


def classify_wait_result(result: Any, *, allow_legacy_shape: bool = False) -> dict[str, Any]:
    """Classify one native wait result without turning a timeout into a failure."""
    if not isinstance(result, dict):
        raise CoordinationPolicyError("wait_threads result must be an object")
    if result.get("timedOut") is True:
        return {
            "kind": "timeout",
            "timedOut": True,
            "observed": False,
            "reviewReady": False,
            "failureClass": None,
        }
    if result.get("timedOut") is False:
        wake = result.get("wake")
        if not isinstance(wake, dict) or not wake:
            if allow_legacy_shape and _nonempty(result.get("reason")):
                return {
                    "kind": "observed",
                    "timedOut": False,
                    "wake": {"reason": result["reason"]},
                    "observed": True,
                    "reviewReady": True,
                    "failureClass": None,
                    "legacyAdapter": True,
                }
            raise CoordinationPolicyError("successful wait_threads result requires a wake object")
        return {
            "kind": "observed",
            "timedOut": False,
            "wake": deepcopy(wake),
            "observed": True,
            "reviewReady": True,
            "failureClass": None,
        }
    if result.get("ok") is True:
        return {
            "kind": "observed",
            "timedOut": False,
            "observed": True,
            "reviewReady": True,
            "failureClass": None,
            "legacyAdapter": True,
        }
    if result.get("ok") is False:
        return {
            "kind": "tool_error",
            "timedOut": False,
            "observed": False,
            "reviewReady": False,
            "failureClass": "orchestration_harness_failure",
        }
    raise CoordinationPolicyError("wait_threads result must be timedOut or an explicit tool status")


def build_supervision_plan(dispatch_id: str, wait_result: dict[str, Any] | None = None, read_result: dict[str, Any] | None = None) -> dict[str, Any]:
    if not _nonempty(dispatch_id):
        raise CoordinationPolicyError("dispatch identity is required")
    plan = {
        "schemaVersion": 1,
        "dispatchId": dispatch_id,
        "sequence": ["wait", "observe", "correct"],
        "nextAction": "wait",
        "failureClass": None,
        "fallbackUsed": False,
        "reviewReady": False,
        "writeAllowed": False,
    }
    if wait_result is None:
        return plan
    try:
        classification = classify_wait_result(wait_result)
    except CoordinationPolicyError:
        plan["nextAction"] = "stop"
        plan["waitOutcome"] = "invalid"
        plan["waitResult"] = deepcopy(wait_result)
        return plan
    plan["waitOutcome"] = classification["kind"]
    if classification["kind"] == "timeout":
        plan["nextAction"] = "wait"
        plan["waitResult"] = deepcopy(wait_result)
        return plan
    if classification["kind"] == "observed":
        plan["nextAction"] = "observe"
        plan["reviewReady"] = True
        plan["waitResult"] = deepcopy(wait_result)
        return plan
    plan["failureClass"] = "orchestration_harness_failure"
    plan["fallbackUsed"] = True
    plan["nextAction"] = "read"
    plan["waitResult"] = deepcopy(wait_result)
    if read_result is not None:
        plan["readResult"] = deepcopy(read_result)
        plan["nextAction"] = "observe" if read_result.get("ok") is True else "stop"
        plan["reviewReady"] = read_result.get("ok") is True
        if read_result.get("ok") is True:
            plan["readObserved"] = True
    return plan


def validate_delegation(parent_role: str, work_category: str, child_role: str | None = None) -> dict[str, Any]:
    allowed_children = DELEGATION_POLICY.get(parent_role, {}).get(work_category, frozenset())
    if child_role is None:
        allowed = False
        reason = "child role is required for the delegation gate"
    elif child_role not in {"management", "execution", "reviewer"}:
        allowed = False
        reason = "child role is invalid"
    else:
        allowed = child_role in allowed_children
        reason = "allowed" if allowed else f"{parent_role} cannot delegate {work_category} to {child_role}"
    require = {
        "allowed": allowed,
        "eventWriteAllowed": allowed,
        "gate": "delegation.child_role_and_work_category",
        "reason": reason,
    }
    return {
        "ok": True,
        "allowed": allowed,
        "parentRole": parent_role,
        "childRole": child_role,
        "workCategory": work_category,
        "reason": reason,
        "require": require,
        "policy": {parent: {category: sorted(children) for category, children in categories.items()} for parent, categories in DELEGATION_POLICY.items()},
    }


def coordination_policy_projection() -> dict[str, Any]:
    return {
        "hostActionStatuses": sorted(HOST_ACTION_STATUSES),
        "forbiddenOverrideKeys": sorted(FORBIDDEN_OVERRIDE_KEYS),
        "hostActionSchemas": {
            action: {
                "tool": ACTION_TO_TOOL[action],
                "required": sorted(schema["required"]),
                "optional": sorted(schema["optional"]),
            }
            for action, schema in HOST_ARG_SCHEMAS.items()
        },
        "createTargetTypes": ["project", "projectless", "chatgptWorkCloud"],
        "migrationPhases": list(MIGRATION_PHASES),
        "delegation": {parent: {category: sorted(children) for category, children in categories.items()} for parent, categories in DELEGATION_POLICY.items()},
        "settingsPolicy": deepcopy(SETTINGS_POLICY),
        "defaultWaitTimeoutMs": DEFAULT_WAIT_TIMEOUT_MS,
        "supervisionSequence": ["wait", "observe", "correct"],
    }


__all__ = [
    "ACTION_TO_TOOL",
    "CoordinationPolicyError",
    "DEFAULT_WAIT_TIMEOUT_MS",
    "DELEGATION_POLICY",
    "FORBIDDEN_OVERRIDE_KEYS",
    "HOST_ACTION_STATUSES",
    "HOST_ARG_SCHEMAS",
    "MIGRATION_PHASES",
    "SETTINGS_POLICY",
    "bind_migration_execution_send",
    "build_execution_creation_action",
    "build_host_action",
    "build_migration_sequence",
    "build_supervision_plan",
    "classify_wait_result",
    "coordination_policy_projection",
    "derive_execution_settings",
    "record_host_action",
    "sanitize_host_args",
    "summarize_migration_steps",
    "validate_create_target",
    "validate_delegation",
    "validate_host_action",
    "validate_host_args",
    "validate_migration_sequence",
    "validate_migration_step",
]
