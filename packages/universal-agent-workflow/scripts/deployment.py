#!/usr/bin/env python3
"""Recoverable Skill installation and release-asset primitives."""

from __future__ import annotations

import os
import shutil
import tempfile
import zipfile
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any

from package_manifest import SKILL_NAME, SKILL_VERSION, inspect_skill_package, package_files
from workflow_policy import WorkflowPolicyError, load_policy, validate_policy


class DeploymentError(ValueError):
    """Raised when a candidate cannot be safely materialized or replaced."""


def _lexical_absolute(value: str | Path) -> Path:
    """Make an absolute path without resolving links or reparse points."""

    return Path(os.path.abspath(os.path.expanduser(os.fspath(value))))


def _is_reparse_point(path: Path) -> bool:
    if not path.exists() and not path.is_symlink():
        return False
    try:
        attributes = getattr(os.lstat(path), "st_file_attributes", 0)
        if attributes & 0x0400:  # FILE_ATTRIBUTE_REPARSE_POINT
            return True
    except OSError:
        return False
    return path.is_symlink()


def _reparse_ancestors(path: Path) -> list[Path]:
    """Return existing link/junction components on the lexical target path."""

    found: list[Path] = []
    current = path
    while True:
        if _is_reparse_point(current):
            found.append(current)
        parent = current.parent
        if parent == current:
            break
        current = parent
    return list(reversed(found))


def _same_real_path(left: Path, right: Path) -> bool:
    try:
        if left.exists() and right.exists() and os.path.samefile(left, right):
            return True
    except OSError:
        pass
    return os.path.normcase(os.path.realpath(left)) == os.path.normcase(os.path.realpath(right))


def _move_path(source: Path, destination: Path) -> None:
    """Small seam used by the rollback regression to inject a rename failure."""

    shutil.move(str(source), str(destination))


def _remove_path(path: Path) -> None:
    if not path.exists() and not path.is_symlink():
        return
    if path.is_dir() and not path.is_symlink():
        shutil.rmtree(path)
    else:
        path.unlink()


def _validate_archive_member(name: str) -> str:
    normalized = name.replace("\\", "/")
    if normalized.startswith(("/", "\\")):
        raise DeploymentError("release asset contains an absolute path")
    first = normalized.split("/", 1)[0]
    if ":" in first:
        raise DeploymentError("release asset contains a drive-qualified path")
    directory = normalized.endswith("/")
    relative = PurePosixPath(normalized.rstrip("/"))
    if relative.is_absolute() or any(part in {"", ".", ".."} for part in relative.parts):
        raise DeploymentError("release asset contains an invalid path")
    result = "/".join(relative.parts)
    return f"{result}/" if directory else result


def _copy_to_candidate(source: Path, candidate: Path) -> None:
    if source.is_dir():
        shutil.copytree(source, candidate, dirs_exist_ok=True)
        return
    if source.suffix.lower() != ".zip":
        raise DeploymentError("deployment source must be a Skill directory or zip archive")
    with zipfile.ZipFile(source) as archive:
        raw_names = [name for name in archive.namelist() if name]
        # Validate directory entries too; otherwise a standalone ``../``
        # member could evade the path contract before file extraction.
        members = [(original, _validate_archive_member(original)) for original in raw_names]
        file_members = [(original, normalized) for original, normalized in members if not normalized.endswith("/")]
        normalized_files = {normalized for _, normalized in file_members}
        prefix = ""
        for possible in ("", *sorted({name.split("/", 1)[0] + "/" for _, name in file_members if "/" in name})):
            if all(f"{possible}{relative}" in normalized_files for relative in ("VERSION", "SKILL.md", "scripts/uaw.py")):
                prefix = possible
                break
        for original, normalized in file_members:
            if not normalized.startswith(prefix):
                continue
            relative = Path(*PurePosixPath(normalized[len(prefix):]).parts)
            if not relative.parts:
                continue
            destination = candidate / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(archive.read(original))


def _validate_candidate(candidate: Path, expected_version: str) -> dict[str, Any]:
    structural = inspect_skill_package(candidate, expected_version)
    errors = list(structural.get("errors", []))
    if structural.get("ok"):
        try:
            policy = load_policy(candidate, expected_version)
            policy_validation = validate_policy(policy, expected_version)
            if not policy_validation.get("ok"):
                errors.append("workflow policy validation failed")
        except (OSError, ValueError, WorkflowPolicyError) as exc:
            errors.append(str(exc))
    else:
        policy_validation = {"ok": False}
    result = dict(structural)
    result["errors"] = errors
    result["policyValidation"] = policy_validation
    result["ok"] = bool(structural.get("ok")) and not errors
    return result


def _backup_name(target: Path, backup_root: Path) -> Path:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    backup_dir = backup_root / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    candidate = backup_dir / f"{target.name}-{stamp}"
    counter = 1
    while candidate.exists():
        candidate = backup_dir / f"{target.name}-{stamp}-{counter}"
        counter += 1
    return candidate


def _transaction_root(target_path: Path, backup_root: str | Path | None) -> Path:
    """Keep candidates and backups on the target's filesystem."""

    target_parent = target_path.parent
    root = _lexical_absolute(backup_root) if backup_root else target_parent / ".uaw-deployment"
    root_parent = root.parent
    target_parent.mkdir(parents=True, exist_ok=True)
    root_parent.mkdir(parents=True, exist_ok=True)
    try:
        if os.stat(target_parent).st_dev != os.stat(root_parent).st_dev:
            raise DeploymentError("backup_root_must_share_filesystem")
    except OSError as exc:
        raise DeploymentError(f"cannot establish deployment filesystem boundary: {exc}") from exc
    root.mkdir(parents=True, exist_ok=True)
    (root / "candidates").mkdir(parents=True, exist_ok=True)
    return root


def deploy_skill(
    source: str | Path,
    target: str | Path,
    *,
    expected_version: str = SKILL_VERSION,
    backup_root: str | Path | None = None,
) -> dict[str, Any]:
    """Validate a candidate first, then replace one exact target recoverably."""

    source_path = _lexical_absolute(source)
    target_path = _lexical_absolute(target)
    if not source_path.exists():
        return {"ok": False, "action": "source_missing", "targetUnchanged": True, "errors": ["Skill install source does not exist"]}
    from bootstrap import installation_plan

    plan = installation_plan(target_path, source_path, expected_version)
    source_valid = plan.get("sourceValidation", {}).get("ok") is True
    target_valid = plan.get("targetValidation", {}).get("ok") is True
    reparse_paths = _reparse_ancestors(target_path)
    if reparse_paths:
        if plan.get("action") == "already_exact" and source_valid and target_valid:
            return {
                "ok": True,
                "action": "already_exact",
                "state": "current",
                "changed": False,
                "targetUnchanged": True,
                "linkedTarget": True,
                "reparsePaths": [str(path) for path in reparse_paths],
                "backup": None,
                "plan": plan,
            }
        return {
            "ok": False,
            "action": "linked_target_requires_manual",
            "state": "manual_required",
            "changed": False,
            "targetUnchanged": True,
            "linkedTarget": True,
            "reparsePaths": [str(path) for path in reparse_paths],
            "errors": ["target or an ancestor is a symlink/junction/reparse point"],
            "plan": plan,
        }
    if _same_real_path(source_path, target_path):
        if plan.get("action") == "already_exact" and source_valid and target_valid:
            return {"ok": True, "action": "already_exact", "state": "current", "changed": False, "targetUnchanged": True, "backup": None, "plan": plan}
        return {
            "ok": False,
            "action": "source_target_same_real_package",
            "state": "manual_required",
            "changed": False,
            "targetUnchanged": True,
            "errors": ["source and target resolve to the same package; refusing self-move"],
            "plan": plan,
        }
    if plan.get("action") == "already_exact" and source_valid:
        return {"ok": True, "action": "already_exact", "state": "current", "changed": False, "targetUnchanged": True, "backup": None, "plan": plan}

    root = _transaction_root(target_path, backup_root)
    candidate = Path(tempfile.mkdtemp(prefix="candidate-", dir=str(root / "candidates")))
    original_target_exists = target_path.exists()
    backup_path: Path | None = None
    backup_moved = False
    try:
        _copy_to_candidate(source_path, candidate)
        validation = _validate_candidate(candidate, expected_version)
        if not validation.get("ok"):
            _remove_path(candidate)
            return {
                "ok": False,
                "action": "candidate_invalid",
                "state": "candidate_invalid",
                "targetUnchanged": True,
                "changed": False,
                "candidateValidation": validation,
                "plan": plan,
            }
        if original_target_exists:
            backup_path = _backup_name(target_path, root)
            _move_path(target_path, backup_path)
            backup_moved = True
        try:
            _move_path(candidate, target_path)
        except Exception as exc:
            # A rename may have left a partial target.  Remove only that
            # non-reparse path, then restore the original backup in-place.
            if target_path.exists() and not _reparse_ancestors(target_path):
                _remove_path(target_path)
            restored = False
            restore_error: str | None = None
            if backup_moved and backup_path and backup_path.exists():
                try:
                    _move_path(backup_path, target_path)
                    restored = target_path.exists()
                except Exception as restore_exc:
                    restore_error = str(restore_exc)
            else:
                restored = not original_target_exists and not target_path.exists()
            if candidate.exists():
                _remove_path(candidate)
            errors = [f"candidate replacement failed: {exc}"]
            if restore_error:
                errors.append(f"rollback failed: {restore_error}")
            return {
                "ok": False,
                "action": "deployment_failed",
                "state": "rolled_back" if restored else "rollback_failed",
                "changed": False,
                "targetUnchanged": restored,
                "restored": restored,
                "backup": str(backup_path) if backup_path else None,
                "errors": errors,
                "plan": plan,
            }
        return {
            "ok": True,
            "action": plan.get("action"),
            "state": "deployed",
            "changed": True,
            "targetUnchanged": False,
            "backup": str(backup_path) if backup_path else None,
            "candidateValidation": validation,
            "plan": plan,
        }
    except Exception as exc:
        if candidate.exists():
            _remove_path(candidate)
        restored = False
        if backup_moved and backup_path and backup_path.exists() and not target_path.exists():
            try:
                _move_path(backup_path, target_path)
                restored = target_path.exists()
            except OSError:
                restored = False
        elif not backup_moved:
            # No target mutation occurred before candidate materialization
            # failed, so existence parity proves the target was untouched.
            restored = target_path.exists() == original_target_exists
        return {
            "ok": False,
            "action": "deployment_failed",
            "state": "rolled_back" if restored else "rollback_failed",
            "targetUnchanged": restored,
            "changed": False,
            "restored": restored,
            "backup": str(backup_path) if backup_path else None,
            "errors": [str(exc)],
            "plan": plan,
        }


def validate_release_tag(tag_name: str, expected_version: str = SKILL_VERSION) -> dict[str, Any]:
    expected_tag = f"v{expected_version}"
    if tag_name != expected_tag:
        raise DeploymentError(f"release tag {tag_name!r} does not match package VERSION {expected_version!r}; expected {expected_tag!r}")
    return {"ok": True, "tag": tag_name, "version": expected_version}


def build_release_asset(
    source: str | Path,
    output: str | Path,
    *,
    expected_version: str = SKILL_VERSION,
    tag_name: str | None = None,
) -> dict[str, Any]:
    """Build one directly installable, complete Skill zip asset."""

    if tag_name is not None:
        validate_release_tag(tag_name, expected_version)
    source_path = _lexical_absolute(source)
    output_path = _lexical_absolute(output)
    validation = inspect_skill_package(source_path, expected_version)
    if not validation.get("ok"):
        raise DeploymentError(f"cannot build release asset from invalid package: {validation.get('missing', [])} {validation.get('errors', [])}")
    if not source_path.is_dir():
        raise DeploymentError("release asset source must be the package directory")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    prefix = f"{SKILL_NAME}-{expected_version}/"
    files = package_files(source_path)
    with zipfile.ZipFile(output_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for relative in files:
            archive.write(source_path / relative, prefix + relative)
    return {
        "ok": True,
        "asset": str(output_path),
        "skillName": SKILL_NAME,
        "version": expected_version,
        "fileCount": len(files),
        "manifest": files,
    }


__all__ = ["DeploymentError", "deploy_skill", "validate_release_tag", "build_release_asset"]
