#!/usr/bin/env python3
"""CLI for the universal-agent-workflow skill."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from bootstrap import BootstrapError, SKILL_VERSION, extract_readiness_receipt, installation_plan, make_verified_readiness_receipt, validate_readiness_receipt
from deployment import DeploymentError, build_release_asset, deploy_skill
from retention import verify_git_current
from workflow_engine import (
    WorkflowError,
    WorkflowStore,
    build_handoff_packet,
    initialize_project,
    make_contract,
    next_action,
    prepare_handoff,
    probe_capabilities,
    quick_validate,
    redact_json,
    redact_text,
    render_status,
    route_request,
    run_selftest,
    validate_install,
    sensitive_match_count,
)
from coordination_policy import (
    CoordinationPolicyError,
    build_host_action,
    build_migration_sequence,
    build_supervision_plan,
    coordination_policy_projection,
    derive_execution_settings,
    validate_create_target,
    validate_delegation,
)
from workflow_policy import WorkflowPolicyError, load_policy, policy_profile, validate_policy
from source_policy_compiler import SourcePolicyCompilerError, compile_workflow_sources
from repair_policy import (
    RepairPolicyError,
    build_repair_policy,
    evaluate_repair_evidence,
    repair_policy_projection,
)


def _configure_utf8_stdio() -> None:
    """Keep CLI JSON machine-readable on Windows paths containing Unicode."""
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if callable(reconfigure):
            reconfigure(encoding="utf-8")


def _json(value: object) -> None:
    print(json.dumps(value, ensure_ascii=False, indent=2))


def _store(args: argparse.Namespace) -> WorkflowStore:
    return WorkflowStore(args.project_root, args.output_root)


def _read_json_file(path: str) -> object:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(prog="uaw", description="Universal contract-first agent workflow engine")
    sub = root.add_subparsers(dest="command", required=True)

    init = sub.add_parser("init", help="create the controlled workflow root")
    init.add_argument("--project-root", default=".")
    init.add_argument("--output-root", default=".agent-workflow")

    plan = sub.add_parser("plan", help="create a task contract")
    plan.add_argument("--project-root", default=".")
    plan.add_argument("--output-root", default=".agent-workflow")
    plan.add_argument("--contract-json")
    plan.add_argument("--task-id")
    plan.add_argument("--title")
    plan.add_argument("--objective")
    plan.add_argument("--role", default="management", choices=["management", "execution", "reviewer"])
    plan.add_argument("--complexity", default="simple", choices=["simple", "complex"])
    plan.add_argument("--work-type", choices=["general", "repair"])
    plan.add_argument("--repair-policy-file")
    plan.add_argument("--actor", default="management")

    for name, help_text in (("dispatch", "request execution dispatch"), ("start", "start execution"), ("complete", "complete after acceptance")):
        command = sub.add_parser(name, help=help_text)
        command.add_argument("--project-root", default=".")
        command.add_argument("--output-root", default=".agent-workflow")
        command.add_argument("--task-id", required=True)
        command.add_argument("--actor", default="management" if name in {"dispatch", "complete"} else "execution")
    dispatch = sub.choices["dispatch"]
    dispatch.add_argument("--packet-ref")

    dispatch_supervised = sub.add_parser("dispatch-supervised", help="dispatch and enter wait/observe/correct supervision")
    dispatch_supervised.add_argument("--project-root", default=".")
    dispatch_supervised.add_argument("--output-root", default=".agent-workflow")
    dispatch_supervised.add_argument("--task-id", required=True)
    dispatch_supervised.add_argument("--dispatch-id", required=True)
    dispatch_supervised.add_argument("--packet-ref")
    dispatch_supervised.add_argument("--actor", default="management")

    plan_host = sub.add_parser("plan-host-action", help="append a planned host action to task state")
    plan_host.add_argument("--project-root", default=".")
    plan_host.add_argument("--output-root", default=".agent-workflow")
    plan_host.add_argument("--task-id", required=True)
    plan_host.add_argument("--action-json", required=True)
    plan_host.add_argument("--actor", default="management")

    record_host = sub.add_parser("record-host-action", help="append a host action result to task state")
    record_host.add_argument("--project-root", default=".")
    record_host.add_argument("--output-root", default=".agent-workflow")
    record_host.add_argument("--task-id", required=True)
    record_host.add_argument("--action-id", required=True)
    record_host.add_argument("--status", required=True, choices=["sent", "observed", "failed"])
    record_host.add_argument("--result-json")
    record_host.add_argument("--actor", default="management")

    migration_step = sub.add_parser("migration-step", help="append one code-backed migration step")
    migration_step.add_argument("--project-root", default=".")
    migration_step.add_argument("--output-root", default=".agent-workflow")
    migration_step.add_argument("--task-id", required=True)
    migration_step.add_argument("--step-json", required=True)
    migration_step.add_argument("--actor", default="management")

    supervision_update = sub.add_parser("supervision-update", help="append wait/observe/correct supervision state")
    supervision_update.add_argument("--project-root", default=".")
    supervision_update.add_argument("--output-root", default=".agent-workflow")
    supervision_update.add_argument("--task-id", required=True)
    supervision_update.add_argument("--dispatch-id", required=True)
    supervision_update.add_argument("--wait-json")
    supervision_update.add_argument("--read-json")
    supervision_update.add_argument("--supervision-epoch", type=int)
    supervision_update.add_argument("--correction-id")
    supervision_update.add_argument("--progress-cursor")
    supervision_update.add_argument("--actor", default="management")

    supervision_advance = sub.add_parser("supervision-advance", help="start the next wait epoch on the same execution task")
    supervision_advance.add_argument("--project-root", default=".")
    supervision_advance.add_argument("--output-root", default=".agent-workflow")
    supervision_advance.add_argument("--task-id", required=True)
    supervision_advance.add_argument("--dispatch-id", required=True)
    supervision_advance.add_argument("--actor", default="management")

    report = sub.add_parser("report", help="record an execution report")
    report.add_argument("--project-root", default=".")
    report.add_argument("--output-root", default=".agent-workflow")
    report.add_argument("--task-id", required=True)
    report.add_argument("--report-ref")
    report.add_argument("--report-text")
    report.add_argument("--repair-evidence-file")
    report.add_argument("--evidence-delta-file")
    report.add_argument("--actor", default="execution")

    review = sub.add_parser("review", help="record independent review or correction")
    review.add_argument("--project-root", default=".")
    review.add_argument("--output-root", default=".agent-workflow")
    review.add_argument("--task-id", required=True)
    review.add_argument("--decision", required=True, choices=["accepted", "correction", "blocked"])
    review.add_argument("--independent", action="store_true")
    review.add_argument("--checkpoint", action="store_true")
    review.add_argument("--reason")
    review.add_argument("--reviewer-id")
    review.add_argument("--correction-id")
    review.add_argument("--evidence-delta-file")
    review.add_argument("--actor", default="management")

    transition = sub.add_parser("transition", help="apply one explicit lifecycle event")
    transition.add_argument("--project-root", default=".")
    transition.add_argument("--output-root", default=".agent-workflow")
    transition.add_argument("--task-id", required=True)
    transition.add_argument("--event", required=True)
    transition.add_argument("--payload-json", default="{}")
    transition.add_argument("--actor", default="system")

    for name, help_text in (("status", "show a task status"), ("audit", "audit task events"), ("next-action", "render the next legal action")):
        command = sub.add_parser(name, help=help_text)
        command.add_argument("--project-root", default=".")
        command.add_argument("--output-root", default=".agent-workflow")
        command.add_argument("--task-id", required=True)
        command.add_argument("--audience", default="management", choices=["management", "execution", "user"])
        command.add_argument("--format", default="json", choices=["json", "text"])

    probe = sub.add_parser("probe", help="classify a host tool inventory")
    probe.add_argument("--inventory-file")
    probe.add_argument("--tools", nargs="*")

    route = sub.add_parser("route", help="route a user request by role")
    route.add_argument("--role", required=True, choices=["management", "execution", "reviewer"])
    route.add_argument("--message", required=True)
    route.add_argument("--relay-file")

    handoff = sub.add_parser("handoff", help="ask, plan native handoff, or write manual relay")
    handoff.add_argument("--project-root", default=".")
    handoff.add_argument("--output-root", default=".agent-workflow")
    handoff.add_argument("--task-id", required=True)
    handoff.add_argument("--message", required=True)
    handoff.add_argument("--confirmed", action="store_true")
    handoff.add_argument("--capabilities-file")

    destination_bootstrap = sub.add_parser("destination-bootstrap", help="code-validate a destination and emit its readiness receipt")
    destination_bootstrap.add_argument("--skill-dir", default=str(Path(__file__).resolve().parents[1]))
    destination_bootstrap.add_argument("--role", required=True, choices=["management", "execution", "reviewer"])
    destination_bootstrap.add_argument("--destination-id", required=True)
    destination_bootstrap.add_argument("--stable-session-id")
    destination_bootstrap.add_argument("--peer-identity", required=True)
    destination_bootstrap.add_argument("--inventory-file")
    destination_bootstrap.add_argument("--tools", nargs="*")

    bootstrap = sub.add_parser("bootstrap", help="record a destination Skill bootstrap request")
    bootstrap.add_argument("--project-root", default=".")
    bootstrap.add_argument("--output-root", default=".agent-workflow")
    bootstrap.add_argument("--task-id", required=True)
    bootstrap.add_argument("--role", required=True, choices=["management", "execution", "reviewer"])
    bootstrap.add_argument("--destination-id", required=True)
    bootstrap.add_argument("--peer-identity", required=True)
    bootstrap.add_argument("--install-source", required=True)
    bootstrap.add_argument("--capability-mode", required=True, choices=["native", "manual"])

    receipt = sub.add_parser("receipt", help="validate and record a destination readiness receipt")
    receipt.add_argument("--project-root", default=".")
    receipt.add_argument("--output-root", default=".agent-workflow")
    receipt.add_argument("--task-id", required=True)
    receipt.add_argument("--receipt-file", required=True)
    receipt.add_argument("--expected-role")
    receipt.add_argument("--expected-destination-id")
    receipt.add_argument("--actor")

    handoff_export = sub.add_parser("handoff-export", help="export a self-contained code-state handoff bundle")
    handoff_export.add_argument("--project-root", default=".")
    handoff_export.add_argument("--output-root", default=".agent-workflow")
    handoff_export.add_argument("--task-id", required=True)
    handoff_export.add_argument("--continuity-file", required=True)
    handoff_export.add_argument("--source-session-id", required=True)
    handoff_export.add_argument("--destination-session-id", required=True)
    handoff_export.add_argument("--source-role", required=True, choices=["management", "execution", "reviewer"])
    handoff_export.add_argument("--destination-role", required=True, choices=["management", "execution", "reviewer"])
    handoff_export.add_argument("--management-peer", required=True)
    handoff_export.add_argument("--execution-peer", required=True)
    handoff_export.add_argument("--capability-mode", required=True, choices=["native", "manual"])
    handoff_export.add_argument("--actor", default="management")

    handoff_receive = sub.add_parser("handoff-receive", help="validate and load a code-state handoff bundle")
    handoff_receive.add_argument("--project-root", default=".")
    handoff_receive.add_argument("--output-root", default=".agent-workflow")
    handoff_receive.add_argument("--task-id", required=True)
    handoff_receive.add_argument("--bundle-file", required=True)
    handoff_receive.add_argument("--expected-destination-id", required=True)
    handoff_receive.add_argument("--expected-role", required=True, choices=["management", "execution", "reviewer"])
    handoff_receive.add_argument("--actor", default="execution")

    accept_handoff = sub.add_parser("handoff-accept", help="record destination acceptance after readiness")
    accept_handoff.add_argument("--project-root", default=".")
    accept_handoff.add_argument("--output-root", default=".agent-workflow")
    accept_handoff.add_argument("--task-id", required=True)
    accept_handoff.add_argument("--role", required=True, choices=["management", "execution", "reviewer"])
    accept_handoff.add_argument("--peer-identity", required=True)
    accept_handoff.add_argument("--actor", default="execution")

    handoff_complete = sub.add_parser("handoff-complete", help="record management confirmation after target acceptance")
    handoff_complete.add_argument("--project-root", default=".")
    handoff_complete.add_argument("--output-root", default=".agent-workflow")
    handoff_complete.add_argument("--task-id", required=True)
    handoff_complete.add_argument("--actor", default="management")

    authorize = sub.add_parser("migration-authorize", help="record explicit user-confirmed source-session removal authorization")
    authorize.add_argument("--project-root", default=".")
    authorize.add_argument("--output-root", default=".agent-workflow")
    authorize.add_argument("--task-id", required=True)
    authorize.add_argument("--source-session-id", required=True)
    authorize.add_argument("--target-session-id", required=True)
    authorize.add_argument("--current-session-id", required=True)
    authorize.add_argument("--actor", default="management")

    remove_source = sub.add_parser("remove-source-session", help="prepare one exact source-session delete or archive")
    remove_source.add_argument("--project-root", default=".")
    remove_source.add_argument("--output-root", default=".agent-workflow")
    remove_source.add_argument("--task-id", required=True)
    remove_source.add_argument("--source-session-id", required=True)
    remove_source.add_argument("--target-session-id", required=True)
    remove_source.add_argument("--current-session-id", required=True)
    remove_source.add_argument("--inventory-file", required=True)
    remove_source.add_argument("--policy-enabled", action="store_true")
    remove_source.add_argument("--actor", default="management")

    source_removed = sub.add_parser("source-session-result", help="record successful or failed source-session removal")
    source_removed.add_argument("--project-root", default=".")
    source_removed.add_argument("--output-root", default=".agent-workflow")
    source_removed.add_argument("--task-id", required=True)
    source_removed.add_argument("--source-session-id", required=True)
    source_removed.add_argument("--success", action="store_true")
    source_removed.add_argument("--actor", default="host")

    install = sub.add_parser("install-plan", help="show a non-destructive exact-version install plan")
    install.add_argument("--target", required=True)
    install.add_argument("--source", required=True)
    install.add_argument("--version", default=SKILL_VERSION)

    deploy = sub.add_parser("install", aliases=["deploy"], help="validate and recoverably install/update/repair one exact Skill target")
    deploy.add_argument("--source", required=True)
    deploy.add_argument("--target", required=True)
    deploy.add_argument("--backup-root")
    deploy.add_argument("--version", default=SKILL_VERSION)

    release_asset = sub.add_parser("build-release-asset", help="build a complete directly installable Skill release zip")
    release_asset.add_argument("--source", default=str(Path(__file__).resolve().parents[1]))
    release_asset.add_argument("--output", required=True)
    release_asset.add_argument("--version", default=SKILL_VERSION)

    policy = sub.add_parser("policy", help="validate and render the code-backed runtime policy for one role")
    policy.add_argument("--skill-dir", default=str(Path(__file__).resolve().parents[1]))
    policy.add_argument("--role", required=True, choices=["management", "execution", "reviewer"])

    coordination_policy = sub.add_parser("coordination-policy", help="render the code-backed current coordination policy")

    repair_policy = sub.add_parser("repair-policy", help="build a code-backed repair completion policy")
    repair_policy.add_argument("--data-recovery-required", action="store_true")
    repair_policy.add_argument("--real-data-write", action="store_true")
    repair_policy.add_argument("--identity-key", action="append", default=[])
    repair_policy.add_argument("--validation-check", action="append", default=[])
    repair_policy.add_argument("--conservation-scope", action="append", default=[])
    repair_policy.add_argument("--preserve-external-call-ledger", action="store_true")

    repair_evidence = sub.add_parser("repair-evidence", help="evaluate repair evidence without writing task state")
    repair_evidence.add_argument("--policy-file", required=True)
    repair_evidence.add_argument("--evidence-file", required=True)

    host_action = sub.add_parser("host-action", help="build a planned host action contract")
    host_action.add_argument("--action-name", required=True)
    host_action.add_argument("--tool", required=True)
    host_action.add_argument("--args-json", default="{}")
    host_action.add_argument("--actor-role", required=True)
    host_action.add_argument("--target-role")
    host_action.add_argument("--actor-session-id")
    host_action.add_argument("--dispatch-id")
    host_action.add_argument("--chain-id")
    host_action.add_argument("--supervision-epoch", type=int)
    host_action.add_argument("--message-id")
    host_action.add_argument("--correction-id")
    host_action.add_argument("--phase", required=True)
    host_action.add_argument("--action-id")

    host_action_result = sub.add_parser("host-action-result", help="record a host action result in a pure projection")
    host_action_result.add_argument("--action-json", required=True)
    host_action_result.add_argument("--status", required=True, choices=["planned", "sent", "observed", "failed"])
    host_action_result.add_argument("--result-json")

    migration_plan = sub.add_parser("migration-plan", help="render the code-backed management migration order")
    migration_plan.add_argument("--old-management-id", required=True)
    migration_plan.add_argument("--new-management-id", required=True)
    migration_plan.add_argument("--target-json", required=True, help="exact codex_app__create_thread target object")
    migration_plan.add_argument("--management-json", help="management settings snapshot object")
    migration_plan.add_argument("--user-json", help="partial user settings override object")
    migration_plan.add_argument("--inheritance-evidence-json", help="optional host inheritance evidence object")

    settings_inherit = sub.add_parser("settings-inherit", help="derive execution settings without overriding user choice")
    settings_inherit.add_argument("--management-json", required=True)
    settings_inherit.add_argument("--user-json")
    settings_inherit.add_argument("--inheritance-evidence-json")

    supervision = sub.add_parser("supervision", help="render wait/observe/correct supervision state")
    supervision.add_argument("--dispatch-id", required=True)
    supervision.add_argument("--wait-json")
    supervision.add_argument("--read-json")
    supervision.add_argument("--supervision-epoch", type=int, default=1)
    supervision.add_argument("--correction-id")
    supervision.add_argument("--progress-cursor")
    supervision.add_argument("--previous-progress-cursor")
    supervision.add_argument("--stagnant-epochs", type=int, default=0)

    delegation = sub.add_parser("delegation-validate", help="validate structured parent-role delegation")
    delegation.add_argument("--parent-role", required=True)
    delegation.add_argument("--work-category", required=True)
    delegation.add_argument("--child-role")

    delegation_request = sub.add_parser("delegation-request", help="record an allowed structured delegation")
    delegation_request.add_argument("--project-root", default=".")
    delegation_request.add_argument("--output-root", default=".agent-workflow")
    delegation_request.add_argument("--task-id", required=True)
    delegation_request.add_argument("--parent-role", required=True)
    delegation_request.add_argument("--work-category", required=True)
    delegation_request.add_argument("--child-role")
    delegation_request.add_argument("--spec-file")

    delegation_complete = sub.add_parser("delegation-complete", help="release one delegation ownership scope after aggregation")
    delegation_complete.add_argument("--project-root", default=".")
    delegation_complete.add_argument("--output-root", default=".agent-workflow")
    delegation_complete.add_argument("--task-id", required=True)
    delegation_complete.add_argument("--delegation-id", required=True)
    delegation_complete.add_argument("--output", required=True)
    delegation_complete.add_argument("--actor", required=True, choices=["management", "execution", "reviewer"])

    source_migrate = sub.add_parser("source-migrate", help="preserve three legacy workflow sources as structured non-runtime evidence")
    source_migrate.add_argument("--project-root", default=".")
    source_migrate.add_argument("--output-root", default=".agent-workflow")
    source_migrate.add_argument("--skill-dir", default=str(Path(__file__).resolve().parents[1]))
    source_migrate.add_argument("--general-source", required=True)
    source_migrate.add_argument("--management-source", required=True)
    source_migrate.add_argument("--execution-source", required=True)

    register = sub.add_parser("retention-register", help="register a Skill-owned artifact")
    register.add_argument("--project-root", default=".")
    register.add_argument("--output-root", default=".agent-workflow")
    register.add_argument("--task-id", required=True)
    register.add_argument("--path", required=True)
    register.add_argument("--kind", required=True)
    register.add_argument("--generation", required=True, type=int)
    register.add_argument("--canonical", action="store_true")
    register.add_argument("--previous", action="store_true")
    register.add_argument("--ephemeral", action="store_true")
    register.add_argument("--retained", action="store_true")

    cleanup = sub.add_parser("cleanup", help="dry-run or apply safe Skill-owned artifact retention")
    cleanup.add_argument("--project-root", default=".")
    cleanup.add_argument("--output-root", default=".agent-workflow")
    cleanup.add_argument("--task-id", required=True)
    cleanup.add_argument("--git-confirmed", action="store_true", help="assert that the current canonical version is committed")
    cleanup.add_argument("--git-root", help="repository root to verify when --git-confirmed is omitted")
    cleanup.add_argument("--apply", action="store_true")

    for name, help_text in (("retention-dry-run", "record the retention dry-run step"), ("retention-apply", "apply the recorded retention plan")):
        command = sub.add_parser(name, help=help_text)
        command.add_argument("--project-root", default=".")
        command.add_argument("--output-root", default=".agent-workflow")
        command.add_argument("--task-id", required=True)
        command.add_argument("--git-confirmed", action="store_true")
        command.add_argument("--git-root")

    rotate = sub.add_parser("retention-rotate", help="mark a registered generation current")
    rotate.add_argument("--project-root", default=".")
    rotate.add_argument("--output-root", default=".agent-workflow")
    rotate.add_argument("--task-id", required=True)
    rotate.add_argument("--generation", required=True, type=int)

    redact = sub.add_parser("redact", help="redact sensitive values without printing matches")
    redact.add_argument("--text", required=True)

    validate = sub.add_parser("validate-install", help="validate a skill package")
    validate.add_argument("--skill-dir", default=str(Path(__file__).resolve().parents[1]))

    sub.add_parser("quick_validate", aliases=["quick-validate"], help="run the code-backed quick validation checks")

    sub.add_parser("selftest", help="run built-in high-value lifecycle and routing tests")
    return root


def main(argv: list[str] | None = None) -> int:
    _configure_utf8_stdio()
    args = parser().parse_args(argv)
    try:
        if args.command == "init":
            _json(initialize_project(args.project_root, args.output_root))
            return 0
        if args.command == "selftest":
            result = run_selftest()
            _json(result)
            return 0 if result["ok"] else 1
        if args.command == "validate-install":
            result = validate_install(args.skill_dir)
            _json(result)
            return 0 if result["ok"] else 1
        if args.command in {"quick_validate", "quick-validate"}:
            result = quick_validate()
            _json(result)
            return 0 if result["ok"] else 1
        if args.command == "probe":
            raw = _read_json_file(args.inventory_file) if args.inventory_file else (args.tools or [])
            _json(probe_capabilities(raw))
            return 0
        if args.command == "route":
            relay = _read_json_file(args.relay_file) if args.relay_file else None
            _json(route_request(args.role, args.message, relay))
            return 0
        if args.command == "redact":
            _json({"redacted": redact_text(args.text), "sensitive_match_count": sensitive_match_count(args.text)})
            return 0
        if args.command == "install-plan":
            _json(installation_plan(args.target, args.source, args.version))
            return 0
        if args.command in {"install", "deploy"}:
            result = deploy_skill(args.source, args.target, expected_version=args.version, backup_root=args.backup_root)
            _json(result)
            return 0 if result.get("ok") else 1
        if args.command == "build-release-asset":
            _json(build_release_asset(args.source, args.output, expected_version=args.version))
            return 0
        if args.command == "policy":
            policy_value = load_policy(args.skill_dir, SKILL_VERSION)
            validation = validate_policy(policy_value, SKILL_VERSION)
            _json({
                "ok": True,
                "command": "policy",
                "validation": validation,
                "runtimePolicy": policy_profile(args.role, args.skill_dir, SKILL_VERSION),
                "externalReadsRequired": False,
            })
            return 0
        if args.command == "coordination-policy":
            _json({"ok": True, "command": "coordination-policy", "skillVersion": SKILL_VERSION, "coordinationPolicy": coordination_policy_projection(), "externalReadsRequired": False})
            return 0
        if args.command == "repair-policy":
            value = build_repair_policy(
                data_recovery_required=args.data_recovery_required,
                real_data_write=args.real_data_write,
                identity_keys=args.identity_key,
                validation_checks=args.validation_check,
                conservation_scopes=args.conservation_scope,
                preserve_external_call_ledger=args.preserve_external_call_ledger,
            )
            _json({"ok": True, "command": "repair-policy", "skillVersion": SKILL_VERSION, "repairPolicy": value, "projection": repair_policy_projection(), "externalReadsRequired": False})
            return 0
        if args.command == "repair-evidence":
            result = evaluate_repair_evidence(_read_json_file(args.policy_file), _read_json_file(args.evidence_file))
            _json({"ok": True, "command": "repair-evidence", "skillVersion": SKILL_VERSION, "result": result, "externalReadsRequired": False})
            return 0 if result["complete"] else 2
        if args.command == "host-action":
            _json({
                "ok": True,
                "command": "host-action",
                "action": build_host_action(
                    args.action_name,
                    args.tool,
                    json.loads(args.args_json),
                    actor_role=args.actor_role,
                    target_role=args.target_role,
                    phase=args.phase,
                    action_id=args.action_id,
                    actor_session_id=args.actor_session_id,
                    dispatch_id=args.dispatch_id,
                    chain_id=args.chain_id,
                    supervision_epoch=args.supervision_epoch,
                    message_id=args.message_id,
                    correction_id=args.correction_id,
                ),
                "externalReadsRequired": False,
            })
            return 0
        if args.command == "host-action-result":
            from coordination_policy import record_host_action

            result = json.loads(args.result_json) if args.result_json else None
            _json({"ok": True, "command": "host-action-result", "action": record_host_action(json.loads(args.action_json), args.status, result), "externalReadsRequired": False})
            return 0
        if args.command == "migration-plan":
            target = json.loads(args.target_json)
            validate_create_target(target)
            management_settings = json.loads(args.management_json) if args.management_json else {}
            user_settings = json.loads(args.user_json) if args.user_json else None
            inheritance_evidence = json.loads(args.inheritance_evidence_json) if args.inheritance_evidence_json else None
            _json({
                "ok": True,
                "command": "migration-plan",
                "steps": build_migration_sequence(
                    args.old_management_id,
                    args.new_management_id,
                    target,
                    management_settings=management_settings,
                    user_settings=user_settings,
                    inheritance_evidence=inheritance_evidence,
                ),
                "externalReadsRequired": False,
            })
            return 0
        if args.command == "settings-inherit":
            management_settings = json.loads(args.management_json)
            user_settings = json.loads(args.user_json) if args.user_json else None
            inheritance_evidence = json.loads(args.inheritance_evidence_json) if args.inheritance_evidence_json else None
            _json({"ok": True, "command": "settings-inherit", "settings": derive_execution_settings(management_settings, user_settings, inheritance_evidence=inheritance_evidence), "externalReadsRequired": False})
            return 0
        if args.command == "supervision":
            wait_result = json.loads(args.wait_json) if args.wait_json else None
            read_result = json.loads(args.read_json) if args.read_json else None
            _json({"ok": True, "command": "supervision", "plan": build_supervision_plan(
                args.dispatch_id,
                wait_result,
                read_result,
                supervision_epoch=args.supervision_epoch,
                correction_id=args.correction_id,
                progress_cursor=args.progress_cursor,
                previous_progress_cursor=args.previous_progress_cursor,
                stagnant_epochs=args.stagnant_epochs,
            ), "externalReadsRequired": False})
            return 0
        if args.command == "delegation-validate":
            _json({"ok": True, "command": "delegation-validate", "decision": validate_delegation(args.parent_role, args.work_category, args.child_role), "externalReadsRequired": False})
            return 0
        if args.command == "source-migrate":
            running_skill_dir = Path(__file__).resolve().parents[1]
            requested_skill_dir = Path(args.skill_dir).expanduser().resolve()
            if requested_skill_dir != running_skill_dir:
                raise WorkflowPolicyError("source-migrate must use the same Skill package that runs the command")
            initialized = initialize_project(args.project_root, args.output_root)
            policy_value = load_policy(args.skill_dir, SKILL_VERSION)
            validation = validate_policy(policy_value, SKILL_VERSION)
            capsule = compile_workflow_sources(
                general_source=args.general_source,
                management_source=args.management_source,
                execution_source=args.execution_source,
                policy_validation=validation,
            )
            capsule = redact_json(capsule)
            capsule["redacted"] = True
            output_path = Path(initialized["outputRoot"]) / "evidence" / "workflow-source-migration.json"
            output_path.write_text(json.dumps(capsule, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            _json({
                "ok": True,
                "command": "source-migrate",
                "capsulePath": str(output_path),
                "sourceCount": capsule["sourceCount"],
                "structuredRecords": capsule["structuredRecords"],
                "losslessNonBlankCoverage": capsule["losslessNonBlankCoverage"],
                "runtimeUse": False,
                "externalReadsRequired": False,
            })
            return 0
        if args.command == "destination-bootstrap":
            running_skill_dir = Path(__file__).resolve().parents[1]
            requested_skill_dir = Path(args.skill_dir).expanduser().resolve()
            if requested_skill_dir != running_skill_dir:
                raise BootstrapError("destination-bootstrap must validate the same Skill package that runs the command")
            inventory = _read_json_file(args.inventory_file) if args.inventory_file else (args.tools or [])
            install_validation = validate_install(args.skill_dir)
            selftest = run_selftest()
            capability_probe = probe_capabilities(inventory)
            policy_value = load_policy(args.skill_dir, SKILL_VERSION)
            policy_validation = validate_policy(policy_value, SKILL_VERSION)
            runtime_policy = policy_profile(args.role, args.skill_dir, SKILL_VERSION)
            receipt_value = make_verified_readiness_receipt(
                role=args.role,
                destination_id=args.destination_id,
                stable_session_id=args.stable_session_id,
                install_path=str(Path(args.skill_dir).expanduser().resolve()),
                peer_identity=args.peer_identity,
                install_validation=install_validation,
                selftest=selftest,
                capability_probe=capability_probe,
                policy_validation=policy_validation,
                runtime_policy=runtime_policy,
            )
            _json({
                "ok": True,
                "command": "destination-bootstrap",
                "receipt": receipt_value,
                "runtimePolicy": runtime_policy,
                "externalReadsRequired": False,
            })
            return 0

        store = _store(args)
        if args.command == "plan":
            if args.contract_json:
                contract = _read_json_file(args.contract_json)
            else:
                missing = [name for name in ("task_id", "title", "objective") if not getattr(args, name)]
                if missing:
                    raise WorkflowError("plan requires --contract-json or " + ", ".join(f"--{name}" for name in missing))
                repair_policy_value = _read_json_file(args.repair_policy_file) if args.repair_policy_file else None
                contract = make_contract(
                    task_id=args.task_id,
                    title=args.title,
                    objective=args.objective,
                    role=args.role,
                    complexity=args.complexity,
                    repair_policy=repair_policy_value,
                    work_type=args.work_type,
                )
            if not contract.get("plan_steps"):
                complexity = contract.get("complexity", "simple")
                contract["plan_steps"] = ["plan", "dispatch", "execute", "report", "review", "accept"] if complexity == "complex" else ["plan", "execute", "review"]
            store.create_contract(contract, actor=args.actor)
            snapshot = store.plan(contract["task_id"], contract["plan_steps"], actor=args.actor)
            _json({
                "ok": True,
                "command": "plan",
                "contract": {
                    "task_id": contract["task_id"],
                    "title": contract["title"],
                    "objective": contract["objective"],
                    "role": contract["role"],
                    "complexity": contract["complexity"],
                    "work_type": contract.get("work_type"),
                    "plan_steps": contract["plan_steps"],
                    "destination_role": contract.get("destination_role"),
                    "repair_policy": contract.get("repair_policy"),
                },
                "snapshot": snapshot,
            })
            return 0
        if args.command == "dispatch":
            _json(store.dispatch(args.task_id, actor=args.actor, packet_ref=args.packet_ref))
            return 0
        if args.command == "dispatch-supervised":
            _json(store.dispatch_with_supervision(args.task_id, args.dispatch_id, actor=args.actor, packet_ref=args.packet_ref))
            return 0
        if args.command == "plan-host-action":
            _json(store.plan_host_action(args.task_id, json.loads(args.action_json), actor=args.actor))
            return 0
        if args.command == "record-host-action":
            result = json.loads(args.result_json) if args.result_json else None
            _json(store.record_host_action_result(args.task_id, args.action_id, args.status, result=result, actor=args.actor))
            return 0
        if args.command == "migration-step":
            _json(store.record_migration_step(args.task_id, json.loads(args.step_json), actor=args.actor))
            return 0
        if args.command == "supervision-update":
            wait_result = json.loads(args.wait_json) if args.wait_json else None
            read_result = json.loads(args.read_json) if args.read_json else None
            _json(store.update_supervision(
                args.task_id,
                args.dispatch_id,
                wait_result,
                read_result,
                supervision_epoch=args.supervision_epoch,
                correction_id=args.correction_id,
                progress_cursor=args.progress_cursor,
                actor=args.actor,
            ))
            return 0
        if args.command == "supervision-advance":
            _json(store.advance_supervision(args.task_id, args.dispatch_id, actor=args.actor))
            return 0
        if args.command == "delegation-request":
            spec = _read_json_file(args.spec_file) if args.spec_file else None
            _json(store.request_delegation(args.task_id, args.parent_role, args.work_category, args.child_role, spec))
            return 0
        if args.command == "delegation-complete":
            _json(store.complete_delegation(args.task_id, args.delegation_id, args.output, args.actor))
            return 0
        if args.command == "start":
            _json(store.start_execution(args.task_id, actor=args.actor))
            return 0
        if args.command == "report":
            repair_evidence_value = _read_json_file(args.repair_evidence_file) if args.repair_evidence_file else None
            evidence_delta = _read_json_file(args.evidence_delta_file) if args.evidence_delta_file else None
            _json(store.report(args.task_id, report_ref=args.report_ref, report_text=args.report_text, repair_evidence=repair_evidence_value, evidence_delta=evidence_delta, actor=args.actor))
            return 0
        if args.command == "review":
            evidence_delta = _read_json_file(args.evidence_delta_file) if args.evidence_delta_file else None
            _json(store.review(
                args.task_id,
                args.decision,
                actor=args.actor,
                independent=args.independent,
                checkpoint=args.checkpoint,
                reason=args.reason,
                reviewer_id=args.reviewer_id,
                correction_id=args.correction_id,
                evidence_delta=evidence_delta,
            ))
            return 0
        if args.command == "complete":
            _json(store.complete(args.task_id, actor=args.actor))
            return 0
        if args.command == "transition":
            _json(store.transition(args.task_id, args.event, json.loads(args.payload_json), actor=args.actor))
            return 0
        if args.command == "status":
            snapshot = store.snapshot(args.task_id)
            print(json.dumps(snapshot, ensure_ascii=False, indent=2) if args.format == "json" else render_status(snapshot, args.audience))
            return 0
        if args.command == "audit":
            result = store.audit(args.task_id)
            print(json.dumps(result, ensure_ascii=False, indent=2) if args.format == "json" else render_status({**result, "task_id": args.task_id}, args.audience))
            return 0 if result["ok"] else 1
        if args.command == "next-action":
            snapshot = store.snapshot(args.task_id)
            result = next_action(snapshot)
            print(json.dumps(result, ensure_ascii=False, indent=2) if args.format == "json" else render_status(snapshot, args.audience))
            return 0
        if args.command == "handoff":
            capabilities = _read_json_file(args.capabilities_file) if args.capabilities_file else None
            result = prepare_handoff(store, args.task_id, args.message, confirmed=args.confirmed, capabilities=capabilities)
            _json(result)
            return 0
        if args.command == "bootstrap":
            store = _store(args)
            _json(store.bootstrap(args.task_id, args.role, args.destination_id, args.peer_identity, args.install_source, args.capability_mode))
            return 0
        if args.command == "receipt":
            store = _store(args)
            receipt_value = extract_readiness_receipt(_read_json_file(args.receipt_file))
            validation = validate_readiness_receipt(receipt_value, args.expected_role, args.expected_destination_id)
            snapshot = store.destination_ready(args.task_id, receipt_value, args.expected_role, args.expected_destination_id, args.actor)
            _json({"ok": True, "command": "receipt", "validation": validation, "snapshot": snapshot})
            return 0
        if args.command == "handoff-export":
            continuity = _read_json_file(args.continuity_file)
            _json(store.export_code_handoff(
                args.task_id,
                continuity,
                args.source_session_id,
                args.destination_session_id,
                args.source_role,
                args.destination_role,
                args.management_peer,
                args.execution_peer,
                args.capability_mode,
                args.actor,
            ))
            return 0
        if args.command == "handoff-receive":
            _json(store.receive_code_handoff(
                args.task_id,
                args.bundle_file,
                args.expected_destination_id,
                args.expected_role,
                args.actor,
            ))
            return 0
        if args.command == "handoff-accept":
            store = _store(args)
            _json(store.handoff_accept(args.task_id, args.role, args.peer_identity, args.actor))
            return 0
        if args.command == "handoff-complete":
            store = _store(args)
            _json(store.handoff_complete(args.task_id, args.actor))
            return 0
        if args.command == "migration-authorize":
            store = _store(args)
            _json(store.authorize_migration(args.task_id, args.source_session_id, args.target_session_id, args.current_session_id, args.actor))
            return 0
        if args.command == "remove-source-session":
            store = _store(args)
            inventory = _read_json_file(args.inventory_file)
            _json(store.request_source_removal(args.task_id, args.source_session_id, args.target_session_id, args.current_session_id, inventory, args.policy_enabled, args.actor))
            return 0
        if args.command == "source-session-result":
            store = _store(args)
            _json(store.record_source_removed(args.task_id, args.source_session_id, args.success, args.actor))
            return 0
        if args.command == "retention-register":
            store = _store(args)
            _json(store.register_artifact(args.task_id, args.path, args.kind, args.generation, canonical=args.canonical, previous=args.previous, ephemeral=args.ephemeral, retained=args.retained))
            return 0
        if args.command == "cleanup":
            store = _store(args)
            git_confirmed = args.git_confirmed or verify_git_current(args.git_root or args.project_root)
            _json(store.cleanup(args.task_id, git_confirmed=git_confirmed, apply=args.apply))
            return 0
        if args.command in {"retention-dry-run", "retention-apply"}:
            store = _store(args)
            git_confirmed = args.git_confirmed or verify_git_current(args.git_root or args.project_root)
            result = store.retention_dry_run(args.task_id, git_confirmed=git_confirmed) if args.command == "retention-dry-run" else store.retention_apply(args.task_id, git_confirmed=git_confirmed)
            _json(result)
            return 0
        if args.command == "retention-rotate":
            store = _store(args)
            _json(store.rotate_retention(args.task_id, args.generation))
            return 0
        raise WorkflowError(f"unsupported command: {args.command}")
    except (WorkflowError, BootstrapError, DeploymentError, WorkflowPolicyError, CoordinationPolicyError, RepairPolicyError, SourcePolicyCompilerError, OSError, json.JSONDecodeError) as exc:
        print(json.dumps({"ok": False, "error": redact_text(str(exc))}, ensure_ascii=False), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
