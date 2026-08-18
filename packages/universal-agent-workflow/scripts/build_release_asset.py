#!/usr/bin/env python3
"""CLI wrapper for the controlled Skill release-asset builder."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from deployment import DeploymentError, build_release_asset
from package_manifest import SKILL_VERSION


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="build_release_asset")
    parser.add_argument("--source", default=str(Path(__file__).resolve().parents[1]))
    parser.add_argument("--output", required=True)
    parser.add_argument("--version", default=SKILL_VERSION)
    parser.add_argument("--tag-name", help="optional Git tag; must equal v<package VERSION>")
    args = parser.parse_args(argv)
    try:
        result = build_release_asset(args.source, args.output, expected_version=args.version, tag_name=args.tag_name)
    except DeploymentError as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False, indent=2))
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
