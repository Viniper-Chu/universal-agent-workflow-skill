---
name: universal-agent-workflow
description: Use this skill when a project needs a contract-first management/execution workflow with explicit planning, role routing, capability-aware native or manual handoff, readiness-verified destination bootstrap, epoch-bound live supervision, ownership-safe delegation, fresh independent review, secret redaction, and safe retention of Skill-owned artifacts.
---

# Universal Agent Workflow

Use the included standard-library engine to turn a business request into a
small task contract, an event-backed lifecycle, and a reviewable handoff. Keep
the event log as the state authority; treat JSON snapshots and Markdown as
human-readable projections.

## Quick start

Run these commands from the Skill package or call the Python modules directly:

```text
python scripts/uaw.py init --project-root <project> --output-root .agent-workflow
python scripts/uaw.py plan --project-root <project> --task-id <id> --title "..." --objective "..."
python scripts/uaw.py probe --inventory-file <tool-inventory.json>
python scripts/uaw.py selftest
python scripts/uaw.py validate-install --skill-dir <installed-skill>
```

The controlled root contains `contracts`, `state`, `reports`, `handoffs`,
`evidence`, and `tmp`. Do not write intermediate files beside the project,
on the desktop, or in a user home directory.

## Decision path

1. Identify the role. Management owns the contract and acceptance; execution
   performs the authorized work; a reviewer independently checks the result.
2. Make a simple plan for a small task and a full contract for a complex task.
   Record objective, non-goals, allowed actions, forbidden actions, risks,
   outputs, and acceptance criteria.
   For a repair task, attach a code-generated `repair_policy`. It keeps product
   root-cause closure separate from recovery of already affected state. A
   repaired record or artifact alone never satisfies final acceptance.
3. Probe the host inventory. Native mode requires create, send, wait, and read
   capabilities. Missing any required capability selects manual mode and lists
   the exact missing names. The Skill never pretends to have a host tool.
4. For a destination change, ask for confirmation. A normal continuation such
   as “continue the next section” is not migration intent.
5. In native mode use this strict order: create or select destination, run the
   exact-version `destination-bootstrap` command there, wait for its
   code-generated readiness receipt, validate it, export a self-contained JSON
   handoff bundle, then run `handoff-receive` in the destination. Business
   dispatch is illegal before both receipts pass.
6. In manual mode give the destination the generated Markdown transport
   guide. It only tells the user which code commands to run; it is not workflow
   authority. After bootstrap, generate the second relay around the JSON
   bundle. The destination must return both code-generated receipts before the
   business packet is accepted.
7. After explicit user-confirmed migration authorization, if migration policy
   allows source-session removal, require target receipt, target acceptance,
   management handoff confirmation, and exact source/target/current session
   IDs. The source must differ from both the target and the current receiving
   session; the target may itself be the current receiving session. Prefer a
   real thread-delete capability;
   if only thread-archive exists, archive the source and report
   `SOURCE_SESSION_REMOVED` with `removalMode=archive`. If neither exists,
   return `MANUAL_SESSION_REMOVAL_REQUIRED` with a precise manual instruction.
   A failed removal never writes a removed terminal state.
8. Dispatch through the canonical host-action sequence: management plans a
   `prompt` send followed by `wait_threads`. Execution starts only after one
   acknowledged delivery binds the dispatch, message, and destination
   identities. Bind every later wait, read, report, and correction to a
   monotonic supervision epoch. Review/correction requires current-epoch wait
   observation, or an observed read-only `read_thread` fallback after a failed
   wait. A correction continues the same dispatch and execution task with a
   new correction identity and evidence delta; it does not repeat execution
   start or create a replacement task.
9. Bind independent acceptance to the current report revision, event cursor,
   reviewing state, and a reviewer identity distinct from execution. Treat
   checkpoints and stale reports as non-final. If progress cursors remain
   unchanged across observed epochs, request a blocker diagnosis and narrowed
   next action from the same execution task; a timeout alone is not evidence
   of no progress.
10. When delegation has real parallel value, record parent and child agent
    identities, role, ownership scopes, access mode, output, dependencies, and
    aggregator. Management delegates management artifacts only; execution
    delegates implementation branches only; reviewer delegates read-only
    review. Concurrent execution writers must not overlap ownership scopes.
    Host settings evidence is read-only and never authorizes automatic changes
    to a user's model or preference choices.
11. After management-confirmed handoff or independently accepted completion,
   let `next-action` drive retention dry-run, apply, and stop. Keep current,
   previous, and retained evidence; remove only registered older or ephemeral
   Skill artifacts. There is no background cleanup.
12. Before retiring legacy workflow source documents, run `source-migrate`.
    It preserves every non-blank line as redacted structured local evidence,
    validates the packaged runtime policy, and marks the capsule non-runtime.
    Delete or archive the originals only after release and install acceptance.

## Contract and lifecycle

The engine rejects out-of-order events. The normal path is:

```text
intake -> planning -> bootstrap_pending -> destination_ready -> dispatched
-> executing -> reviewing -> accepted -> complete
```

Correction remains in the same execution task and opens a new supervision
epoch. A blocked task stays blocked until an explicit unblock. A valid receipt
includes the Skill name and fixed `0.2.0`
version, destination role, install/resolve path or provider, passed selftest
and quick validation, capability mode, stable destination identity when the
host provides one, peer identity, and `ready=true`. “The prompt was received”
is not a receipt.

The engine also validates management manual-relay packets, including exact
Skill identity and both authorization and redaction flags. Direct ordinary
business instructions sent to an execution role return
`REDIRECT_TO_MANAGEMENT`; malformed or unauthorized relay packets are rejected.

Source-session removal is separate from artifact retention. It is only a
post-handoff operation, uses a stable source ID that differs from the target
and current receiving session, and never treats archive as physical delete.

## Handoff and deployment

`bootstrap.py` provides a non-destructive installation plan, code-backed
destination bootstrap, readiness receipt builder, and strict receipt
validator. `workflow_policy.py` loads the packaged runtime rules and rejects a
missing rule or external-Markdown dependency. An existing different Skill
version returns `update_required`; it is never silently overwritten.

`source_policy_compiler.py` performs one-time lossless non-blank migration of
legacy workflow sources into local structured evidence. The capsule is not
loaded by destination sessions and is not public policy.
The implementation is platform-neutral and does not require a particular
host's thread API.

`session_migration.py` distinguishes `thread_delete` from `thread_archive`.
Both can complete the user-facing “old session removed from active use” step,
but the audit preserves whether the operation was physical deletion or archive.

`handoff_bundle.py` binds the full contract, event-backed state, continuity
facts, protected boundaries, next action, peers, destination identity and role
policy. The receiver fails closed when any external document read is required.
Native host actions remain plans for the host adapter to execute; they are not
claims that a session was created or a message was sent.

## Audit and rendering

Use `status`, `next-action`, and `audit` for projections. Audit checks the
contract-created first event, destination readiness before dispatch,
acknowledged delivery before execution, current-epoch supervision,
report-before-review, current-revision independent acceptance before
completion, delegation ownership, and controlled artifact references. Use the
management rendering for concise user-facing status and execution rendering
for technical handoff evidence.

## Repair completion

Use `repair-policy` to generate a project-neutral repair contract. Execution
then supplies `--repair-evidence-file` with its final or checkpoint report.
The code evaluates the original failure, first faulty production layer,
shared root-cause change, red-green regression, and direct consumers before it
can mark `productRootCauseClosed=true`.

When existing state also needs recovery, the contract separately configures
an isolated production-chain candidate, current identity rebinding, shared
validator recomputation, conservation scopes, optional external-call ledger
preservation, and snapshot-first zero-write guards for real data. Review may
inspect an incomplete report, but final acceptance is rejected until both the
product root cause and every required recovery gate pass.

## Secret boundary

Pass external text through `redact_text` before placing it in events, reports,
or handoffs. The engine recognizes common API-key, token, password, cookie,
Bearer, Authorization, and JWT-shaped values. It reports counts without
printing matches. Never put a real secret in a contract, fixture, receipt,
public repository, or Markdown handoff.

## Safe retention

`retention.py` maintains a manifest for artifacts generated and registered by
this Skill. Each item records generation, owner, kind, creation time, and
canonical/previous/ephemeral/retained flags. Cleanup requires a
management-confirmed handoff or independently accepted complete task and an explicit confirmation
that the current canonical version is committed in Git.

Run a dry-run first. Cleanup is fail-closed if ownership, path containment,
current/previous generations, or Git confirmation is missing. It never removes
user source, project files, real data, configuration, credentials, sessions,
dependencies, Git objects, commits, tags, releases, unregistered files, or
host-tool output. The default retention window is two generations and can be
changed in the controlled config. Manual collaboration instructions tell the
user to confirm the receipt and acceptance before running cleanup.

## Minimal verification

The built-in selftest covers native/manual probing, migration confirmation,
bootstrap receipt gates, direct receipt piping, role routing, acknowledged
delivery, supervision epochs, same-task correction, fresh independent review,
delegation ownership, read-only settings evidence, no-progress escalation,
checkpoint rejection, redaction, retention dry-run/apply, protected
current/previous artifacts, and idempotent cleanup. Extend tests only when the
project contract exposes a new direct consumer; do not build a second state
machine or parser in a consumer.

Read `references/contract-schema.md` for the input shape and
`references/protocol.md` for the event, receipt, relay, and retention
protocols. The installed package version is `0.2.0`. Collect every issue found
while fulfilling one continuous user request into one release batch and bump
the version only once after that batch is verified. Compatible fixes use a
patch release; backward-compatible capabilities use a minor release; a major
release requires explicit user authorization. Message numbers, intermediate
commits, pull requests, and internal rollout labels do not create compatibility
versions.
