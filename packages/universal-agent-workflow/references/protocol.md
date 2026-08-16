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
-> dispatch.requested -> execution.started -> execution.reported
-> review.accepted -> completion.requested
```

Correction and blocked transitions are explicit. A checkpoint cannot complete
a task.

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
