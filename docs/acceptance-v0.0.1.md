# Acceptance v0.0.1

Status: release candidate until a fresh public repository, tag and real
management/execution handoff have passed.

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
