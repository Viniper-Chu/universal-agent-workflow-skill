# Changelog

## 0.1.0

- Added a code-backed repair contract that separates product root-cause
  closure from recovery of already affected data or artifacts.
- Added structured repair evidence for original-failure reproduction, first
  faulty production layer, shared fix, red-green regression and direct
  consumers; final acceptance now rejects data-only remediation.
- Added optional recovery gates for isolated production-chain candidates,
  current identity rebinding, shared validator recomputation, snapshot-first
  zero-write real-data mutation, non-target conservation scopes and external
  call ledger preservation.
- Added CLI builders/evaluators, event-backed outcome projection, handoff
  continuity, negative acceptance tests and complete package validation for
  the new repair policy module.

## 0.0.3

- Added one complete package manifest shared by installation planning,
  validation, recoverable deployment, and Release asset construction, with
  explicit missing/update/current/repair states.
- Added candidate-first deployment with same-filesystem backup/restore,
  lexical target paths, junction/reparse fail-closed handling, and zero-write
  invalid-candidate checks.
- Added a directly installable Release zip and a tag gate requiring
  `v<package VERSION>` before build or publication.
- Added strict current-version writes while allowing only read/replay of
  persisted 0.0.1/0.0.2 contracts.
- Corrected native dispatch supervision so management send results unlock
  execution while wait/read observation remains the review/correction gate.

## 0.0.2

- Added code-backed coordination policy for host-action states, ordered
  management migration, settings inheritance evidence, supervision gates and
  structured delegation requirements.
- Bound canonical dispatch to management send (`prompt`) then wait, with
  same-dispatch start/review evidence and read-only wait-failure fallback.
- Added zero-write negative tests, CLI/audit projections, and explicit
  execution-id evidence for migration-created threads.

## 0.0.1

- Added contract-first planning and an event-backed lifecycle.
- Added capability probing with honest native/manual selection.
- Added management/execution role routing and redacted manual relay packets.
- Added packaged code-validated runtime policy covering universal scope,
  planning, roles, collaboration, migration, retention and release rules.
- Added lossless non-blank source migration into redacted, structured,
  non-runtime local evidence before legacy workflow files are retired.
- Added code-generated destination bootstrap and exact-version readiness
  receipts that prove policy validation without external Markdown reads.
- Added self-contained JSON handoff export/receive with task state,
  continuity, boundaries, role policy and two-stage receipt gates.
- Added manual transport guidance that invokes the same code path instead of
  asking a destination to reconstruct state from Markdown.
- Added status, next-action, audit, selftest, and install validation commands.
- Added manifest-based two-generation retention with dry-run, fail-closed path
  checks, and safe idempotent apply.
- Added post-handoff source-session removal: real delete first, archive fallback
  as an explicit removal mode, and manual guidance when neither capability is
  available.
- Added Windows UTF-8 CLI output and one-request/one-release batching.
- Made contract creation atomic so a rejected actor cannot leave a contract
  without its authoritative first event.
- Bound destination-side readiness and handoff events to the contract's exact
  destination role, including management-to-management transfers.
- Bound handoff acceptance to the code bundle's exact source session; a
  destination that names itself as its peer now fails before event write.
