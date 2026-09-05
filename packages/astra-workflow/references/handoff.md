# Continue or hand off

Read only when changing workers or sessions, or continuing after context loss.

For ordinary work, reuse the current conversation unless the user requested a
new task or a supported worker would help. A changed topic detail is not a request
to move sessions. Read the latest relevant state once and continue completed work.

When a transfer is needed, send one small packet with:

- The user's objective and accepted corrections.
- The actual workspace and affected files or branch; note unrelated dirty edits.
- What is complete, what was verified, and what remains uncertain.
- Existing authorization and any pending consequential decision.
- The next useful action and the evidence needed to finish.

Use real returned task IDs. Send through an available host tool and observe its
delivery/status result. A successful send is not proof the worker finished. Wait
or read only as needed to collect new progress; an unchanged cursor or timeout
alone does not prove a stall. Avoid repeated whole-thread reads.

Secrets and private conversation dumps do not belong in handoff documents.
Do not archive or delete the source task as an automatic side effect.

## Existing governed UAW tasks

The repository also ships `universal-agent-workflow` 0.2.0 for users with an
existing event-backed management/execution contract. If the task actually uses
that protocol, use that package and its current code-generated state. Its
destination bootstrap, peer identity, dispatch, supervision, and independent
acceptance requirements still apply to that task. This reference is not a new
implementation of the protocol and is not permission to skip a failing gate.

Do not load or initialize that engine for an ordinary task. If it is genuinely
required but unavailable, report the missing runtime and continue whatever
independent work is authorized. A future engine migration is a separate change
with compatibility testing; never relabel old receipts as a new version.
