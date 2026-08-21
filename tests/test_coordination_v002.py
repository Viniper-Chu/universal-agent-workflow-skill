import copy
import sys
import unittest
from pathlib import Path


SCRIPT_DIR = Path(__file__).resolve().parents[1] / "packages" / "universal-agent-workflow" / "scripts"
sys.path.insert(0, str(SCRIPT_DIR))

from coordination_policy import (  # noqa: E402
    CoordinationPolicyError,
    build_host_action,
    build_migration_sequence,
    build_supervision_plan,
    classify_wait_result,
    derive_execution_settings,
    record_host_action,
    validate_create_target,
    validate_delegation,
    validate_migration_sequence,
    validate_migration_step,
)


PROJECT_TARGET = {
    "type": "project",
    "projectId": "project-1",
    "environment": {"type": "local"},
}


class CoordinationPolicyV002Tests(unittest.TestCase):
    def test_native_host_args_are_tool_exact(self):
        send = build_host_action(
            "send_message",
            "codex_app__send_message_to_thread",
            {"threadId": "new-management", "prompt": "handoff", "hostId": "host-1"},
            actor_role="management",
            actor_session_id="old-management",
            chain_id="chain-1",
            phase="old_management_to_new_management_message",
        )
        self.assertEqual(set(send["args"]), {"threadId", "prompt", "hostId"})
        self.assertNotIn("targetRole", send["args"])
        self.assertNotIn("targetThreadId", send["args"])
        self.assertEqual(record_host_action(send, "sent", {"ok": True, "threadId": "new-management", "chainId": send["chainId"], "actorSessionId": "old-management"})["status"], "sent")

        wait = build_host_action(
            "wait_threads",
            "codex_app__wait_threads",
            {"targets": [{"threadId": "execution", "hostId": "host-1", "afterCursor": "c1"}], "timeoutMs": 0},
            actor_role="management",
            phase="management_dispatch_wait",
        )
        self.assertEqual(set(wait["args"]), {"targets", "timeoutMs"})
        self.assertEqual(set(wait["args"]["targets"][0]), {"threadId", "hostId", "afterCursor"})

        read = build_host_action(
            "read_thread",
            "codex_app__read_thread",
            {"threadId": "execution", "cursor": "c1", "includeOutputs": True, "turnLimit": 2},
            actor_role="management",
            phase="management_dispatch_read_fallback",
        )
        self.assertEqual(set(read["args"]), {"threadId", "cursor", "includeOutputs", "turnLimit"})

        create = build_host_action(
            "create_thread",
            "codex_app__create_thread",
            {"prompt": "create", "target": PROJECT_TARGET, "title": "Execution"},
            actor_role="management",
            phase="new_management_creates_execution",
        )
        self.assertEqual(set(create["args"]), {"prompt", "target", "title"})
        self.assertNotIn("threadId", create["args"])
        self.assertNotIn("targetRole", create["args"])

        for bad in (
            {"threadId": "x", "message": "wrong"},
            {"targetThreadId": "x", "prompt": "wrong"},
            {"threadId": "x", "prompt": "wrong", "model": "override"},
        ):
            with self.assertRaises(CoordinationPolicyError):
                build_host_action("send_message", "codex_app__send_message_to_thread", bad, actor_role="management", phase="test")

    def test_create_target_requires_real_native_shape(self):
        self.assertEqual(validate_create_target(PROJECT_TARGET), PROJECT_TARGET)
        self.assertEqual(validate_create_target({"type": "projectless"})["type"], "projectless")
        self.assertEqual(validate_create_target({"type": "chatgptWorkCloud", "projectId": "cloud-1"})["type"], "chatgptWorkCloud")
        for bad in ({"type": "unknown"}, {"type": "project", "projectId": "p"}, {"type": "project", "projectId": "p", "environment": {"type": "bad"}}):
            with self.assertRaises(CoordinationPolicyError):
                validate_create_target(bad)

    def test_migration_plan_has_four_uncompleted_stages_without_execution_id_placeholder(self):
        sequence = build_migration_sequence("old-m", "new-m", PROJECT_TARGET)
        self.assertEqual(len(sequence), 4)
        self.assertTrue(all(step["completed"] is False for step in sequence))
        self.assertNotIn("execution-id", repr(sequence))
        self.assertNotIn("executionId", repr(sequence))
        self.assertNotIn("create_result", repr(sequence))
        self.assertEqual(sequence[0]["args"]["threadId"], "new-m")
        self.assertEqual(sequence[0]["actorSessionId"], "old-m")
        self.assertEqual(sequence[1]["actorSessionId"], "new-m")
        self.assertEqual(sequence[2]["actorSessionId"], "new-m")
        self.assertEqual(sequence[3]["actorSessionId"], "new-m")
        self.assertNotIn("threadId", sequence[2]["args"])
        self.assertNotIn("threadId", sequence[3]["args"])
        validation = validate_migration_sequence(sequence)
        self.assertTrue(validation["ok"])
        self.assertFalse(validation["completed"])
        self.assertTrue(validation["evidenceRequired"])

    def test_migration_real_identity_chain_and_wrong_order_reject_zero_write_shape(self):
        sequence = build_migration_sequence("old-m", "new-m", PROJECT_TARGET)
        sequence[0] = record_host_action(sequence[0], "sent", {"ok": True, "threadId": "new-m", "chainId": sequence[0]["chainId"], "actorSessionId": "old-m"})
        sequence[1]["status"] = "accepted"
        sequence[1]["accepted"] = True
        sequence[2] = record_host_action(sequence[2], "sent", {"ok": True, "chainId": sequence[2]["chainId"], "actorSessionId": "new-m"})
        sequence[2] = record_host_action(sequence[2], "observed", {"ok": True, "threadId": "execution-real", "chainId": sequence[2]["chainId"], "actorSessionId": "new-m"})
        sequence[3]["args"] = {"threadId": "execution-real", "prompt": "execution dispatch"}
        sequence[3]["deferred"] = False
        sequence[3] = record_host_action(sequence[3], "sent", {"ok": True, "threadId": "execution-real", "chainId": sequence[3]["chainId"], "actorSessionId": "new-m"})
        completed = validate_migration_sequence(sequence)
        self.assertTrue(completed["completed"])
        self.assertFalse(completed["evidenceRequired"])
        wrong = copy.deepcopy(sequence)
        wrong[3]["args"]["threadId"] = "other-execution"
        with self.assertRaises(CoordinationPolicyError):
            validate_migration_sequence(wrong)
        wrong = copy.deepcopy(sequence)
        wrong[1]["actorSessionId"] = "other-management"
        with self.assertRaises(CoordinationPolicyError):
            validate_migration_sequence(wrong)
        wrong = copy.deepcopy(sequence)
        wrong[2]["actorSessionId"] = "other-management"
        wrong[2]["result"]["actorSessionId"] = "other-management"
        with self.assertRaises(CoordinationPolicyError):
            validate_migration_sequence(wrong)
        wrong = copy.deepcopy(sequence)
        wrong[3]["actorSessionId"] = "other-management"
        wrong[3]["result"]["actorSessionId"] = "other-management"
        with self.assertRaises(CoordinationPolicyError):
            validate_migration_sequence(wrong)

    def test_partial_migration_step_checks_roles_and_gate(self):
        sequence = build_migration_sequence("old-m", "new-m", PROJECT_TARGET)
        invalid = copy.deepcopy(sequence[0])
        invalid["actorRole"] = "execution"
        with self.assertRaises(CoordinationPolicyError):
            validate_migration_step(invalid, 0)
        invalid = copy.deepcopy(sequence[1])
        invalid["accepted"] = True
        invalid["status"] = "accepted"
        invalid["actorSessionId"] = "other-management"
        with self.assertRaises(CoordinationPolicyError):
            validate_migration_step(invalid, 1)
        invalid = copy.deepcopy(sequence[2])
        invalid["args"]["model"] = "override"
        with self.assertRaises(CoordinationPolicyError):
            validate_migration_step(invalid, 2)

    def test_settings_inherit_then_preserve_user_adjustment(self):
        management = {"model": "management-model", "reasoning": "high", "locale": "zh-CN"}
        inherited = derive_execution_settings(management)
        self.assertEqual(inherited["settings"], management)
        self.assertEqual(inherited["managementSnapshot"], management)
        self.assertTrue(inherited["evidenceRequired"])
        self.assertFalse(inherited["evidenceProven"])
        user = {"locale": "en-US"}
        preserved = derive_execution_settings(management, user)
        self.assertEqual(preserved["settings"], {"model": "management-model", "reasoning": "high", "locale": "en-US"})
        self.assertEqual(preserved["userOverrides"], user)
        self.assertEqual(management["model"], "management-model")

    def test_migration_threads_management_snapshot_and_partial_user_overrides(self):
        management = {
            "model": "management-model",
            "reasoning": "high",
            "locale": "zh-CN",
            "ui": {"theme": "dark", "density": "comfortable"},
        }
        user = {"locale": "en-US", "ui": {"density": "compact"}}
        sequence = build_migration_sequence(
            "old-m",
            "new-m",
            PROJECT_TARGET,
            management_settings=management,
            user_settings=user,
            inheritance_evidence={
                "proven": True,
                "readOnly": True,
                "source": "host-settings-snapshot",
                "destinationId": "execution-observed",
                "observedSettings": {
                    "model": "management-model",
                    "reasoning": "high",
                    "locale": "en-US",
                    "ui": {"theme": "dark", "density": "compact"},
                },
            },
        )
        create = sequence[2]
        self.assertEqual(create["settings"]["managementSnapshot"], management)
        self.assertEqual(create["settings"]["userOverrides"], user)
        self.assertEqual(
            create["settings"]["settings"],
            {
                "model": "management-model",
                "reasoning": "high",
                "locale": "en-US",
                "ui": {"theme": "dark", "density": "compact"},
            },
        )
        self.assertFalse(create["settingsPolicy"]["evidenceRequired"])
        self.assertEqual(create["settingsPolicy"]["evidenceStatus"], "PROVEN_MATCH")
        self.assertNotIn("model", create["args"])
        self.assertNotIn("thinking", create["args"])
        self.assertNotIn("reasoning", create["args"])
        self.assertTrue(validate_migration_sequence(sequence)["ok"])

    def test_supervision_requires_wait_observe_correct_and_read_fallback(self):
        plan = build_supervision_plan("dispatch-1")
        self.assertEqual(plan["sequence"], ["wait", "observe", "correct"])
        fallback = build_supervision_plan("dispatch-1", {"ok": False, "error": "wait unavailable"})
        self.assertEqual(fallback["nextAction"], "read")
        self.assertEqual(fallback["failureClass"], "orchestration_harness_failure")
        self.assertFalse(fallback["writeAllowed"])
        recovered = build_supervision_plan("dispatch-1", {"ok": False, "error": "wait unavailable"}, {"ok": True})
        self.assertEqual(recovered["nextAction"], "observe")

    def test_wait_result_semantics_timeout_turn_completed_and_tool_failure(self):
        timeout = build_supervision_plan("dispatch-1", {"timedOut": True})
        self.assertEqual(timeout["nextAction"], "wait")
        self.assertFalse(timeout["reviewReady"])
        self.assertIsNone(timeout["failureClass"])
        completed = build_supervision_plan("dispatch-1", {"timedOut": False, "wake": {"reason": "turnCompleted"}})
        self.assertEqual(completed["nextAction"], "observe")
        self.assertTrue(completed["reviewReady"])
        failed = build_supervision_plan("dispatch-1", {"ok": False, "error": "tool unavailable"})
        self.assertEqual(failed["nextAction"], "read")
        self.assertEqual(failed["failureClass"], "orchestration_harness_failure")

    def test_wait_success_requires_wake_and_timeout_cannot_be_observed(self):
        with self.assertRaises(CoordinationPolicyError):
            classify_wait_result({"timedOut": False, "reason": "turnCompleted"})
        self.assertEqual(
            classify_wait_result({"timedOut": False, "reason": "turnCompleted"}, allow_legacy_shape=True)["kind"],
            "observed",
        )
        wait = build_host_action(
            "wait_threads",
            "codex_app__wait_threads",
            {"targets": [{"threadId": "execution"}], "timeoutMs": 120000},
            actor_role="management",
            phase="management_dispatch_wait",
        )
        with self.assertRaises(CoordinationPolicyError):
            record_host_action(wait, "observed", {"timedOut": True})

    def test_delegation_is_child_role_and_category_bound(self):
        self.assertTrue(validate_delegation("management", "outline", "management")["allowed"])
        self.assertTrue(validate_delegation("management", "contract", "reviewer")["allowed"])
        self.assertTrue(validate_delegation("management", "review", "reviewer")["allowed"])
        self.assertTrue(validate_delegation("execution", "parallel_implementation", "execution")["allowed"])
        for parent, category, child in (
            ("management", "outline", "execution"),
            ("management", "implementation", "execution"),
            ("execution", "implementation", "execution"),
            ("execution", "planning", "execution"),
            ("execution", "acceptance", "execution"),
            ("execution", "review", "reviewer"),
        ):
            decision = validate_delegation(parent, category, child)
            self.assertFalse(decision["allowed"])
            self.assertFalse(decision["require"]["eventWriteAllowed"])
            self.assertTrue(decision["require"]["gate"])


if __name__ == "__main__":
    unittest.main()
