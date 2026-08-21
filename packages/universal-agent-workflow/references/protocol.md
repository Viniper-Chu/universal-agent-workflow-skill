# Collaboration protocol

## Capability mode

Probe the host inventory before planning a cross-session action. Native mode
requires create, send, wait, and read. A specialized handoff tool is optional.
Manual mode lists missing core capabilities and never claims a host action ran.

## Code-backed bootstrap

Management sends a `skill-bootstrap` packet containing the exact Skill name,
version, destination role and identity, install source, peer identity,
capability mode, and one `destination-bootstrap` command. The destination runs
that command. Code validates the install, selftest, packaged runtime policy and
host inventory, then emits the readiness receipt.

The receipt includes name/version, role, install/resolve path, test and policy
status, capability mode, destination and peer identities,
`runtimeAuthority=code-state`, `externalReadsRequired=false`, and `ready=true`.
Native receipts carry a stable session identity. Manual receipts explicitly
declare when no stable identity exists. Hand-authored receipts without the code
validation source and policy proof are rejected.

The receipt validator also accepts the complete JSON object emitted by
`destination-bootstrap`, unwraps its nested readiness receipt, and derives the
destination actor, role, and peer identity from validated code state. A caller
does not need to inspect or rewrite that JSON by hand.

## Code-state handoff

After readiness, management runs `handoff-export`. The resulting
`uaw-code-handoff` JSON contains the full contract, continuous event history,
current state, continuity facts, protected boundaries, forbidden actions,
pending decisions, next action, role policy, peers, and exact source and
destination identities.

The destination runs `handoff-receive`. Code checks Skill version, role,
destination identity, task identity, event order, role policy and the
continuity schema. Any external document dependency fails closed. A successful
receiver emits `uaw-code-handoff-receipt` with
`externalReadsRequired=false`. Handoff acceptance is illegal before this
second receipt.

Optional evidence may still point at reports or documents, but the destination
must not need them to reconstruct runtime state.

## Manual relay

Manual Markdown is user-forwardable transport guidance, not workflow
authority. The first relay invokes `destination-bootstrap`. After readiness,
management generates a second relay around the JSON bundle with the exact
`handoff-receive` command. The destination returns both code-generated
receipts. A legacy management relay is accepted only when exact Skill identity,
authorization and redaction flags pass.

## Role routing

Management is user-facing and uses plain language. Execution reports technical
state to management. A normal direct user business instruction arriving at an
execution role returns `REDIRECT_TO_MANAGEMENT`. A validated manual relay or
code-state handoff is accepted.

## Event order

The shortest migrated execution path is:

```text
contract.created -> plan.created -> bootstrap.requested -> destination.ready
-> handoff.requested -> handoff.bundle_received -> handoff.accepted
-> dispatch.requested -> coordination.supervision_updated
-> host-action.planned(send) -> host-action.planned(wait)
-> host-action.sent(send with delivery acknowledgement) -> execution.started
-> host-action.observed(wait) -> execution.reported
-> review.accepted -> completion.requested
```

The dispatch send must use the host adapter's `prompt` argument. Its host result
must acknowledge delivery and bind the dispatch, message, and destination
identities before execution starts. Each wait/read round has a monotonic
supervision epoch. Review/correction requires the current epoch's observed
wait; a failed wait requires an observed read-only `read_thread` fallback.

Correction is an explicit same-task branch:

```text
execution.reported(revision N) -> review.correction_requested(correction ID)
-> supervision epoch N+1 -> acknowledged correction delivery
-> current-epoch observation -> execution.reported(revision N+1)
```

It never repeats `execution.started` or creates a replacement execution task.
Independent review binds the current report revision, report event cursor,
reviewing state, and a reviewer identity distinct from execution. Repeated
observed progress cursors request a blocker diagnosis and narrowed next action
from the same task; timeouts alone do not count as no-progress evidence. A
checkpoint cannot complete a task.

## Delegation and settings

Structured delegation binds parent and child agent identities, role,
ownership scopes, access mode, expected output, dependencies, and aggregator.
Management delegates management artifacts, execution delegates implementation
branches, and reviewer delegation is read-only. Concurrent execution writers
cannot own overlapping scopes. A completion event releases the ownership only
after the expected output is recorded.

Settings inheritance evidence is read-only. It may prove that destination
settings match the management source or that the user changed them, but it
never authorizes automatic model, intelligence, or preference mutation.

## Repair evidence

For a contract with `repair_policy`, every `execution.reported` event carries
code-validated `repairEvidence`. The engine derives two independent values:
`productRootCauseClosed` and `dataRecovered`. Incomplete evidence is allowed
for correction or checkpoint review, but `review.accepted` fails until the
combined outcome is `complete`.

The root-cause gate covers the original failure, first faulty production layer,
shared fix, red-before/green-after regression and direct consumers. When
recovery is required, its gate covers isolated production-chain rebuilding,
current identity rebinding, shared validator recomputation, snapshot-first
zero-write mutation for real data, configured non-target conservation scopes,
and optional external-call ledger preservation. This evidence travels inside
the event-backed snapshot and code handoff; no Markdown is needed to recreate
the decision.

## Source-session removal

After destination readiness, code handoff acceptance, management handoff
confirmation, and explicit user-confirmed migration authorization, the engine
may request removal of one exact source session. It distinguishes physical
delete from archive. If only archive exists, archive is a successful removal
from active use and remains labelled `removalMode=archive`. If neither exists,
return precise manual instructions. Source, target and current identities must
be present; source must differ from both target and current, while target may
equal the current receiving session.

## Retention

Only registered Skill-owned artifacts inside the controlled root are
candidates. Dry-run precedes apply. Cleanup requires accepted completion or a
management-confirmed handoff and Git-current confirmation. Current, previous,
retained, unregistered, external, user and Git artifacts remain protected.
