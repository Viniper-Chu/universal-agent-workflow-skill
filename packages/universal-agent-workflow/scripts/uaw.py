#!/usr/bin/env python3
"""CLI for the universal-agent-workflow skill."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from bootstrap import BootstrapError, SKILL_VERSION, installation_plan, make_verified_readiness_receipt, validate_readiness_receipt
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
    redact_json,
    redact_text,
    render_status,
    route_request,
    run_selftest,
    validate_install,
    sensitive_match_count,
)
from workflow_policy import WorkflowPolicyError, load_policy, policy_profile, validate_policy
from source_policy_compiler import SourcePolicyCompilerError, compile_workflow_sources


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
    plan.add_argument("--actor", default="management")

    for name, help_text in (("dispatch", "request execution dispatch"), ("start", "start execution"), ("complete", "complete after acceptance")):
        command = sub.add_parser(name, help=help_text)
        command.add_argument("--project-root", default=".")
        command.add_argument("--output-root", default=".agent-workflow")
        command.add_argument("--task-id", required=True)
        command.add_argument("--actor", default="management" if name in {"dispatch", "complete"} else "execution")
    dispatch = sub.choices["dispatch"]
    dispatch.add_argument("--packet-ref")

    report = sub.add_parser("report", help="record an execution report")
    report.add_argument("--project-root", default=".")
    report.add_argument("--output-root", default=".agent-workflow")
    report.add_argument("--task-id", required=True)
    report.add_argument("--report-ref")
    report.add_argument("--report-text")
    report.add_argument("--actor", default="execution")

    review = sub.add_parser("review", help="record independent review or correction")
    review.add_argument("--project-root", default=".")
    review.add_argument("--output-root", default=".agent-workflow")
    review.add_argument("--task-id", required=True)
    review.add_argument("--decision", required=True, choices=["accepted", "correction", "blocked"])
    review.add_argument("--independent", action="store_true")
    review.add_argument("--checkpoint", action="store_true")
    review.add_argument("--reason")
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
    receipt.add_argument("--actor", default="execution")

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

    policy = sub.add_parser("policy", help="validate and render the code-backed runtime policy for one role")
    policy.add_argument("--skill-dir", default=str(Path(__file__).resolve().parents[1]))
    policy.add_argument("--role", required=True, choices=["management", "execution", "reviewer"])

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
                contract = make_contract(task_id=args.task_id, title=args.title, objective=args.objective, role=args.role, complexity=args.complexity)
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
                    "plan_steps": contract["plan_steps"],
                    "destination_role": contract.get("destination_role"),
                },
                "snapshot": snapshot,
            })
            return 0
        if args.command == "dispatch":
            _json(store.dispatch(args.task_id, actor=args.actor, packet_ref=args.packet_ref))
            return 0
        if args.command == "start":
            _json(store.start_execution(args.task_id, actor=args.actor))
            return 0
        if args.command == "report":
            _json(store.report(args.task_id, report_ref=args.report_ref, report_text=args.report_text, actor=args.actor))
            return 0
        if args.command == "review":
            _json(store.review(args.task_id, args.decision, actor=args.actor, independent=args.independent, checkpoint=args.checkpoint, reason=args.reason))
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
            receipt_value = _read_json_file(args.receipt_file)
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
    except (WorkflowError, BootstrapError, WorkflowPolicyError, SourcePolicyCompilerError, OSError, json.JSONDecodeError) as exc:
        print(json.dumps({"ok": False, "error": redact_text(str(exc))}, ensure_ascii=False), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
