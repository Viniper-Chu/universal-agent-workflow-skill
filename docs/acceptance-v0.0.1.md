# Acceptance v0.0.1

Status: PASS.

The release must prove:

- package validation and all unit/selftests pass;
- the three legacy workflow sources migrate with full non-blank structured
  coverage into redacted local evidence that is explicitly non-runtime;
- destination bootstrap loads the packaged runtime policy in code;
- native and manual capability modes are distinguished honestly;
- a self-contained JSON handoff is accepted by the exact destination without
  required external Markdown reads;
- wrong destination, role, version, event order, missing policy and any
  external-read requirement fail closed;
- management/execution routing, supervision, independent acceptance, source
  session archive/delete gating and two-generation retention remain active;
- Windows paths containing non-ASCII characters produce valid UTF-8 JSON;
- a fresh real management task and execution task adopt the exact Skill and
  accept their code-state handoffs before old tasks are archived.

Observed acceptance:

- 21 unit tests, the built-in selftest, install validation, structure
  validation and public CI passed;
- all three legacy sources migrated with complete non-blank structured
  coverage and explicit non-runtime status before their retirement;
- a fresh management task and one fresh execution task both ran code-backed
  destination bootstrap and self-contained JSON handoff receive without
  reading external workflow Markdown;
- the fresh execution contract entered `executing` with a zero-error audit;
- real deployment exposed and then verified fixes for atomic task creation,
  destination-role signing and exact source-peer identity;
- a self-referential peer was rejected without appending an event, while the
  exact source management identity was accepted.
