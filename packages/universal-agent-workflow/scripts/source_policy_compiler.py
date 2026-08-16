#!/usr/bin/env python3
"""Losslessly migrate workflow Markdown sources into structured local evidence.

The resulting capsule preserves every non-blank source line for recovery and
audit, but it is explicitly not a runtime dependency. Runtime behavior comes
from the validated packaged policy and state machine.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any


class SourcePolicyCompilerError(ValueError):
    """Raised when workflow source migration is incomplete or unsafe."""


def _classify_line(text: str) -> str:
    stripped = text.lstrip()
    if stripped.startswith("#"):
        return "heading"
    if stripped.startswith("```"):
        return "code-fence"
    if stripped.startswith(("- ", "* ", "+ ")):
        return "list-item"
    prefix = stripped.split(".", 1)[0]
    if prefix.isdigit() and stripped.startswith(prefix + ". "):
        return "ordered-item"
    return "paragraph"


def _structured_source(role: str, path: str | Path) -> dict[str, Any]:
    source = Path(path).expanduser().resolve()
    if not source.is_file():
        raise SourcePolicyCompilerError(f"{role} workflow source is missing")
    lines = source.read_text(encoding="utf-8").splitlines()
    records = [
        {"line": index, "kind": _classify_line(text), "text": text}
        for index, text in enumerate(lines, start=1)
        if text.strip()
    ]
    if not records:
        raise SourcePolicyCompilerError(f"{role} workflow source is empty")
    return {
        "role": role,
        "sourceName": source.name,
        "lineCount": len(lines),
        "nonBlankLineCount": len(records),
        "records": records,
    }


def compile_workflow_sources(
    *,
    general_source: str | Path,
    management_source: str | Path,
    execution_source: str | Path,
    policy_validation: dict[str, Any],
) -> dict[str, Any]:
    if policy_validation.get("ok") is not True or policy_validation.get("externalMarkdownRequired") is not False:
        raise SourcePolicyCompilerError("runtime policy must pass before source migration")
    sources = [
        _structured_source("general", general_source),
        _structured_source("management", management_source),
        _structured_source("execution", execution_source),
    ]
    total_nonblank = sum(source["nonBlankLineCount"] for source in sources)
    total_records = sum(len(source["records"]) for source in sources)
    if total_nonblank != total_records:
        raise SourcePolicyCompilerError("workflow source migration lost non-blank lines")
    return {
        "schemaVersion": 1,
        "capsuleType": "workflow-source-migration-evidence",
        "runtimeAuthority": "code-state",
        "runtimeUse": False,
        "externalMarkdownRequired": False,
        "policyVersion": policy_validation["policyVersion"],
        "policyRuleCount": policy_validation["ruleCount"],
        "sourceCount": 3,
        "nonBlankSourceLines": total_nonblank,
        "structuredRecords": total_records,
        "losslessNonBlankCoverage": total_nonblank == total_records,
        "sources": sources,
        "retireSourceFilesAfterReleaseAndInstallAcceptance": True,
    }


__all__ = ["SourcePolicyCompilerError", "compile_workflow_sources"]
