# Design notes

## State authority

The append-only JSONL event log is the source of truth. A task contract is an
input artifact validated at creation. The task JSON snapshot is regenerated
from the contract and events for convenient reading; it is not independently
edited to advance a task.

## Adapter boundary

`probe_capabilities` accepts a host-provided inventory and returns native or
manual mode. The engine creates action plans but never invents a thread tool.
An adapter owned by the host performs real create/send/wait/read operations.
This keeps platform names out of the public Skill and makes missing host
capabilities explicit.

## Destination safety

Bootstrap and handoff are separate from business dispatch. The destination
runs one code-backed bootstrap that validates install, selftest, packaged
runtime policy and host capabilities. Formal continuity is then exported as a
self-contained JSON bundle and consumed through the code receiver. The state
machine rejects handoff acceptance without both receipts and rejects every
bundle that requires external document reads.

## Runtime policy

Universal workflow requirements are stored as versioned structured rules in
`assets/workflow-policy.json`. `workflow_policy.py` enforces mandatory rule
coverage and renders a role profile. Project-specific facts are not policy;
they belong to the handoff continuity payload.

## Retention safety

The manifest is a capability boundary, not a general filesystem cleaner. Only
registered, owned, contained artifacts can become candidates. Cleanup is gated
by management-confirmed handoff or independently accepted completion and
explicit Git confirmation. `next-action` drives dry-run then apply; there is
no background cleanup. Current, previous, and retained generations survive.
