import os
import shutil
import sys
import tempfile
import tomllib
import unittest
import zipfile
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
PACKAGE = ROOT / "packages" / "universal-agent-workflow"
SCRIPTS = PACKAGE / "scripts"
sys.path.insert(0, str(SCRIPTS))

from bootstrap import SKILL_VERSION, installation_plan  # noqa: E402
import deployment as deployment_module  # noqa: E402
from deployment import build_release_asset, deploy_skill, validate_release_tag  # noqa: E402
from package_manifest import REQUIRED_SKILL_FILES, inspect_skill_package, package_files  # noqa: E402
from workflow_engine import validate_install  # noqa: E402


class DeploymentV003Tests(unittest.TestCase):
    def copy_package(self, destination: Path) -> Path:
        shutil.copytree(PACKAGE, destination)
        return destination

    def test_version_sources_are_consistent(self):
        with (ROOT / "pyproject.toml").open("rb") as handle:
            project_version = tomllib.load(handle)["project"]["version"]
        self.assertEqual(SKILL_VERSION, "0.0.3")
        self.assertEqual((ROOT / "VERSION").read_text(encoding="utf-8").strip(), SKILL_VERSION)
        self.assertEqual((PACKAGE / "VERSION").read_text(encoding="utf-8").strip(), SKILL_VERSION)
        self.assertEqual(project_version, SKILL_VERSION)

    def test_four_state_plan_reuses_complete_manifest(self):
        with tempfile.TemporaryDirectory(prefix="uaw-v003-plan-") as raw:
            root = Path(raw)
            source = self.copy_package(root / "source")
            fresh = root / "fresh"
            self.assertEqual(installation_plan(fresh, source)["action"], "install_required")

            old = self.copy_package(root / "old")
            (old / "VERSION").write_text("0.0.2\n", encoding="utf-8")
            self.assertEqual(installation_plan(old, source)["action"], "update_required")

            same = self.copy_package(root / "same")
            plan = installation_plan(same, source)
            self.assertEqual(plan["action"], "already_exact")
            self.assertEqual(plan["state"], "current")
            self.assertEqual(plan["sourceValidation"]["manifest"], plan["targetValidation"]["manifest"])
            installed = validate_install(source)
            self.assertTrue(installed["ok"])
            self.assertEqual(set(installed["manifest"]), set(plan["sourceValidation"]["manifest"]))

            incomplete = root / "incomplete"
            incomplete.mkdir()
            (incomplete / "VERSION").write_text(SKILL_VERSION, encoding="utf-8")
            self.assertEqual(installation_plan(incomplete, source)["action"], "repair_required")

    def test_invalid_candidate_has_zero_target_writes_and_valid_replace_is_recoverable(self):
        with tempfile.TemporaryDirectory(prefix="uaw-v003-deploy-") as raw:
            root = Path(raw)
            source = self.copy_package(root / "source")
            invalid = self.copy_package(root / "invalid")
            (invalid / "scripts" / "uaw.py").unlink()
            target = self.copy_package(root / "target")
            before = sorted((path.relative_to(target).as_posix(), path.read_bytes()) for path in target.rglob("*") if path.is_file())
            rejected = deploy_skill(invalid, target, backup_root=root / "evidence")
            self.assertFalse(rejected["ok"])
            self.assertEqual(rejected["action"], "candidate_invalid")
            after = sorted((path.relative_to(target).as_posix(), path.read_bytes()) for path in target.rglob("*") if path.is_file())
            self.assertEqual(before, after)
            self.assertFalse(list((root / "evidence" / "backups").glob("*")))

            old = root / "old"
            self.copy_package(old)
            (old / "VERSION").write_text("0.0.2\n", encoding="utf-8")
            deployed = deploy_skill(source, old, backup_root=root / "evidence")
            self.assertTrue(deployed["ok"])
            self.assertEqual(deployed["action"], "update_required")
            self.assertTrue(Path(deployed["backup"]).exists())
            self.assertTrue(inspect_skill_package(old)["ok"])
            idempotent = deploy_skill(source, old, backup_root=root / "evidence")
            self.assertTrue(idempotent["ok"])
            self.assertEqual(idempotent["action"], "already_exact")
            self.assertFalse(idempotent["changed"])

    def test_failed_candidate_rename_restores_old_target(self):
        with tempfile.TemporaryDirectory(prefix="uaw-v003-rollback-") as raw:
            root = Path(raw)
            source = self.copy_package(root / "source")
            target = self.copy_package(root / "target")
            (target / "VERSION").write_text("0.0.2\n", encoding="utf-8")
            original_move = deployment_module._move_path
            failed_once = False

            def fail_candidate_rename(source_path, destination_path):
                nonlocal failed_once
                if destination_path == target and not failed_once:
                    failed_once = True
                    raise OSError("injected candidate rename failure")
                return original_move(source_path, destination_path)

            with mock.patch.object(deployment_module, "_move_path", side_effect=fail_candidate_rename):
                result = deploy_skill(source, target, backup_root=root / "evidence")
            self.assertFalse(result["ok"])
            self.assertEqual(result["state"], "rolled_back")
            self.assertTrue(result["restored"])
            self.assertTrue(result["targetUnchanged"])
            self.assertEqual((target / "VERSION").read_text(encoding="utf-8").strip(), "0.0.2")
            self.assertTrue(failed_once)

    def test_reparse_target_is_current_or_fail_closed_without_following_it(self):
        with tempfile.TemporaryDirectory(prefix="uaw-v003-reparse-") as raw:
            root = Path(raw)
            source = self.copy_package(root / "source")
            complete = self.copy_package(root / "complete")
            linked_complete = root / "linked-complete"
            try:
                os.symlink(complete, linked_complete, target_is_directory=True)
            except (OSError, NotImplementedError) as exc:
                self.skipTest(f"directory links unavailable: {exc}")
            current = deploy_skill(source, linked_complete, backup_root=root / "evidence")
            self.assertTrue(current["ok"])
            self.assertTrue(current["linkedTarget"])
            self.assertTrue(current["targetUnchanged"])
            old = self.copy_package(root / "old")
            (old / "VERSION").write_text("0.0.2\n", encoding="utf-8")
            linked_old = root / "linked-old"
            os.symlink(old, linked_old, target_is_directory=True)
            rejected = deploy_skill(source, linked_old, backup_root=root / "evidence")
            self.assertFalse(rejected["ok"])
            self.assertEqual(rejected["action"], "linked_target_requires_manual")
            self.assertTrue(rejected["targetUnchanged"])
            self.assertEqual((old / "VERSION").read_text(encoding="utf-8").strip(), "0.0.2")

    def test_zip_path_and_release_tag_gates_fail_closed(self):
        with tempfile.TemporaryDirectory(prefix="uaw-v003-gates-") as raw:
            root = Path(raw)
            source = self.copy_package(root / "source")
            target = self.copy_package(root / "target")
            (target / "VERSION").write_text("0.0.2\n", encoding="utf-8")
            for index, member in enumerate(("../VERSION", "/VERSION", "C:/VERSION")):
                malicious = root / f"malicious-{index}.zip"
                with zipfile.ZipFile(malicious, "w") as archive:
                    archive.writestr(member, "0.0.3")
                rejected = deploy_skill(malicious, target, backup_root=root / "evidence")
                self.assertFalse(rejected["ok"])
                self.assertTrue(rejected["targetUnchanged"])
                self.assertIn("path", " ".join(rejected.get("errors", [])))
            self.assertEqual((target / "VERSION").read_text(encoding="utf-8").strip(), "0.0.2")
            self.assertEqual(validate_release_tag("v0.0.3")["version"], "0.0.3")
            with self.assertRaisesRegex(ValueError, "does not match"):
                validate_release_tag("v0.0.2")

    def test_release_asset_is_complete_and_installable(self):
        with tempfile.TemporaryDirectory(prefix="uaw-v003-release-") as raw:
            root = Path(raw)
            source = self.copy_package(root / "source")
            asset = root / "dist" / "universal-agent-workflow-0.0.3.zip"
            result = build_release_asset(source, asset)
            self.assertTrue(result["ok"])
            self.assertEqual(result["version"], "0.0.3")
            self.assertTrue(set(REQUIRED_SKILL_FILES).issubset(set(result["manifest"])))
            archive_validation = inspect_skill_package(asset)
            self.assertTrue(archive_validation["ok"])
            self.assertEqual(archive_validation["sourceType"], "zip")

            directory_asset = root / "dist" / "explicit-directory.zip"
            with zipfile.ZipFile(directory_asset, "w") as archive:
                archive.writestr("uaw/", b"")
                for relative in package_files(source):
                    original = "uaw/" + relative
                    archive.writestr(original, (source / relative).read_bytes())
            target = root / "target"
            deployed = deploy_skill(directory_asset, target, backup_root=root / "evidence")
            self.assertTrue(deployed["ok"])
            self.assertEqual(deployed["action"], "install_required")
            self.assertTrue(inspect_skill_package(target)["ok"])


if __name__ == "__main__":
    unittest.main()
