import sys
import subprocess
import tempfile
import unittest
import json
import copy
from pathlib import Path


SCRIPT_DIR = Path(__file__).resolve().parents[1] / "packages" / "universal-agent-workflow" / "scripts"
sys.path.insert(0, str(SCRIPT_DIR))

from bootstrap import BootstrapError, SKILL_NAME, SKILL_VERSION, installation_plan, make_bootstrap_packet, validate_readiness_receipt  # noqa: E402
from handoff_bundle import HandoffBundleError, receive_code_handoff_bundle, validate_code_handoff_bundle  # noqa: E402
from workflow_policy import WorkflowPolicyError, load_policy, policy_profile, validate_policy  # noqa: E402
from session_migration import build_delete_action, classify_delete_capability, validate_delete_target  # noqa: E402
from coordination_policy import build_host_action, build_migration_sequence  # noqa: E402
from workflow_engine import (  # noqa: E402
    WorkflowError,
    WorkflowStore,
    detect_handoff_intent,
    initialize_project,
    make_contract,
    next_action,
    prepare_handoff,
    probe_capabilities,
    redact_text,
    resolve_output_root,
    route_request,
    sensitive_match_count,
)


class WorkflowEngineTests(unittest.TestCase):
    def make_store(self):
        temp = tempfile.TemporaryDirectory(prefix="uaw-test-")
        project = Path(temp.name) / "project"
        project.mkdir()
        root = project / ".agent-workflow"
        initialize_project(project, root)
        return temp, project, WorkflowStore(project, root)

    def make_contract_and_plan(self, store, management_peer=None, execution_peer=None):
        contract = make_contract("task", "Task", "test the governed path", complexity="complex", plan_steps=["plan", "execute", "review"])
        if management_peer:
            contract["management_peer"] = management_peer
        if execution_peer:
            contract["execution_peer"] = execution_peer
        store.create_contract(contract)
        store.plan("task", contract["plan_steps"])

    def complete_dispatch(self, store, wait_result=None):
        state = store.state("task")
        dispatch_id = state["supervision"]["dispatchId"]
        send = next(item for item in state["host_actions"] if item.get("dispatchId") == dispatch_id and item["action"] == "send_message")
        wait = next(item for item in state["host_actions"] if item.get("dispatchId") == dispatch_id and item["action"] == "wait_threads")
        store.record_host_action_result("task", send["actionId"], "sent", {"ok": True, "messageId": "test-send"})
        if wait_result is None:
            store.record_host_action_result("task", wait["actionId"], "observed", {"ok": True, "observed": True})
        else:
            store.record_host_action_result("task", wait["actionId"], "failed", wait_result)
        return dispatch_id

    def test_invalid_contract_actor_leaves_no_partial_task(self):
        temp, project, store = self.make_store()
        self.addCleanup(temp.cleanup)
        contract = make_contract("atomic-task", "Atomic task", "reject partial creation")
        with self.assertRaisesRegex(WorkflowError, "event actor is invalid"):
            store.create_contract(contract, actor="source-management6")
        self.assertFalse(store._contract_path("atomic-task").exists())
        self.assertFalse(store._task_state_path("atomic-task").exists())
        self.assertEqual(store.events("atomic-task"), [])

    def test_destination_events_are_signed_by_contract_destination_role(self):
        temp, project, store = self.make_store()
        self.addCleanup(temp.cleanup)
        contract = make_contract("management-handoff", "Management handoff", "transfer management")
        contract["destination_role"] = "management"
        store.create_contract(contract)
        store.plan("management-handoff", contract["plan_steps"])
        store.bootstrap("management-handoff", "management", "management-7", "source-management", "installed", "native")
        receipt = self.receipt("management-7")
        receipt["role"] = "management"
        with self.assertRaisesRegex(WorkflowError, "does not match destination role"):
            store.destination_ready("management-handoff", receipt, "management", "management-7", actor="execution")
        snapshot = store.destination_ready("management-handoff", receipt, "management", "management-7", actor="management")
        self.assertEqual(snapshot["status"], "destination_ready")

    def test_handoff_acceptance_role_must_match_contract_destination(self):
        temp, project, store = self.make_store()
        self.addCleanup(temp.cleanup)
        contract = make_contract("execution-handoff", "Execution handoff", "transfer execution")
        store.create_contract(contract)
        store.plan("execution-handoff", contract["plan_steps"])
        store.bootstrap("execution-handoff", "execution", "execution-7", "management-7", "installed", "native")
        receipt = self.receipt("execution-7")
        store.destination_ready("execution-handoff", receipt, "execution", "execution-7")
        exported = store.export_code_handoff(
            "execution-handoff",
            self.continuity(),
            "source-session",
            "execution-7",
            "management",
            "execution",
            "management-7",
            "execution-7",
            "native",
        )
        store.receive_code_handoff("execution-handoff", exported["bundlePath"], "execution-7", "execution")
        with self.assertRaisesRegex(WorkflowError, "role does not match"):
            store.handoff_accept("execution-handoff", "management", "management-7")
        with self.assertRaisesRegex(WorkflowError, "peer does not match"):
            store.handoff_accept("execution-handoff", "execution", "execution-7")
        snapshot = store.handoff_accept("execution-handoff", "execution", "source-session")
        self.assertTrue(snapshot["handoff_accepted"])

    def receipt(self, destination="destination"):
        return {
            "skillName": SKILL_NAME,
            "skillVersion": SKILL_VERSION,
            "role": "execution",
            "installPath": "controlled-install",
            "selftestStatus": "passed",
            "quickValidateStatus": "passed",
            "capabilityMode": "native",
            "destinationId": destination,
            "stableSessionId": "stable-session",
            "peerIdentity": "execution-peer",
            "runtimeAuthority": "code-state",
            "externalReadsRequired": False,
            "validationSource": "destination-bootstrap",
            "policyStatus": "passed",
            "policyVersion": SKILL_VERSION,
            "policyRuleCount": 1,
            "ready": True,
        }

    def continuity(self):
        return {
            "project": "test-project",
            "objective": "continue through code state",
            "currentState": "destination_ready",
            "nextAction": "wait for management",
            "facts": ["baseline accepted"],
            "protectedBoundaries": ["preserve user data"],
            "forbiddenActions": ["do not call external providers"],
            "pendingDecisions": [],
            "requiredExternalReads": [],
        }

    def export_and_receive(self, store, destination="destination"):
        exported = store.export_code_handoff(
            "task",
            self.continuity(),
            "source-session",
            destination,
            "management",
            "execution",
            "management-peer",
            "execution-peer",
            "native",
        )
        received = store.receive_code_handoff("task", exported["bundlePath"], destination, "execution")
        return exported, received

    def test_native_and_manual_capability_modes(self):
        inventory = ["codex_app__create_thread", "codex_app__send_message_to_thread", "codex_app__wait_threads", "codex_app__read_thread", "codex_app__set_thread_archived"]
        native = probe_capabilities(inventory)
        self.assertEqual(native["mode"], "native")
        self.assertEqual(native["selected"]["wait"], "codex_app__wait_threads")
        self.assertEqual(native["sourceSessionRemoval"]["mode"], "native_archive")
        partial = probe_capabilities(inventory[:-2])
        self.assertEqual(partial["mode"], "manual")
        self.assertIn("read", partial["missing"])
        archive_only = probe_capabilities(["codex_app__set_thread_archived"])
        self.assertEqual(archive_only["sourceSessionRemoval"]["mode"], "native_archive")
        self.assertEqual(archive_only["mode"], "manual")
        manual = probe_capabilities(["create_thread"])
        self.assertEqual(manual["mode"], "manual")
        self.assertIn("send", manual["missing"])
        self.assertIn("wait", manual["missing"])

    def test_handoff_confirmation_and_ordinary_continuation(self):
        self.assertTrue(detect_handoff_intent("换个对话继续"))
        self.assertFalse(detect_handoff_intent("继续下一节"))
        temp, project, store = self.make_store()
        try:
            self.make_contract_and_plan(store, management_peer="management-7", execution_peer="execution-6")
            ask = prepare_handoff(store, "task", "请交接到新会话", confirmed=False)
            self.assertTrue(ask["confirmation_required"])
            manual = prepare_handoff(store, "task", "请交接到新会话", confirmed=True, capabilities=["create_thread"])
            self.assertEqual(manual["mode"], "manual")
            self.assertIn("Step 0", Path(manual["path"]).read_text(encoding="utf-8"))
            native_tools = [
                "codex_app__create_thread",
                "codex_app__send_message_to_thread",
                "codex_app__wait_threads",
                "codex_app__read_thread",
            ]
            native = prepare_handoff(store, "task", "请交接到新会话", confirmed=True, capabilities=native_tools)
            self.assertEqual(native["mode"], "native")
            self.assertEqual(native["packet"]["nextAction"]["mode"], "native")
            self.assertEqual(native["packet"]["nextAction"]["missing"], [])
            self.assertEqual(native["packet"]["managementPeer"], "management-7")
            self.assertEqual(native["packet"]["executionPeer"], "execution-6")
        finally:
            temp.cleanup()

    def test_role_routing_and_relay(self):
        self.assertEqual(route_request("execution", "直接做这个业务")["route"], "REDIRECT_TO_MANAGEMENT")
        relay = {
            "packetType": "management-manual-relay", "taskId": "task", "contractRef": "contracts/task.json",
            "objective": "objective", "currentState": "reviewing", "nextAction": "review",
            "managementPeer": "management", "executionPeer": "execution", "authorization": True, "redacted": True,
            "skillName": SKILL_NAME, "skillVersion": SKILL_VERSION,
        }
        self.assertTrue(route_request("execution", "转发", relay)["ok"])
        relay["redacted"] = False
        self.assertFalse(route_request("execution", "转发", relay)["ok"])
        relay["redacted"] = True
        relay.pop("skillVersion")
        self.assertFalse(route_request("execution", "转发", relay)["ok"])

    def test_code_policy_and_destination_bootstrap_need_no_markdown(self):
        skill_dir = SCRIPT_DIR.parent
        policy = load_policy(skill_dir, SKILL_VERSION)
        validation = validate_policy(policy, SKILL_VERSION)
        self.assertEqual(validation["ruleCount"], validation["mandatoryRuleCount"])
        self.assertFalse(validation["externalMarkdownRequired"])
        execution_profile = policy_profile("execution", skill_dir, SKILL_VERSION)
        execution_ids = {rule["id"] for rule in execution_profile["rules"]}
        self.assertIn("execution-direct-user-redirect", execution_ids)
        self.assertIn("code-handoff-bundle", execution_ids)

        broken = copy.deepcopy(policy)
        broken["rules"] = [rule for rule in broken["rules"] if rule["id"] != "code-handoff-bundle"]
        with self.assertRaises(WorkflowPolicyError):
            validate_policy(broken, SKILL_VERSION)
        broken = copy.deepcopy(policy)
        broken["externalMarkdownRequired"] = True
        with self.assertRaises(WorkflowPolicyError):
            validate_policy(broken, SKILL_VERSION)

        cli = SCRIPT_DIR / "uaw.py"
        result = subprocess.run(
            [
                sys.executable,
                str(cli),
                "destination-bootstrap",
                "--skill-dir", str(skill_dir),
                "--role", "execution",
                "--destination-id", "destination-7",
                "--stable-session-id", "destination-7",
                "--peer-identity", "management-7",
                "--tools",
                "codex_app__create_thread",
                "codex_app__send_message_to_thread",
                "codex_app__wait_threads",
                "codex_app__read_thread",
                "codex_app__set_thread_archived",
            ],
            capture_output=True,
            check=True,
        )
        payload = json.loads(result.stdout.decode("utf-8"))
        self.assertTrue(payload["ok"])
        self.assertFalse(payload["externalReadsRequired"])
        self.assertFalse(payload["runtimePolicy"]["externalMarkdownRequired"])
        self.assertEqual(payload["receipt"]["policyStatus"], "passed")
        self.assertEqual(payload["receipt"]["validationSource"], "destination-bootstrap")
        with tempfile.TemporaryDirectory(prefix="uaw-missing-policy-") as missing_skill:
            failed = subprocess.run(
                [
                    sys.executable,
                    str(cli),
                    "destination-bootstrap",
                    "--skill-dir", missing_skill,
                    "--role", "execution",
                    "--destination-id", "destination-7",
                    "--stable-session-id", "destination-7",
                    "--peer-identity", "management-7",
                    "--tools", "create_thread",
                ],
                capture_output=True,
                check=False,
            )
            self.assertNotEqual(failed.returncode, 0)
            self.assertEqual(failed.stdout, b"")
            self.assertFalse(json.loads(failed.stderr.decode("utf-8"))["ok"])

    def test_code_handoff_is_self_contained_and_rejects_document_dependencies(self):
        temp, project, store = self.make_store()
        try:
            self.make_contract_and_plan(store)
            store.bootstrap("task", "execution", "destination", "management-peer", "controlled-install", "native")
            store.destination_ready("task", self.receipt(), expected_role="execution", expected_destination_id="destination")
            exported, received = self.export_and_receive(store)
            bundle = exported["bundle"]
            self.assertTrue(validate_code_handoff_bundle(bundle)["ok"])
            self.assertFalse(received["receipt"]["externalReadsRequired"])
            self.assertEqual(received["receipt"]["context"]["continuity"]["facts"], ["baseline accepted"])

            for mutation in (
                lambda value: value.update({"requiredExternalReads": ["legacy.md"]}),
                lambda value: value["continuity"].update({"requiredExternalReads": ["project.md"]}),
                lambda value: value["destination"].update({"sessionId": "wrong"}),
                lambda value: value.update({"skillVersion": "9.9.9"}),
                lambda value: value["contract"].update({"objective": "tampered"}),
            ):
                candidate = copy.deepcopy(bundle)
                mutation(candidate)
                with self.assertRaises(HandoffBundleError):
                    receive_code_handoff_bundle(
                        candidate,
                        expected_skill_name=SKILL_NAME,
                        expected_skill_version=SKILL_VERSION,
                        expected_destination_id="destination",
                        expected_role="execution",
                    )
        finally:
            temp.cleanup()

    def test_manual_transport_invokes_same_code_receiver(self):
        temp, project, store = self.make_store()
        try:
            self.make_contract_and_plan(store)
            manual_receipt = self.receipt()
            manual_receipt["capabilityMode"] = "manual"
            manual_receipt.pop("stableSessionId")
            manual_receipt["stableSessionIdUnavailable"] = True
            store.bootstrap("task", "execution", "destination", "management-peer", "controlled-install", "manual")
            store.destination_ready("task", manual_receipt, expected_role="execution", expected_destination_id="destination")
            exported = store.export_code_handoff(
                "task",
                self.continuity(),
                "source-session",
                "destination",
                "management",
                "execution",
                "management-peer",
                "execution-peer",
                "manual",
            )
            relay_text = Path(exported["manualRelayPath"]).read_text(encoding="utf-8")
            self.assertIn("handoff-receive", relay_text)
            self.assertIn("not workflow authority", relay_text)
            self.assertNotIn("read project", relay_text.lower())
            received = store.receive_code_handoff("task", exported["bundlePath"], "destination", "execution")
            self.assertTrue(received["receipt"]["accepted"])
        finally:
            temp.cleanup()
    def test_lifecycle_requires_receipt_report_and_independent_acceptance(self):
        temp, project, store = self.make_store()
        try:
            self.make_contract_and_plan(store)
            with self.assertRaises(WorkflowError):
                store.dispatch("task")
            store.bootstrap("task", "execution", "destination", "management-peer", "controlled-install", "native")
            store.destination_ready("task", self.receipt(), expected_role="execution", expected_destination_id="destination")
            store.dispatch("task")
            self.complete_dispatch(store)
            store.start_execution("task")
            with self.assertRaises(WorkflowError):
                store.review("task", "accepted", independent=True)
            store.report("task", report_ref="reports/task.md")
            with self.assertRaises(WorkflowError):
                store.review("task", "accepted", independent=True, checkpoint=True)
            store.review("task", "accepted", independent=True)
            store.complete("task")
            self.assertEqual(store.state("task")["status"], "complete")
            self.assertTrue(store.audit("task")["ok"])
        finally:
            temp.cleanup()

    def test_formal_handoff_acceptance_precedes_dispatch(self):
        temp, project, store = self.make_store()
        try:
            self.make_contract_and_plan(store)
            store.bootstrap("task", "execution", "destination", "management-peer", "controlled-install", "native")
            store.destination_ready("task", self.receipt(), expected_role="execution", expected_destination_id="destination")
            exported, received = self.export_and_receive(store)
            self.assertFalse(received["externalReadsRequired"])
            with self.assertRaises(WorkflowError):
                store.dispatch("task")
            store.handoff_accept("task", "execution", "source-session")
            self.assertEqual(store.state("task")["status"], "destination_ready")
            store.dispatch("task")
            self.assertEqual(store.state("task")["status"], "dispatched")
        finally:
            temp.cleanup()

    def prepare_handoff_complete(self, store):
        contract = make_contract("task", "Task", "test source removal", complexity="complex", plan_steps=["plan", "execute", "review"], migration_policy={"enabled": True})
        store.create_contract(contract)
        store.plan("task", contract["plan_steps"])
        store.bootstrap("task", "execution", "target-session", "management-peer", "controlled-install", "native")
        store.destination_ready("task", self.receipt("target-session"), expected_role="execution", expected_destination_id="target-session")
        self.export_and_receive(store, "target-session")
        store.handoff_accept("task", "execution", "source-session")
        store.handoff_complete("task")
        store.authorize_migration("task", "source-session", "target-session", "current-session")

    def test_source_session_removal_gates_delete_archive_and_manual(self):
        self.assertEqual(classify_delete_capability(["thread_archive"])["mode"], "native_archive")
        self.assertEqual(classify_delete_capability([])["mode"], "manual_remove_required")
        self.assertEqual(build_delete_action("source", "target", "current", classify_delete_capability(["thread_archive"]), True)["removalMode"], "archive")
        receiving_target = build_delete_action("source", "target", "target", classify_delete_capability(["thread_archive"]), True)
        self.assertEqual(receiving_target["currentSessionId"], "target")
        with self.assertRaises(Exception):
            validate_delete_target("same", "same", "current")
        with self.assertRaises(Exception):
            validate_delete_target("same", "target", "same")

        temp, project, store = self.make_store()
        try:
            self.make_contract_and_plan(store)
            store.bootstrap("task", "execution", "target-session", "management-peer", "controlled-install", "native")
            store.destination_ready("task", self.receipt("target-session"), expected_role="execution", expected_destination_id="target-session")
            with self.assertRaises(WorkflowError):
                store.request_source_removal("task", "source-session", "target-session", "current-session", ["thread_delete"], True)
        finally:
            temp.cleanup()

        temp, project, store = self.make_store()
        try:
            self.prepare_handoff_complete(store)
            manual = store.request_source_removal("task", "source-session", "target-session", "current-session", ["thread_archive_missing"], True)
            self.assertEqual(manual["status"], "MANUAL_SESSION_REMOVAL_REQUIRED")
            self.assertFalse(store.state("task")["source_removal_requested"])
            archive_state = store.request_source_removal("task", "source-session", "target-session", "current-session", ["thread_archive"], True)
            self.assertEqual(archive_state["source_removal_mode"], "archive")
            self.assertEqual(archive_state["hostAction"]["args"], {"archived": True, "threadId": "source-session"})
            removed = store.record_source_removed("task", "source-session", True)
            self.assertEqual(removed["status"], "removed")
            with self.assertRaises(WorkflowError):
                store.dispatch("task")
        finally:
            temp.cleanup()

        temp, project, store = self.make_store()
        try:
            self.prepare_handoff_complete(store)
            store.authorize_migration("task", "source-session", "target-session", "current-session")
            with self.assertRaises(WorkflowError):
                store.request_source_removal("task", "same", "same", "current", ["thread_delete"], True)
            store.request_source_removal("task", "source-session", "target-session", "current-session", ["thread_delete"], True)
            failed = store.record_source_removed("task", "source-session", False)
            self.assertEqual(failed["status"], "handoff_complete")
            self.assertFalse(failed["session_removed"])
        finally:
            temp.cleanup()

        temp, project, store = self.make_store()
        try:
            self.prepare_handoff_complete(store)
            store.authorize_migration("task", "source-session", "target-session", "current-session")
            store.request_source_removal("task", "source-session", "target-session", "current-session", ["thread_delete"], True)
            removed = store.record_source_removed("task", "source-session", True)
            self.assertEqual(removed["status"], "removed")
            self.assertTrue(removed["session_deleted"])
            self.assertEqual(removed["source_removal_mode"], "delete")
        finally:
            temp.cleanup()

    def test_receipt_rejects_wrong_version_role_identity_and_failed_checks(self):
        receipt = self.receipt()
        for key, value in (("skillVersion", "9.0.0"), ("role", "reviewer"), ("destinationId", "other"), ("selftestStatus", "failed"), ("ready", False)):
            candidate = dict(receipt)
            candidate[key] = value
            with self.assertRaises(BootstrapError):
                validate_readiness_receipt(candidate, expected_role="execution", expected_destination_id="destination")
        manual = dict(receipt)
        manual["capabilityMode"] = "manual"
        manual.pop("stableSessionId")
        manual["stableSessionIdUnavailable"] = True
        self.assertTrue(validate_readiness_receipt(manual, expected_role="execution", expected_destination_id="destination")["ok"])
        manual.pop("stableSessionIdUnavailable")
        with self.assertRaises(BootstrapError):
            validate_readiness_receipt(manual, expected_role="execution", expected_destination_id="destination")

    def test_default_plans_and_actor_audit_guards(self):
        simple = make_contract("simple", "Simple", "short")
        complex_contract = make_contract("complex", "Complex", "large", complexity="complex")
        self.assertTrue(simple["plan_steps"])
        self.assertLess(len(simple["plan_steps"]), len(complex_contract["plan_steps"]))
        temp, project, store = self.make_store()
        try:
            store.create_contract(simple)
            with self.assertRaises(WorkflowError):
                store.plan("simple", simple["plan_steps"], actor="execution")
            with self.assertRaises(WorkflowError):
                store.transition("simple", "plan.created", {"steps": simple["plan_steps"]}, actor="execution")
            store.plan("simple", simple["plan_steps"])
            lines = store.events_path.read_text(encoding="utf-8").splitlines()
            record = json.loads(lines[1])
            record["actor"] = "execution"
            lines[1] = json.dumps(record, ensure_ascii=False)
            store.events_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
            audit = store.audit("simple")
            self.assertFalse(audit["ok"])
            self.assertTrue(any("actor" in error for error in audit["errors"]))
            record["actor"] = "management"
            record["id"] = 9
            lines[1] = json.dumps(record, ensure_ascii=False)
            store.events_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
            self.assertFalse(store.audit("simple")["ok"])
            self.assertTrue(any("continuous" in error for error in store.audit("simple")["errors"]))
        finally:
            temp.cleanup()

    def test_retention_keeps_current_previous_and_retained(self):
        temp, project, store = self.make_store()
        try:
            self.make_contract_and_plan(store)
            store.bootstrap("task", "execution", "destination", "management-peer", "controlled-install", "native")
            store.destination_ready("task", self.receipt(), expected_role="execution", expected_destination_id="destination")
            store.dispatch("task")
            self.complete_dispatch(store)
            store.start_execution("task")
            store.report("task", report_ref="reports/task.md")
            store.review("task", "accepted", independent=True)
            store.complete("task")
            evidence = store.root / "evidence"
            evidence.mkdir(exist_ok=True)
            old, previous, current, retained = [evidence / name for name in ("old", "previous", "current", "retained")]
            for path in (old, previous, current, retained):
                path.write_text(path.name, encoding="utf-8")
            store.register_artifact("task", old, "evidence", 1)
            store.register_artifact("task", previous, "evidence", 2, previous=True)
            store.register_artifact("task", current, "evidence", 3, canonical=True)
            store.register_artifact("task", retained, "evidence", 1, retained=True)
            dry = store.cleanup("task", git_confirmed=True)
            self.assertTrue(old.exists())
            self.assertIn(str(old), dry["delete"])
            applied = store.cleanup("task", git_confirmed=True, apply=True)
            self.assertFalse(old.exists())
            self.assertTrue(previous.exists() and current.exists() and retained.exists())
            self.assertIn(str(old), applied["deleted"])
            self.assertEqual(store.cleanup("task", git_confirmed=True, apply=True)["deleted"], [])
        finally:
            temp.cleanup()

    def test_next_action_drives_retention_and_generation_rotation(self):
        temp, project, store = self.make_store()
        try:
            self.make_contract_and_plan(store)
            store.bootstrap("task", "execution", "destination", "management-peer", "controlled-install", "native")
            store.destination_ready("task", self.receipt(), expected_role="execution", expected_destination_id="destination")
            store.dispatch("task")
            self.complete_dispatch(store)
            store.start_execution("task")
            store.report("task", report_ref="reports/task.md")
            store.review("task", "accepted", independent=True)
            store.complete("task")
            evidence = store.root / "evidence"
            evidence.mkdir(exist_ok=True)
            old, previous, current, newest = [evidence / name for name in ("old", "previous", "current", "newest")]
            for path in (old, previous, current, newest):
                path.write_text(path.name, encoding="utf-8")
            store.register_artifact("task", old, "evidence", 1)
            store.register_artifact("task", previous, "evidence", 2, previous=True)
            store.register_artifact("task", current, "evidence", 3, canonical=True)
            store.register_artifact("task", newest, "evidence", 4)
            self.assertEqual(next_action(store.snapshot("task"))["action"], "retention_dry_run")
            store.retention_dry_run("task", git_confirmed=True)
            self.assertEqual(next_action(store.snapshot("task"))["action"], "retention_apply")
            store.retention_apply("task", git_confirmed=True)
            self.assertEqual(next_action(store.snapshot("task"))["action"], "stop")
            summary = store.rotate_retention("task", 4)
            self.assertEqual(summary["currentGeneration"], 4)
            self.assertEqual(summary["previousGeneration"], 3)
        finally:
            temp.cleanup()

    def test_cli_plan_and_receipt_are_single_json_documents(self):
        temp = tempfile.TemporaryDirectory(prefix="uaw-cli-contract-")
        try:
            project = Path(temp.name) / "project"
            project.mkdir()
            cli = SCRIPT_DIR / "uaw.py"

            def run(*args):
                return subprocess.run(
                    [sys.executable, str(cli), *args],
                    capture_output=True,
                    text=True,
                    check=True,
                )

            common = ("--project-root", str(project), "--output-root", ".agent-workflow")
            run("init", *common)
            plan_result = run(
                "plan",
                *common,
                "--task-id", "cli-task",
                "--title", "CLI task",
                "--objective", "verify one JSON result",
            )
            self.assertEqual(plan_result.stderr, "")
            plan_payload = json.loads(plan_result.stdout)
            self.assertTrue(plan_payload["ok"])
            self.assertEqual(plan_payload["command"], "plan")
            self.assertEqual(plan_payload["snapshot"]["status"], "planning")
            self.assertEqual(plan_payload["snapshot"]["last_event"], "plan.created")
            self.assertTrue(plan_payload["snapshot"]["plan_steps"])
            self.assertEqual(plan_payload["contract"]["task_id"], "cli-task")

            run(
                "bootstrap",
                *common,
                "--task-id", "cli-task",
                "--role", "execution",
                "--destination-id", "destination",
                "--peer-identity", "execution-peer",
                "--install-source", "controlled-install",
                "--capability-mode", "native",
            )
            receipt_path = project / "receipt.json"
            receipt_path.write_text(json.dumps({
                "skillName": SKILL_NAME,
                "skillVersion": SKILL_VERSION,
                "role": "execution",
                "installPath": "controlled-install",
                "selftestStatus": "passed",
                "quickValidateStatus": "passed",
                "capabilityMode": "native",
                "destinationId": "destination",
                "stableSessionId": "stable-session",
                "peerIdentity": "execution-peer",
                "runtimeAuthority": "code-state",
                "externalReadsRequired": False,
                "validationSource": "destination-bootstrap",
                "policyStatus": "passed",
                "policyVersion": SKILL_VERSION,
                "policyRuleCount": 1,
                "ready": True,
            }), encoding="utf-8")
            receipt_result = run(
                "receipt",
                *common,
                "--task-id", "cli-task",
                "--receipt-file", str(receipt_path),
                "--expected-role", "execution",
                "--expected-destination-id", "destination",
            )
            self.assertEqual(receipt_result.stderr, "")
            receipt_payload = json.loads(receipt_result.stdout)
            self.assertTrue(receipt_payload["ok"])
            self.assertEqual(receipt_payload["command"], "receipt")
            self.assertTrue(receipt_payload["validation"]["ready"])
            self.assertEqual(receipt_payload["snapshot"]["status"], "destination_ready")
            self.assertEqual(receipt_payload["snapshot"]["last_event"], "destination.ready")

            error_result = subprocess.run(
                [
                    sys.executable,
                    str(cli),
                    "plan",
                    *common,
                    "--task-id", "invalid-cli-task",
                    "--title", "Invalid CLI task",
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertNotEqual(error_result.returncode, 0)
            self.assertEqual(error_result.stdout, "")
            error_payload = json.loads(error_result.stderr)
            self.assertFalse(error_payload["ok"])

            unicode_project = Path(temp.name) / "中文项目"
            unicode_project.mkdir()
            unicode_result = subprocess.run(
                [
                    sys.executable,
                    str(cli),
                    "init",
                    "--project-root", str(unicode_project),
                    "--output-root", ".agent-workflow",
                ],
                capture_output=True,
                check=True,
            )
            unicode_payload = json.loads(unicode_result.stdout.decode("utf-8"))
            self.assertIn("中文项目", unicode_payload["outputRoot"])
        finally:
            temp.cleanup()

    def test_canonical_dispatch_binds_management_send_and_supervision(self):
        temp, project, store = self.make_store()
        try:
            self.make_contract_and_plan(store, execution_peer="execution-peer")
            store.bootstrap("task", "execution", "destination", "management-peer", "controlled-install", "native")
            store.destination_ready("task", self.receipt(), expected_role="execution", expected_destination_id="destination")
            snapshot = store.dispatch("task")
            self.assertEqual(snapshot["status"], "dispatched")
            self.assertEqual(snapshot["host_actions"][0]["actorRole"], "management")
            self.assertEqual(snapshot["host_actions"][0]["status"], "planned")
            self.assertEqual(snapshot["supervision"]["sequence"], ["wait", "observe", "correct"])
            self.assertEqual(next_action(snapshot)["action"], "send_dispatch_host_action")
            with self.assertRaises(WorkflowError):
                store.start_execution("task")
            self.complete_dispatch(store)
            store.start_execution("task")
        finally:
            temp.cleanup()

    def test_dispatch_order_and_review_wait_gate_with_read_fallback(self):
        temp, project, store = self.make_store()
        try:
            self.make_contract_and_plan(store)
            store.bootstrap("task", "execution", "destination", "management-peer", "controlled-install", "native")
            store.destination_ready("task", self.receipt(), expected_role="execution", expected_destination_id="destination")
            store.dispatch("task")
            state = store.state("task")
            dispatch_id = state["supervision"]["dispatchId"]
            send = next(item for item in state["host_actions"] if item["action"] == "send_message")
            wait = next(item for item in state["host_actions"] if item["action"] == "wait_threads")
            self.assertEqual(wait["args"]["timeoutMs"], 120000)
            before = len(store.events("task"))
            with self.assertRaises(WorkflowError):
                store.record_host_action_result("task", wait["actionId"], "observed", {"ok": True})
            self.assertEqual(len(store.events("task")), before)
            with self.assertRaises(WorkflowError):
                store.record_host_action_result("task", send["actionId"], "sent", {"ok": True}, actor="host")
            self.assertEqual(len(store.events("task")), before)
            store.record_host_action_result("task", send["actionId"], "sent", {"ok": True})
            self.assertEqual(next_action(store.snapshot("task"))["action"], "wait_dispatch_host_action")
            before = len(store.events("task"))
            with self.assertRaises(WorkflowError):
                store.start_execution("task")
            self.assertEqual(len(store.events("task")), before)
            with self.assertRaises(WorkflowError):
                store.record_host_action_result("task", wait["actionId"], "observed", {"timedOut": True})
            self.assertEqual(len(store.events("task")), before)
            self.assertEqual(next_action(store.snapshot("task"))["action"], "wait_dispatch_host_action")
            store.record_host_action_result("task", wait["actionId"], "observed", {"timedOut": False, "wake": {"reason": "turnCompleted"}})
            store.start_execution("task")
            store.report("task", report_ref="reports/task.md")
            store.review("task", "correction", reason="wait now observed")
            self.assertEqual(store.state("task")["status"], "correction")

            temp2, project2, store2 = self.make_store()
            try:
                self.make_contract_and_plan(store2)
                store2.bootstrap("task", "execution", "destination", "management-peer", "controlled-install", "native")
                store2.destination_ready("task", self.receipt(), expected_role="execution", expected_destination_id="destination")
                store2.dispatch("task")
                state2 = store2.state("task")
                send2 = next(item for item in state2["host_actions"] if item["action"] == "send_message")
                wait2 = next(item for item in state2["host_actions"] if item["action"] == "wait_threads")
                store2.record_host_action_result("task", send2["actionId"], "sent", {"ok": True})
                store2.record_host_action_result("task", wait2["actionId"], "failed", {"ok": False, "error": "wait unavailable"})
                read = next(item for item in store2.state("task")["host_actions"] if item["action"] == "read_thread")
                before = len(store2.events("task"))
                with self.assertRaises(WorkflowError):
                    store2.start_execution("task")
                self.assertEqual(len(store2.events("task")), before)
                store2.record_host_action_result("task", read["actionId"], "observed", {"ok": True, "snapshot": "state"})
                store2.start_execution("task")
                store2.report("task", report_ref="reports/task.md")
                store2.review("task", "correction", reason="read observed")
                self.assertEqual(store2.state("task")["status"], "correction")
            finally:
                temp2.cleanup()
        finally:
            temp.cleanup()

    def test_invalid_migration_step_and_forged_host_action_are_zero_write(self):
        temp, project, store = self.make_store()
        try:
            self.make_contract_and_plan(store)
            before = len(store.events("task"))
            sequence = build_migration_sequence("old", "new", {"type": "projectless"})
            sequence[0]["targetRole"] = "execution"
            with self.assertRaises(WorkflowError):
                store.record_migration_step("task", sequence[0])
            self.assertEqual(len(store.events("task")), before)
            forged = build_host_action(
                "send_message",
                "codex_app__send_message_to_thread",
                {"threadId": "execution", "prompt": "forged"},
                actor_role="execution",
                target_role="execution",
                phase="management_dispatch_send",
            )
            with self.assertRaises(WorkflowError):
                store.plan_host_action("task", forged, actor="execution")
            self.assertEqual(len(store.events("task")), before)
            with self.assertRaises(WorkflowError):
                store.request_delegation("task", "management", "implementation", "execution")
            self.assertEqual(len(store.events("task")), before)
        finally:
            temp.cleanup()

    def test_migration_event_order_and_real_thread_identity_are_zero_write(self):
        temp, project, store = self.make_store()
        try:
            self.make_contract_and_plan(store)
            sequence = build_migration_sequence("old", "new", {"type": "projectless"})
            before = len(store.events("task"))
            with self.assertRaises(WorkflowError):
                store.record_migration_step("task", sequence[1])
            self.assertEqual(len(store.events("task")), before)

            first = dict(sequence[0])
            first["status"] = "sent"
            first["result"] = {"ok": True, "threadId": "new", "chainId": first["chainId"], "actorSessionId": "old"}
            store.record_migration_step("task", first)
            accepted = dict(sequence[1])
            accepted["status"] = "accepted"
            accepted["accepted"] = True
            store.record_migration_step("task", accepted)
            create = dict(sequence[2])
            create["status"] = "observed"
            create["result"] = {"ok": True, "threadId": "execution-real", "chainId": create["chainId"], "actorSessionId": "new"}
            wrong_create = dict(create)
            wrong_create["actorSessionId"] = "other-management"
            wrong_create["result"] = dict(create["result"])
            wrong_create["result"]["actorSessionId"] = "other-management"
            before = len(store.events("task"))
            with self.assertRaises(WorkflowError):
                store.record_migration_step("task", wrong_create)
            self.assertEqual(len(store.events("task")), before)
            store.record_migration_step("task", create)
            final = dict(sequence[3])
            final["deferred"] = False
            final["args"] = {"threadId": "wrong-execution", "prompt": "execution dispatch"}
            final["status"] = "sent"
            final["result"] = {"ok": True, "threadId": "wrong-execution", "chainId": final["chainId"], "actorSessionId": "new"}
            before = len(store.events("task"))
            with self.assertRaises(WorkflowError):
                store.record_migration_step("task", final)
            self.assertEqual(len(store.events("task")), before)
            final["args"]["threadId"] = "execution-real"
            final["result"]["threadId"] = "execution-real"
            wrong_final = dict(final)
            wrong_final["actorSessionId"] = "other-management"
            wrong_final["result"] = dict(final["result"])
            wrong_final["result"]["actorSessionId"] = "other-management"
            before = len(store.events("task"))
            with self.assertRaises(WorkflowError):
                store.record_migration_step("task", wrong_final)
            self.assertEqual(len(store.events("task")), before)
            store.record_migration_step("task", final)
            self.assertTrue(store.snapshot("task")["migration"]["completed"])
            self.assertFalse(store.audit("task")["migration"]["evidenceRequired"])
        finally:
            temp.cleanup()

    def test_cli_code_handoff_end_to_end(self):
        temp = tempfile.TemporaryDirectory(prefix="uaw-cli-handoff-")
        try:
            project = Path(temp.name) / "中文交接项目"
            project.mkdir()
            cli = SCRIPT_DIR / "uaw.py"
            skill_dir = SCRIPT_DIR.parent

            def run(*args):
                return subprocess.run([sys.executable, str(cli), *args], capture_output=True, check=True)

            common = ("--project-root", str(project), "--output-root", ".agent-workflow")
            run("init", *common)
            run("plan", *common, "--task-id", "handoff-task", "--title", "Handoff", "--objective", "continue from code state")
            run(
                "bootstrap",
                *common,
                "--task-id", "handoff-task",
                "--role", "execution",
                "--destination-id", "destination-8",
                "--peer-identity", "management-8",
                "--install-source", str(skill_dir),
                "--capability-mode", "native",
            )
            bootstrap_payload = json.loads(run(
                "destination-bootstrap",
                "--skill-dir", str(skill_dir),
                "--role", "execution",
                "--destination-id", "destination-8",
                "--stable-session-id", "destination-8",
                "--peer-identity", "management-8",
                "--tools",
                "create_thread", "send_message_to_thread", "wait_threads", "read_thread", "set_thread_archived",
            ).stdout.decode("utf-8"))
            receipt_file = project / ".agent-workflow" / "tmp" / "destination-receipt.json"
            receipt_file.write_text(json.dumps(bootstrap_payload["receipt"], ensure_ascii=False), encoding="utf-8")
            run(
                "receipt",
                *common,
                "--task-id", "handoff-task",
                "--receipt-file", str(receipt_file),
                "--expected-role", "execution",
                "--expected-destination-id", "destination-8",
            )
            continuity_file = project / ".agent-workflow" / "tmp" / "continuity.json"
            continuity_file.write_text(json.dumps({
                "project": "generic-project",
                "objective": "continue from code state",
                "currentState": "destination_ready",
                "nextAction": "wait for management contract",
                "facts": ["current state accepted"],
                "protectedBoundaries": ["preserve user data"],
                "forbiddenActions": ["no external calls"],
                "pendingDecisions": [],
                "requiredExternalReads": [],
            }, ensure_ascii=False), encoding="utf-8")
            exported = json.loads(run(
                "handoff-export",
                *common,
                "--task-id", "handoff-task",
                "--continuity-file", str(continuity_file),
                "--source-session-id", "source-8",
                "--destination-session-id", "destination-8",
                "--source-role", "management",
                "--destination-role", "execution",
                "--management-peer", "management-8",
                "--execution-peer", "execution-8",
                "--capability-mode", "native",
            ).stdout.decode("utf-8"))
            received = json.loads(run(
                "handoff-receive",
                *common,
                "--task-id", "handoff-task",
                "--bundle-file", exported["bundlePath"],
                "--expected-destination-id", "destination-8",
                "--expected-role", "execution",
            ).stdout.decode("utf-8"))
            self.assertEqual(received["receipt"]["status"], "CODE_HANDOFF_ACCEPTED")
            self.assertFalse(received["externalReadsRequired"])
            self.assertEqual(received["receipt"]["context"]["continuity"]["facts"], ["current state accepted"])
            accepted = json.loads(run(
                "handoff-accept",
                *common,
                "--task-id", "handoff-task",
                "--role", "execution",
                "--peer-identity", "source-8",
            ).stdout.decode("utf-8"))
            self.assertTrue(accepted["handoff_accepted"])
        finally:
            temp.cleanup()

    def test_source_migration_preserves_structure_but_is_not_runtime_authority(self):
        temp = tempfile.TemporaryDirectory(prefix="uaw-source-migration-")
        try:
            project = Path(temp.name) / "project"
            project.mkdir()
            sources = []
            for name, body in (
                ("general.md", "# General\n\n- plan first\n"),
                ("management.md", "# Management\n\n- speak plainly\n"),
                ("execution.md", "# Execution\n\n- api_key=SECRET\n"),
            ):
                path = Path(temp.name) / name
                path.write_text(body, encoding="utf-8")
                sources.append(path)
            cli = SCRIPT_DIR / "uaw.py"
            result = subprocess.run(
                [
                    sys.executable,
                    str(cli),
                    "source-migrate",
                    "--project-root", str(project),
                    "--output-root", ".agent-workflow",
                    "--skill-dir", str(SCRIPT_DIR.parent),
                    "--general-source", str(sources[0]),
                    "--management-source", str(sources[1]),
                    "--execution-source", str(sources[2]),
                ],
                capture_output=True,
                check=True,
            )
            payload = json.loads(result.stdout.decode("utf-8"))
            self.assertTrue(payload["losslessNonBlankCoverage"])
            self.assertFalse(payload["runtimeUse"])
            capsule = json.loads(Path(payload["capsulePath"]).read_text(encoding="utf-8"))
            self.assertEqual(capsule["sourceCount"], 3)
            self.assertEqual(capsule["structuredRecords"], 6)
            self.assertNotIn("SECRET", json.dumps(capsule))
            self.assertFalse(capsule["externalMarkdownRequired"])
        finally:
            temp.cleanup()

    def test_output_guard_and_install_plan(self):
        temp = tempfile.TemporaryDirectory(prefix="uaw-path-")
        try:
            project = Path(temp.name) / "project"
            project.mkdir()
            with self.assertRaises(WorkflowError):
                resolve_output_root(project, project)
            source = project / "source"
            target = project / "target"
            source.mkdir()
            target.mkdir()
            (target / "VERSION").write_text("0.9.0", encoding="utf-8")
            plan = installation_plan(target, source)
            self.assertEqual(plan["action"], "update_required")
            packet = make_bootstrap_packet("execution", "dest", "peer", "source", "manual")
            self.assertEqual(packet["skillVersion"], SKILL_VERSION)
        finally:
            temp.cleanup()

    def test_secrets_are_redacted_without_returning_values(self):
        value = "Authorization: Bearer token-value api_key=secret-value"
        redacted = redact_text(value)
        self.assertNotIn("token-value", redacted)
        self.assertNotIn("secret-value", redacted)
        self.assertGreaterEqual(sensitive_match_count(value), 2)


if __name__ == "__main__":
    unittest.main()
