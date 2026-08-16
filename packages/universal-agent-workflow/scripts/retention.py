#!/usr/bin/env python3
"""Safe retention for Skill-owned workflow artifacts only."""

from __future__ import annotations

import json
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


OWNER = "universal-agent-workflow"
SCHEMA_VERSION = 1


class RetentionError(ValueError):
    """Raised when cleanup cannot prove ownership and safety."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _inside(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def _manifest_path(root: Path) -> Path:
    return root / "state" / "retention-manifest.json"


def _read(root: Path) -> dict[str, Any]:
    path = _manifest_path(root)
    if not path.exists():
        return {"schemaVersion": SCHEMA_VERSION, "owner": OWNER, "items": []}
    data = json.loads(path.read_text(encoding="utf-8"))
    if data.get("schemaVersion") != SCHEMA_VERSION or data.get("owner") != OWNER or not isinstance(data.get("items"), list):
        raise RetentionError("retention manifest identity is invalid")
    return data


def _write(root: Path, data: dict[str, Any]) -> None:
    path = _manifest_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _safe_item_path(root: Path, item: dict[str, Any], require_exists: bool = True) -> Path:
    if item.get("owner") != OWNER or not isinstance(item.get("path"), str) or not item["path"]:
        raise RetentionError("manifest item is not owned by this Skill")
    path = (root / item["path"]).resolve()
    if not _inside(path, root.resolve()):
        raise RetentionError("manifest path escapes controlled output root")
    if require_exists and not path.exists():
        raise RetentionError("manifest path disappeared before cleanup")
    return path


def register_artifact(root: str | Path, path: str | Path, kind: str, generation: int, *, canonical: bool = False, previous: bool = False, ephemeral: bool = False, retained: bool = False) -> dict[str, Any]:
    root_path = Path(root).expanduser().resolve()
    artifact = Path(path).expanduser().resolve()
    if not _inside(artifact, root_path) or artifact == root_path:
        raise RetentionError("registered artifact must be inside the controlled root")
    if not artifact.exists():
        raise RetentionError("registered artifact must already exist")
    if not isinstance(generation, int) or generation < 1:
        raise RetentionError("generation must be a positive integer")
    if not kind or not isinstance(kind, str):
        raise RetentionError("artifact kind is required")
    data = _read(root_path)
    relative = artifact.relative_to(root_path).as_posix()
    for item in data["items"]:
        if item.get("path") == relative and not item.get("deleted"):
            item.update({
                "generation": generation,
                "kind": kind,
                "createdAt": _now(),
                "canonical": bool(canonical),
                "previous": bool(previous),
                "ephemeral": bool(ephemeral),
                "retained": bool(retained),
            })
            _write(root_path, data)
            return {"ok": True, "item": item, "updated": True}
    item = {
        "path": relative,
        "generation": generation,
        "owner": OWNER,
        "kind": kind,
        "createdAt": _now(),
        "canonical": bool(canonical),
        "previous": bool(previous),
        "ephemeral": bool(ephemeral),
        "retained": bool(retained),
        "deleted": False,
    }
    data["items"].append(item)
    _write(root_path, data)
    return {"ok": True, "item": item}


def _active_items(data: dict[str, Any]) -> list[dict[str, Any]]:
    return [item for item in data["items"] if not item.get("deleted")]


def _plan(root: Path, data: dict[str, Any]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    active = _active_items(data)
    if not active:
        return [], []
    current = [item for item in active if item.get("canonical")]
    previous = [item for item in active if item.get("previous")]
    if not current or not previous:
        raise RetentionError("current and previous generations are required before cleanup")
    current_generation = max(int(item["generation"]) for item in current)
    delete: list[dict[str, Any]] = []
    keep: list[dict[str, Any]] = []
    for item in active:
        _safe_item_path(root, item)
        if item.get("retained") or item.get("canonical") or item.get("previous"):
            keep.append(item)
        elif item.get("ephemeral") or int(item["generation"]) < current_generation - 1:
            delete.append(item)
        else:
            keep.append(item)
    return delete, keep


def retention_summary(root: str | Path) -> dict[str, Any]:
    root_path = Path(root).expanduser().resolve()
    data = _read(root_path)
    active = _active_items(data)
    return {
        "ok": True,
        "owner": OWNER,
        "items": len(active),
        "candidates": [item["path"] for item in active if not item.get("canonical") and not item.get("previous") and not item.get("retained")],
        "current": [item["path"] for item in active if item.get("canonical")],
        "previous": [item["path"] for item in active if item.get("previous")],
        "retained": [item["path"] for item in active if item.get("retained")],
        "deleted": [item["path"] for item in data["items"] if item.get("deleted")],
        "currentGeneration": max((int(item["generation"]) for item in active if item.get("canonical")), default=None),
        "previousGeneration": max((int(item["generation"]) for item in active if item.get("previous")), default=None),
    }


def rotate_generations(root: str | Path, generation: int) -> dict[str, Any]:
    """Mark a prepared generation current and the adjacent generation previous."""
    if not isinstance(generation, int) or generation < 1:
        raise RetentionError("generation must be a positive integer")
    root_path = Path(root).expanduser().resolve()
    data = _read(root_path)
    active = _active_items(data)
    if not any(int(item.get("generation", 0)) == generation for item in active):
        raise RetentionError("cannot mark a generation that has no registered artifacts")
    for item in active:
        item_generation = int(item["generation"])
        item["canonical"] = item_generation == generation
        item["previous"] = item_generation == generation - 1
    _write(root_path, data)
    return retention_summary(root_path)


def verify_git_current(repo_root: str | Path) -> bool:
    """Return only a boolean; never expose repository identifiers."""
    root = Path(repo_root).expanduser().resolve()
    try:
        inside = subprocess.run(["git", "-C", str(root), "rev-parse", "--is-inside-work-tree"], capture_output=True, text=True, check=False)
        if inside.returncode != 0 or inside.stdout.strip().lower() != "true":
            return False
        status = subprocess.run(["git", "-C", str(root), "status", "--porcelain"], capture_output=True, text=True, check=False)
        subject = subprocess.run(["git", "-C", str(root), "log", "-1", "--format=%s"], capture_output=True, text=True, check=False)
        return status.returncode == 0 and not status.stdout.strip() and subject.returncode == 0 and bool(subject.stdout.strip())
    except OSError:
        return False


def cleanup_artifacts(root: str | Path, *, git_confirmed: bool, apply: bool = False) -> dict[str, Any]:
    root_path = Path(root).expanduser().resolve()
    if not git_confirmed:
        raise RetentionError("Git current canonical version is not confirmed")
    data = _read(root_path)
    delete, keep = _plan(root_path, data)
    result: dict[str, Any] = {
        "ok": True,
        "dryRun": not apply,
        "delete": [str((root_path / item["path"]).resolve()) for item in delete],
        "keep": [str((root_path / item["path"]).resolve()) for item in keep],
        "deleted": [],
    }
    if not apply:
        return result
    for item in delete:
        path = _safe_item_path(root_path, item)
        if path.is_dir() and not path.is_symlink():
            shutil.rmtree(path)
        else:
            path.unlink()
        item["deleted"] = True
        item["deletedAt"] = _now()
        result["deleted"].append(str(path))
    _write(root_path, data)
    result["dryRun"] = False
    return result


__all__ = ["OWNER", "RetentionError", "cleanup_artifacts", "register_artifact", "retention_summary", "rotate_generations", "verify_git_current"]
