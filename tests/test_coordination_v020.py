import json
import sys
import tempfile
import unittest
from pathlib import Path


SCRIPT_DIR = Path(__file__).resolve().parents[1] / "packages" / "universal-agent-workflow" / "scripts"
sys.path.insert(0, str(SCRIPT_DIR))

from bootstrap import SKILL_NAME, SKILL_VERSION  # noqa: E402
from coordination_policy import (  # noqa: E402
    CoordinationPolicyError,
    build_delegation_contract,
    build_supervision_plan,
    derive_execution_settings,
)
from workflow_engine import WorkflowError, WorkflowStore, initialize_project, make_contract, next_action  # noqa: E402


class CoordinationPolicyV020Tests(unittest.TestCase):
    def make_store(self, *, execution_peer="execution-1"):
        temp = tempfile.TemporaryDirectory(prefix="uaw-v020-")
        project = Path(temp.name) / "project"
        project.mkdir()
        root = project / ".agent-workflow"
        initialize_project(project, root)
        store = WorkflowStore(project, root)
        contract = make_contract("task", "Task", "exercise 0.2.0 coordination", complexity="complex")
        contract["execution_peer"] = execution_peer
        store.create_contract(contract)
        store.plan("task", contract["plan_steps"])
        return temp, store

    def receipt(self, *, destination="execution-1", peer="management-1", role="execution"):
        return {
            "skillName": SKILL_NAME,
            "skillVersion": SKILL_VERSION,
            "role": role,
            "installPath": "controlled-install",
            "selftestStatus": "passed",
            "quickValidateStatus": "passed",
            "capabilityMode": "native",
            "destinationId": destination,
            "stableSessionId": destination,
            "peerIdentity": peer,
            "runtimeAuthority": "code-state",
            "externalReadsRequired": False,
            "validationSource": "destination-bootstrap",
            "policyStatus": "passed",
            "policyVersion": SKILL_VERSION,
            "policyRuleCount": 1,
            "ready": True,
        }

    def prepare_dispatch(self, store):
        store.bootstrap("task", "execution", "execution-1", "management-1", "controlled-install", "native")
        store.destination_ready("task", self.receipt())
        store.dispatch("task", dispatch_id="dispatch-1")
        state = store.state("task")
        send = next(item for item in state["host_actions"] if item["action"] == "send_message")
        wait = next(item for item in state["host_actions"] if item["action"] == "wait_threads")
        return send, wait

    def acknowledge(self, store, send):
        return store.record_host_action_result("task", send["actionId"], "sent", {
            "ok": True,
            "deliveryAcknowledged": True,
            "messageId": send["messageId"],
            "threadId": send["args"]["threadId"],
        })

    def test_delivery_ack_is_single_start_gate_and_wrong_identity_is_zero_write(self):
        temp, store = self.make_store()
        self.addCleanup(temp.cleanup)
        send, _ = self.prepare_dispatch(store)
        before = len(store.events("task"))
        with self.assertRaisesRegex(WorkflowError, "deliveryAcknowledged"):
            store.record_host_action_result("task", send["actionId"], "sent", {"ok": True})
        self.assertEqual(len(store.events("task")), before)
        with self.assertRaisesRegex(WorkflowError, "messageId"):
            store.record_host_action_result("task", send["actionId"], "sent", {
                "ok": True,
                "deliveryAcknowledged": True,
                "messageId": "wrong",
                "threadId": "execution-1",
            })
        self.assertEqual(len(store.events("task")), before)
        snapshot = self.acknowledge(store, send)
        self.assertEqual(len(snapshot["delivery_acknowledgements"]), 1)
        self.assertEqual(next_action(snapshot)["action"], "start_execution")

    def test_correction_uses_next_epoch_without_second_start_and_review_is_fresh(self):
        temp, store = self.make_store()
        self.addCleanup(temp.cleanup)
        send, wait = self.prepare_dispatch(store)
        self.acknowledge(store, send)
        store.start_execution("task")
        store.record_host_action_result("task", wait["actionId"], "observed", {
            "timedOut": False,
            "wake": {"reason": "turnCompleted", "cursor": "cursor-1"},
        })
        store.report("task", report_ref="reports/task-r1.md", evidence_delta={"added": ["baseline"]})
        store.review("task", "correction", reason="narrow the first fault", correction_id="correction-1", evidence_delta={"replace": ["rootCause"]})

        state = store.state("task")
        self.assertEqual(state["status"], "executing")
        self.assertEqual(state["supervision"]["supervisionEpoch"], 2)
        self.assertEqual(sum(event["event"] == "execution.started" for event in store.events("task")), 1)
        correction_send = next(
            item for item in state["host_actions"]
            if item.get("supervisionEpoch") == 2 and item["action"] == "send_message"
        )
        correction_wait = next(
            item for item in state["host_actions"]
            if item.get("supervisionEpoch") == 2 and item["action"] == "wait_threads"
        )
        self.acknowledge(store, correction_send)
        store.report("task", report_ref="reports/task-r2.md", evidence_delta={"added": ["red-green"]})
        with self.assertRaisesRegex(WorkflowError, "same-dispatch wait"):
            store.review("task", "accepted", independent=True, reviewer_id="reviewer-1")
        store.record_host_action_result("task", correction_wait["actionId"], "observed", {
            "timedOut": False,
            "wake": {"reason": "turnCompleted", "cursor": "cursor-2"},
        })
        current = store.state("task")
        with self.assertRaisesRegex(WorkflowError, "stale"):
            store._append("task", "review.accepted", {
                "independent": True,
                "checkpoint": False,
                "reportRevision": 1,
                "reportEventCursor": current["execution_reports"][0]["eventCursor"],
                "workState": "reviewing",
                "reviewerId": "reviewer-1",
            }, "reviewer")
        accepted = store.review("task", "accepted", independent=True, reviewer_id="reviewer-1", actor="reviewer")
        self.assertEqual(accepted["review_binding"]["reportRevision"], 2)
        self.assertEqual(len(accepted["execution_reports"]), 2)
        self.assertEqual(accepted["corrections"][0]["correctionId"], "correction-1")

    def test_delegation_ownership_rejects_overlap_and_releases_after_aggregation(self):
        temp, store = self.make_store()
        self.addCleanup(temp.cleanup)
        before = len(store.events("task"))
        with self.assertRaisesRegex(WorkflowError, "structured spec"):
            store.request_delegation("task", "execution", "parallel_implementation", "execution")
        self.assertEqual(len(store.events("task")), before)
        first = {
            "delegationId": "branch-1",
            "parentAgentId": "execution-parent",
            "childAgentId": "execution-child-1",
            "ownershipScopes": ["server/import"],
            "accessMode": "implementation",
            "expectedOutput": "tested parser change",
            "dependencies": [],
            "aggregatorId": "execution-parent",
        }
        store.request_delegation("task", "execution", "parallel_implementation", "execution", first)
        overlapping = {
            **first,
            "delegationId": "branch-2",
            "childAgentId": "execution-child-2",
            "ownershipScopes": ["server/import/parser"],
        }
        before = len(store.events("task"))
        with self.assertRaisesRegex(WorkflowError, "overlaps"):
            store.request_delegation("task", "execution", "parallel_implementation", "execution", overlapping)
        self.assertEqual(len(store.events("task")), before)
        windows_overlap = {
            **first,
            "delegationId": "branch-windows",
            "childAgentId": "execution-child-windows",
            "ownershipScopes": ["SERVER\\IMPORT\\NORMALIZER"],
        }
        with self.assertRaisesRegex(WorkflowError, "overlaps"):
            store.request_delegation("task", "execution", "parallel_implementation", "execution", windows_overlap)
        self.assertEqual(len(store.events("task")), before)
        store.complete_delegation("task", "branch-1", "aggregated by parent", "execution")
        accepted = store.request_delegation("task", "execution", "parallel_implementation", "execution", overlapping)
        self.assertEqual(accepted["delegations"][-1]["delegationId"], "branch-2")
        with self.assertRaisesRegex(CoordinationPolicyError, "cannot delegate implementation"):
            build_delegation_contract(
                "management",
                "outline",
                "management",
                parent_agent_id="management-parent",
                child_agent_id="management-child",
                ownership_scopes=["product/source"],
                access_mode="implementation",
                expected_output="implementation",
                dependencies=[],
                aggregator_id="management-parent",
            )

    def test_bootstrap_output_is_direct_receipt_input_and_peer_is_derived(self):
        temp, store = self.make_store()
        self.addCleanup(temp.cleanup)
        store.bootstrap("task", "execution", "execution-1", "management-1", "controlled-install", "native")
        wrapper = {
            "ok": True,
            "command": "destination-bootstrap",
            "receipt": self.receipt(),
            "runtimePolicy": {"role": "execution"},
            "externalReadsRequired": False,
        }
        wrong = json.loads(json.dumps(wrapper))
        wrong["receipt"]["peerIdentity"] = "execution-1"
        before = len(store.events("task"))
        with self.assertRaisesRegex(WorkflowError, "peer"):
            store.destination_ready("task", wrong)
        self.assertEqual(len(store.events("task")), before)
        snapshot = store.destination_ready("task", wrapper)
        self.assertTrue(snapshot["destination_ready"])
        self.assertEqual(store.events("task")[-1]["actor"], "execution")
        self.assertEqual(snapshot["readiness_receipt"]["peerIdentity"], "management-1")

    def test_read_only_settings_proof_never_mutates_user_choice(self):
        management = {"model": "same-as-management", "locale": "zh-CN"}
        user = {"locale": "en-US"}
        with self.assertRaisesRegex(CoordinationPolicyError, "read-only"):
            derive_execution_settings(management, user, inheritance_evidence={"proven": True})
        result = derive_execution_settings(management, user, inheritance_evidence={
            "proven": True,
            "readOnly": True,
            "source": "host-settings-read",
            "destinationId": "execution-1",
            "observedSettings": {"model": "user-changed-model", "locale": "en-US"},
        })
        self.assertEqual(result["evidenceStatus"], "PROVEN_USER_STATE_DIFFERS")
        self.assertFalse(result["mutationAllowed"])
        self.assertEqual(result["settings"], {"model": "same-as-management", "locale": "en-US"})

    def test_no_progress_escalates_same_execution_and_timeout_is_not_evidence(self):
        timed_out = build_supervision_plan(
            "dispatch-1",
            {"timedOut": True},
            supervision_epoch=4,
            progress_cursor="cursor-1",
            previous_progress_cursor="cursor-1",
            stagnant_epochs=2,
        )
        self.assertEqual(timed_out["stagnantEpochs"], 2)
        self.assertEqual(timed_out["nextAction"], "wait")
        stagnant = build_supervision_plan(
            "dispatch-1",
            {"timedOut": False, "wake": {"reason": "turnCompleted"}},
            supervision_epoch=4,
            progress_cursor="cursor-1",
            previous_progress_cursor="cursor-1",
            stagnant_epochs=2,
        )
        self.assertEqual(stagnant["nextAction"], "request_progress_diagnosis")
        self.assertEqual(stagnant["escalation"]["target"], "same_execution")
        self.assertFalse(stagnant["escalation"]["createReplacementSession"])


if __name__ == "__main__":
    unittest.main()
