#!/usr/bin/env python3
"""Fail-closed source-session deletion gates for completed handoffs."""

from __future__ import annotations

from typing import Any


DELETE_ALIASES = {"thread_delete", "delete_thread", "delete_session", "delete_task", "remove_thread"}
ARCHIVE_ALIASES = {
    "thread_archive", "archive_thread", "archive_session", "archive_task", "archive",
    "set_thread_archived",
}


class SessionMigrationError(ValueError):
    """Raised when source-session deletion cannot be proven safe."""


def normalize_tool_name(name: str) -> str:
    """Normalize host names without binding the Skill to one namespace."""
    value = name.strip().lower()
    return value.rsplit("__", 1)[-1]


def inventory_tools(inventory: Any) -> dict[str, str]:
    if isinstance(inventory, dict):
        values = inventory.get("tools", inventory.get("capabilities", []))
    else:
        values = inventory
    if isinstance(values, dict):
        values = values.keys()
    names: dict[str, str] = {}
    for item in values or []:
        if isinstance(item, str):
            names[normalize_tool_name(item)] = item
        elif isinstance(item, dict) and isinstance(item.get("name"), str):
            names[normalize_tool_name(item["name"])] = item["name"]
    return names


def inventory_names(inventory: Any) -> set[str]:
    return set(inventory_tools(inventory))


def classify_delete_capability(inventory: Any) -> dict[str, Any]:
    tools = inventory_tools(inventory)
    delete_tool = next((tools[name] for name in sorted(DELETE_ALIASES) if name in tools), None)
    archive_tool = next((tools[name] for name in sorted(ARCHIVE_ALIASES) if name in tools), None)
    if delete_tool:
        return {"mode": "native_delete", "deleteTool": delete_tool, "archiveTool": archive_tool, "manualRequired": False}
    if archive_tool:
        return {"mode": "native_archive", "deleteTool": None, "archiveTool": archive_tool, "removalTool": archive_tool, "manualRequired": False, "removalMode": "archive"}
    return {"mode": "manual_remove_required", "deleteTool": None, "archiveTool": None, "removalTool": None, "manualRequired": True, "reason": "NO_DELETE_OR_ARCHIVE_CAPABILITY"}


def validate_delete_target(source_session_id: Any, target_session_id: Any, current_session_id: Any) -> dict[str, Any]:
    values = (source_session_id, target_session_id, current_session_id)
    if any(not isinstance(value, str) or not value.strip() for value in values):
        raise SessionMigrationError("source, target, and current session IDs are required")
    if source_session_id == target_session_id or source_session_id == current_session_id:
        raise SessionMigrationError("source session must be distinct from target and current receiving session")
    return {"ok": True, "sourceSessionId": source_session_id, "targetSessionId": target_session_id, "currentSessionId": current_session_id}


def build_delete_action(source_session_id: str, target_session_id: str, current_session_id: str, capability: dict[str, Any], policy_enabled: bool) -> dict[str, Any]:
    validate_delete_target(source_session_id, target_session_id, current_session_id)
    if policy_enabled is not True:
        return {"ok": False, "status": "SOURCE_SESSION_REMOVAL_REJECTED", "reason": "migration policy is not enabled"}
    if capability.get("mode") == "native_delete" and capability.get("deleteTool"):
        return {
            "ok": True,
            "status": "SOURCE_SESSION_REMOVAL_READY",
            "tool": capability["deleteTool"],
            "removalMode": "delete",
            "sourceSessionId": source_session_id,
            "targetSessionId": target_session_id,
            "currentSessionId": current_session_id,
            "args": {"threadId": source_session_id},
            "singleTarget": True,
        }
    if capability.get("mode") == "native_archive" and capability.get("archiveTool"):
        return {
            "ok": True,
            "status": "SOURCE_SESSION_REMOVAL_READY",
            "tool": capability["archiveTool"],
            "removalMode": "archive",
            "sourceSessionId": source_session_id,
            "targetSessionId": target_session_id,
            "currentSessionId": current_session_id,
            "args": {"archived": True, "threadId": source_session_id},
            "singleTarget": True,
        }
    if capability.get("manualRequired"):
        reason = capability.get("reason", "NO_DELETE_OR_ARCHIVE_CAPABILITY")
        return {
            "ok": False,
            "status": "MANUAL_SESSION_REMOVAL_REQUIRED",
            "reason": reason,
            "message": "当前没有可调用的删除或归档能力；请在完整交接回执后，由用户手动移除精确 sourceSessionId。",
            "sourceSessionId": source_session_id,
            "targetSessionId": target_session_id,
        }
    raise SessionMigrationError("source session removal capability is indeterminate")


__all__ = [
    "ARCHIVE_ALIASES", "DELETE_ALIASES", "SessionMigrationError", "build_delete_action",
    "classify_delete_capability", "inventory_names", "inventory_tools", "normalize_tool_name", "validate_delete_target",
]
