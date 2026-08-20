import copy
import sys
import tempfile
import unittest
from pathlib import Path


SCRIPT_DIR = Path(__file__).resolve().parents[1] / "packages" / "universal-agent-workflow" / "scripts"
sys.path.insert(0, str(SCRIPT_DIR))

from bootstrap import SKILL_NAME, SKILL_VERSION  # noqa: E402
from repair_policy import (  # noqa: E402
    RepairPolicyError,
    build_repair_policy,
    evaluate_repair_evidence,
    validate_repair_policy,
)
from workflow_engine import WorkflowError, WorkflowStore, initialize_project, make_contract  # noqa: E402


class RepairPolicyTests(unittest.TestCase):
    def policy(self):
        return build_repair_policy(
            data_recovery_required=True,
            real_data_write=True,
            identity_keys=["source identity", "current tuple", "attempt version"],
            validation_checks=["quality", "source", "projection"],
            conservation_scopes=["target", "same-container non-target", "other containers"],
            preserve_external_call_ledger=True,
        )

    def evidence(self, *, root_closed):
        return {
            "schemaVersion": 1,
            "rootCause": {
                "originalFailureReproduced": root_closed,
                "firstFaultLayer": "shared producer" if root_closed else "",
                "sharedRootCauseFixed": root_closed,
                "regression": {"redBeforeFix": root_closed, "greenAfterFix": root_closed},
                "directConsumersPassed": root_closed,
            },
            "dataRecovery": {
                "status": "recovered",
                "isolatedProductionRebuild": True,
                "identityRebound": True,
                "identityKeys": ["source identity", "current tuple", "attempt version"],
                "sharedValidatorsRecomputed": True,
                "sharedValidationChecks": ["quality", "source", "projection"],
                "snapshotSucceeded": True,
                "guardFailuresZeroWrite": True,
                "conservation": {
                    "passed": True,
                    "scopes": ["target", "same-container non-target", "other containers"],
                    "externalCallLedgerPreserved": True,
                },
            },
        }

    def receipt(self):
        return {
            "skillName": SKILL_NAME,
            "skillVersion": SKILL_VERSION,
            "role": "execution",
            "installPath": "controlled-install",
            "selftestStatus": "passed",
            "quickValidateStatus": "passed",
            "capabilityMode": "native",
            "destinationId": "execution-1",
            "stableSessionId": "execution-1",
            "peerIdentity": "management-1",
            "runtimeAuthority": "code-state",
            "externalReadsRequired": False,
            "validationSource": "destination-bootstrap",
            "policyStatus": "passed",
            "policyVersion": SKILL_VERSION,
            "policyRuleCount": 1,
            "ready": True,
        }

    def test_data_recovery_cannot_substitute_for_root_cause_closure(self):
        outcome = evaluate_repair_evidence(self.policy(), self.evidence(root_closed=False))
        self.assertTrue(outcome["dataRecovered"])
        self.assertFalse(outcome["productRootCauseClosed"])
        self.assertFalse(outcome["complete"])
        self.assertEqual(outcome["outcome"], "data_recovered_product_root_cause_open")

    def test_real_data_recovery_contract_requires_rebuild_and_guards(self):
        with self.assertRaisesRegex(RepairPolicyError, "require dataRecoveryRequired"):
            build_repair_policy(real_data_write=True)
        invalid = self.policy()
        invalid["recovery"]["identityKeys"] = []
        with self.assertRaisesRegex(RepairPolicyError, "identityKeys must not be empty"):
            validate_repair_policy(invalid)
        invalid = self.policy()
        invalid["recovery"]["snapshotBeforeWrite"] = False
        with self.assertRaisesRegex(RepairPolicyError, "snapshot-first"):
            validate_repair_policy(invalid)

    def test_repair_contract_rejects_final_acceptance_until_both_outcomes_pass(self):
        with tempfile.TemporaryDirectory(prefix="uaw-repair-") as temp:
            project = Path(temp) / "project"
            project.mkdir()
            root = project / ".agent-workflow"
            initialize_project(project, root)
            store = WorkflowStore(project, root)
            contract = make_contract(
                "repair-task",
                "Repair task",
                "fix the production root cause and recover affected state",
                complexity="complex",
                repair_policy=self.policy(),
            )
            store.create_contract(contract)
            store.plan("repair-task", contract["plan_steps"])
            store.bootstrap("repair-task", "execution", "execution-1", "management-1", "controlled-install", "native")
            store.destination_ready("repair-task", self.receipt(), "execution", "execution-1")
            store.dispatch("repair-task")
            state = store.state("repair-task")
            dispatch_id = state["supervision"]["dispatchId"]
            send = next(item for item in state["host_actions"] if item["dispatchId"] == dispatch_id and item["action"] == "send_message")
            wait = next(item for item in state["host_actions"] if item["dispatchId"] == dispatch_id and item["action"] == "wait_threads")
            store.record_host_action_result("repair-task", send["actionId"], "sent", {"ok": True})
            store.record_host_action_result("repair-task", wait["actionId"], "observed", {"ok": True})
            store.start_execution("repair-task")
            with self.assertRaisesRegex(WorkflowError, "requires code-validated repair evidence"):
                store.report("repair-task", report_ref="reports/repair.md")
            self.assertEqual(store.state("repair-task")["status"], "executing")

            store.report("repair-task", report_ref="reports/repair.md", repair_evidence=self.evidence(root_closed=False))
            with self.assertRaisesRegex(WorkflowError, "product root-cause closure"):
                store.review("repair-task", "accepted", independent=True)
            state = store.state("repair-task")
            self.assertEqual(state["repair_outcome"]["outcome"], "data_recovered_product_root_cause_open")
            self.assertEqual(state["status"], "reviewing")

            store.review("repair-task", "correction", reason="close the shared production root cause")
            store.start_execution("repair-task")
            store.report("repair-task", report_ref="reports/repair.md", repair_evidence=self.evidence(root_closed=True))
            store.review("repair-task", "accepted", independent=True)
            store.complete("repair-task")
            final = store.audit("repair-task")
            self.assertTrue(final["ok"])
            self.assertEqual(final["status"], "complete")
            self.assertTrue(final["repairOutcome"]["complete"])

    def test_invalid_repair_contract_leaves_no_task_state(self):
        with tempfile.TemporaryDirectory(prefix="uaw-repair-contract-") as temp:
            project = Path(temp) / "project"
            project.mkdir()
            root = project / ".agent-workflow"
            initialize_project(project, root)
            store = WorkflowStore(project, root)
            invalid = copy.deepcopy(self.policy())
            invalid["recovery"]["sharedValidationChecks"] = []
            contract = make_contract("invalid", "Invalid", "must not persist", repair_policy=None)
            contract["work_type"] = "repair"
            contract["repair_policy"] = invalid
            with self.assertRaisesRegex(WorkflowError, "sharedValidationChecks must not be empty"):
                store.create_contract(contract)
            self.assertFalse(store._contract_path("invalid").exists())
            self.assertEqual(store.events("invalid"), [])
            missing_policy = make_contract("missing-policy", "Missing", "must not persist")
            missing_policy["work_type"] = "repair"
            with self.assertRaisesRegex(WorkflowError, "requires a code-generated repair_policy"):
                store.create_contract(missing_policy)
            self.assertFalse(store._contract_path("missing-policy").exists())


if __name__ == "__main__":
    unittest.main()
