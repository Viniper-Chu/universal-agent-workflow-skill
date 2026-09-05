---
name: astra-workflow
description: "Run an authorized multi-step task through implementation, focused verification, and delivery with GPT-6 Astra. Use for lightweight autonomous execution, continuation, or handoff when a formal workflow engine is unnecessary. Existing governed tasks keep their agreed protocol."
---

# Astra Workflow

Use the least process that can deliver and verify the user's outcome. This is
a workflow preference, not an override of host permissions or project contracts.
It does not change the selected model or install other skills.

## Execute and finish

- Read the relevant files and carry forward the user's objective, corrections,
  and existing authorization. Resolve routine choices yourself. Do not repeat
  completed investigation after a continuation or compaction.
- Small tasks need no contract, task ledger, rubric, or delegation. For larger
  work, keep a short plan containing the outcome, affected scope, and how to
  verify it. Update the plan when the work actually changes.
- Implement, verify, and deliver within the authorized scope. Ask only for
  information that materially affects correctness or for an action whose
  concrete impact is not yet authorized. Finish independent preparation while
  awaiting the answer. Silence is not authorization.
- A tool's access denial describes that operation, not every future operation.
  Report the denied action and actual reason. Use supported authorization
  mechanisms when available; do not disguise the same action to bypass a denial.

## Keep verification useful

- Use the repository's existing commands and conventions. Test the changed
  behavior and its likely consumers; stop expanding tests after these pass
  unless another change, failure, or unresolved concern warrants it.
- For a bug, locate the first failing layer from reproduction, logs, or other
  direct evidence, then verify the repair. If intermittent behavior is not
  observed, state what remains unproven instead of changing unrelated settings.
- Check the artifact the user will actually consume: rendered document,
  installed build, working interface, saved configuration, or deployed content.
  A source test alone does not establish deployment or usability.
- Keep claims bounded: API connectivity does not prove long-task reliability;
  writing a config does not prove a running process adopted it. Report the
  observable result and any remaining limitation in plain language.

## Protect work without ceremony

- Keep temporary output in the project's established output area. If none
  exists, use `codex/` and create only the needed subfolders. Put final files
  where the user requested them. Read [local-work.md](references/local-work.md)
  only for Windows/browser operations or organizing local files.
- Preserve unrelated edits, original documents, credentials, sessions, and
  user data. Before an authorized destructive change, identify exact targets
  and whether a recovery copy is needed. Names such as `old` or `temp` are not
  evidence that a file is disposable. Never publish local secrets or transcripts.
- Use existing Git history for recovery. Do not create a repository for a
  one-off system check or rewrite history to retain only two commits. A
  current/previous retention rule applies only to identified generated outputs,
  within an authorized cleanup scope, not to unique work or Git history.
- Use checksums, schemas, rubrics, or additional tests when they resolve a real
  uncertainty. Do not generate them as routine ceremony or ban them universally.

## Coordinate only when it helps

Delegate independent work when it saves time or provides a useful second view.
Give each worker a concrete output and non-overlapping write scope. Reuse the
current task for corrections; do not create replacement tasks just to retry.
Creating separate user-visible tasks requires the user's request and host support.
Use observed tool receipts to distinguish sent, received, running, and complete.

For a cross-session transfer, read [handoff.md](references/handoff.md). Ordinary
handoffs need a concise factual packet. Tasks already bound to the legacy UAW
engine keep their contracts, receipts, and acceptance rules; this skill neither
migrates their state nor lets a worker self-approve an independent review.

Finish with the result, relevant verification, artifact location when applicable,
and unresolved work. Prefer a few clear sentences over a mandatory report format.
