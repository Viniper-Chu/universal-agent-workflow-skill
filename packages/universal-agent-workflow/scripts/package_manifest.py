#!/usr/bin/env python3
"""The single structural manifest for an installable Skill package.

Installation planning and destination validation deliberately consume this
module instead of maintaining separate lists of files.  The manifest is
structural (paths, version and policy text); it never fingerprints content.
"""

from __future__ import annotations

import zipfile
from pathlib import Path
from typing import Any, Iterable


SKILL_NAME = "universal-agent-workflow"
PACKAGE_ROOT = Path(__file__).resolve().parents[1]
VERSION_FILE = PACKAGE_ROOT / "VERSION"
SKILL_VERSION = VERSION_FILE.read_text(encoding="utf-8").strip()

# Keep this list as the one package contract.  New deployment helpers are
# included so a same-version partial install cannot be mistaken for current.
REQUIRED_SKILL_FILES: tuple[str, ...] = (
    "VERSION",
    "SKILL.md",
    "agents/openai.yaml",
    "scripts/workflow_engine.py",
    "scripts/coordination_policy.py",
    "scripts/bootstrap.py",
    "scripts/retention.py",
    "scripts/session_migration.py",
    "scripts/handoff_bundle.py",
    "scripts/workflow_policy.py",
    "scripts/source_policy_compiler.py",
    "scripts/package_manifest.py",
    "scripts/deployment.py",
    "scripts/build_release_asset.py",
    "assets/workflow-policy.json",
    "scripts/uaw.py",
    "references/contract-schema.md",
    "references/protocol.md",
)


def required_skill_files(root: str | Path | None = None) -> list[str]:
    """Return a copy of the required relative-path manifest."""

    return list(REQUIRED_SKILL_FILES)


def package_files(root: str | Path) -> list[str]:
    """List package-relative files, excluding interpreter caches."""

    base = Path(root)
    if not base.is_dir():
        return []
    return sorted(
        path.relative_to(base).as_posix()
        for path in base.rglob("*")
        if path.is_file() and "__pycache__" not in path.parts and path.suffix != ".pyc"
    )


def _read_directory_files(root: Path) -> tuple[dict[str, bytes], str]:
    return {
        relative: (root / relative).read_bytes()
        for relative in package_files(root)
    }, "directory"


def _zip_root(names: Iterable[str]) -> str:
    normalized = [name.strip("/") for name in names if name and not name.endswith("/")]
    for prefix in ("", *sorted({name.split("/", 1)[0] + "/" for name in normalized if "/" in name})):
        if all(f"{prefix}{required}" in normalized for required in REQUIRED_SKILL_FILES):
            return prefix
    return ""


def _read_zip_files(path: Path) -> tuple[dict[str, bytes], str]:
    with zipfile.ZipFile(path) as archive:
        names = archive.namelist()
        prefix = _zip_root(names)
        files = {
            name[len(prefix):]: archive.read(name)
            for name in names
            if name.startswith(prefix) and not name.endswith("/")
        }
    return files, prefix


def inspect_skill_package(
    package: str | Path,
    expected_version: str | None = None,
) -> dict[str, Any]:
    """Validate package shape for either a directory or a release zip."""

    path = Path(package).expanduser()
    expected = expected_version or SKILL_VERSION
    if not path.exists():
        return {
            "ok": False,
            "source": str(path),
            "sourceType": "missing",
            "version": None,
            "missing": ["<package>"],
            "errors": ["package does not exist"],
            "manifest": [],
        }
    try:
        if path.is_dir():
            files, source_type = _read_directory_files(path)
            source_root = str(path.resolve())
        elif path.suffix.lower() == ".zip":
            files, prefix = _read_zip_files(path)
            source_type = "zip"
            source_root = prefix
        else:
            return {
                "ok": False,
                "source": str(path),
                "sourceType": "unsupported",
                "version": None,
                "missing": [],
                "errors": ["package must be a directory or zip archive"],
                "manifest": [],
            }
    except (OSError, zipfile.BadZipFile) as exc:
        return {
            "ok": False,
            "source": str(path),
            "sourceType": "unreadable",
            "version": None,
            "missing": [],
            "errors": [str(exc)],
            "manifest": [],
        }
    missing = [relative for relative in REQUIRED_SKILL_FILES if relative not in files]
    version_text = files.get("VERSION", b"").decode("utf-8", errors="replace").strip() if "VERSION" in files else None
    errors: list[str] = []
    if version_text is not None and version_text != expected:
        errors.append(f"package VERSION {version_text!r} does not match expected {expected!r}")
    if not version_text:
        errors.append("package VERSION is missing or empty")
    return {
        "ok": not missing and not errors,
        "source": str(path.resolve()),
        "sourceType": source_type,
        "sourceRoot": source_root,
        "version": version_text,
        "expectedVersion": expected,
        "missing": missing,
        "errors": errors,
        "manifest": sorted(files),
    }


__all__ = [
    "SKILL_NAME",
    "SKILL_VERSION",
    "REQUIRED_SKILL_FILES",
    "required_skill_files",
    "package_files",
    "inspect_skill_package",
]
