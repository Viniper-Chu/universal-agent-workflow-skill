# Contract schema

The engine accepts a JSON object with these required fields:

```json
{
  "task_id": "stable-task-id",
  "title": "short title",
  "objective": "measurable objective",
  "role": "management",
  "complexity": "simple",
  "work_type": "general",
  "acceptance": ["independent acceptance"],
  "allowed_actions": [],
  "forbidden_actions": [],
  "plan_steps": ["plan", "execute", "review"],
  "destination_role": "execution",
  "migration_policy": {"enabled": false},
  "skill_name": "universal-agent-workflow",
  "skill_version": "0.2.0"
}
```

Repair tasks add a code-generated `repair_policy`. It is optional for ordinary
work and required by management when the requested outcome is a repair:

```json
{
  "work_type": "repair",
  "repair_policy": {
    "schemaVersion": 1,
    "enabled": true,
    "productRootCauseRequired": true,
    "rootCauseStages": [
      "original_failure_reproduced",
      "first_fault_layer_identified",
      "shared_root_cause_fixed",
      "root_cause_regression_red_green",
      "direct_consumers_passed"
    ],
    "dataRecoveryRequired": true,
    "recovery": {
      "candidateMode": "isolated-production-chain",
      "realDataWrite": true,
      "identityKeys": ["source identity", "current tuple", "attempt version"],
      "sharedValidationChecks": ["quality", "source", "projection"],
      "conservationScopes": ["target", "same-container non-target", "other containers"],
      "snapshotBeforeWrite": true,
      "zeroWriteOnGuardFailure": true,
      "externalCallLedger": "preserve"
    }
  }
}
```

The list values are project facts; the code-backed gates and completion
semantics are universal. If recovery is not required, the builder emits
`candidateMode=not-required` and empty lists.

Cross-session continuity is not reconstructed from this reference or any
project Markdown. `handoff-export` embeds the validated contract, event-backed
state and a required continuity object in the code-state bundle. The
continuity object must contain project, objective, currentState, nextAction,
facts, protectedBoundaries, forbiddenActions, pendingDecisions and an empty
requiredExternalReads list.

`role` is one of `management`, `execution`, or `reviewer`. `complexity` is
`simple` or `complex`. A complex contract should state non-goals, risks,
outputs, and a direct acceptance oracle in its lists. The event log records
the lifecycle; the contract JSON is the immutable input projection and must
not be treated as a replacement state machine.

Every task has one management peer and one execution peer. A destination
role, peer identity, and stable task ID are required in bootstrap and handoff
packets. A completion event is legal only after an execution report,
independent non-checkpoint acceptance, and a validated destination receipt.
For a repair contract, acceptance additionally requires both
`productRootCauseClosed=true` and all configured recovery gates. A recovered
record with an open product root cause is projected as
`data_recovered_product_root_cause_open`.

Current-version dispatch also requires a delivery acknowledgement that binds
the dispatch ID, generated message ID, and destination thread ID before
execution starts. Supervision is a sequence of monotonic epochs; observations,
corrections, and review readiness must bind the current epoch. Each execution
report carries an increasing revision and its event cursor. A correction binds
that report, adds a correction ID and evidence delta, and continues the same
dispatch and execution task. Independent acceptance binds the current report
revision and cursor and requires a reviewer identity distinct from execution.

Structured delegation records parent/child agent identities, role, ownership
scopes, access mode, expected output, dependencies, and aggregator. Active
execution delegations with write access cannot overlap ownership scopes.
Management delegation is limited to management artifacts; reviewer delegation
is read-only. Settings inheritance evidence is observational and must declare
`readOnly=true`; it cannot authorize a model or preference mutation.

When `migration_policy.enabled` is true, source-session removal still requires
an explicit management event recording user-confirmed migration, the
destination receipt, target acceptance, management handoff completion, and
exact source/target/current session IDs. The source must differ from the
target and current receiving session; the target may equal the current
receiving session. The policy does not authorize
arbitrary session deletion or artifact cleanup. The request returns a precise
host action; only a later host success event enters the removed terminal state.

Retention is a separate next-action chain. After management-confirmed handoff
or independent completion, the engine emits a dry-run action, then an apply
action when candidates exist, and only then stops. Generation rotation marks
the new registered generation current and the adjacent generation previous.
