# Universal Agent Workflow

Universal Agent Workflow is a small, standard-library-only Skill and workflow
engine for governed management/execution collaboration. It keeps a task
contract and JSONL event log as the state authority, then renders status,
reports, handoffs, readiness receipts, and retention plans for people and host
adapters.

## Install

Copy or link `packages/universal-agent-workflow` into the host Skill directory.
The exact destination must run its own validation; a machine-global install is
not proof that a new session has adopted the Skill.

```text
python packages/universal-agent-workflow/scripts/uaw.py validate-install \
  --skill-dir packages/universal-agent-workflow
python packages/universal-agent-workflow/scripts/uaw.py selftest
```

The current release is `0.0.3` and follows Semantic Versioning. Use the same version
in a destination bootstrap packet and readiness receipt.

One continuous user request is one release batch: collect all issues found
during implementation and real deployment, finish verification, then publish
one version update. Compatible fixes increment the patch version;
backward-compatible capabilities increment the minor version; a major version
requires explicit user authorization. Intermediate commits and pull requests
do not create extra versions.

## Release asset install/update

The official asset is
`https://github.com/Viniper-Chu/universal-agent-workflow-skill/releases/download/v0.0.3/universal-agent-workflow-0.0.3.zip`.
Download it into a controlled directory, then run the packaged CLI through the
same code path for a fresh install or an existing target update:

```text
curl -L <asset-url> -o universal-agent-workflow-0.0.3.zip
python <uaw-runner>/scripts/uaw.py install \
  --source universal-agent-workflow-0.0.3.zip \
  --target <skill-dir> --backup-root <controlled-backups>
python <skill-dir>/scripts/uaw.py validate-install --skill-dir <skill-dir>
python <skill-dir>/scripts/uaw.py selftest
python <skill-dir>/scripts/uaw.py destination-bootstrap \
  --skill-dir <skill-dir> --role execution \
  --destination-id <destination-id> --stable-session-id <stable-session-id> \
  --peer-identity <management-peer> \
  --tools codex_app__create_thread codex_app__send_message_to_thread \
  codex_app__wait_threads codex_app__read_thread \
  codex_app__set_thread_archived codex_app__navigate_to_codex_page
```

`deploy` is the CLI alias for `install`. The installer validates the complete
manifest before a recoverable replacement, distinguishes `install_required`,
`update_required`, `already_exact/current`, and `repair_required`, and refuses
to mutate a linked target that needs repair. The bootstrap receipt is the final
code-generated readiness check; a downloaded file or prompt alone is not proof
that a destination adopted the Skill.

## Quick start

```text
python packages/universal-agent-workflow/scripts/uaw.py init \
  --project-root <project> --output-root .agent-workflow
python packages/universal-agent-workflow/scripts/uaw.py plan \
  --project-root <project> --task-id example \
  --title "Example" --objective "Run a governed task"
python packages/universal-agent-workflow/scripts/uaw.py probe --tools \
  codex_app__create_thread codex_app__send_message_to_thread \
  codex_app__wait_threads codex_app__read_thread
```

Successful CLI commands emit exactly one JSON value on stdout. `policy` loads
the packaged executable workflow policy. `destination-bootstrap` validates the
exact install, selftest, policy and host capabilities in one call.

Before retiring legacy workflow documents, `source-migrate` records every
non-blank line as redacted structured evidence inside the controlled project
root and verifies the packaged runtime policy. That evidence is non-runtime;
new sessions continue from the JSON code-state handoff instead.

Use `references/contract-schema.md` and `references/protocol.md` for packet
and lifecycle details.

## Native and manual collaboration

The capability probe calls a host inventory rather than assuming that thread
tools exist. Native mode requires create, send, wait, and read. Manual mode
reports the missing capabilities and produces Markdown transport guidance
that only tells the user which code commands to run.

Migration intent first asks for confirmation. Native flow is:

```text
create/select destination -> run destination-bootstrap -> validate receipt
-> export JSON code-state bundle -> run handoff-receive -> validate receipt
-> dispatch business work
```

The receipt proves the exact Skill name/version, role, install or resolve
location, validation results, capability mode, destination identity, peer
identity, and `ready=true`. A prompt that was merely received is insufficient.
Manual handoff starts with the same code-backed deployment and validation and
is explicit when automatic host operations are unavailable. The Markdown file
is never workflow authority: the destination loads contract, event state,
continuity facts, protected boundaries, role policy and next action through
`handoff-receive`. A bundle requiring any external Markdown read is rejected.

After the target accepts the packet and management confirms handoff completion,
the management layer records explicit user-confirmed migration authorization;
only then can the optional migration policy remove the exact source session. A real
`thread_delete` is preferred. If only `thread_archive` exists, the source is
automatically archived and recorded as `SOURCE_SESSION_REMOVED` with
`removalMode=archive`; it is not described internally as physical deletion.
With neither capability the result is `MANUAL_SESSION_REMOVAL_REQUIRED`.
Missing or ambiguous IDs fail closed, as does any request that identifies the
source as the target or current receiving session. The target may equal the
current receiving session, which is the normal post-handoff case. Session
removal is independent from retention cleanup, and the engine returns a
precise host action without claiming that the host already executed it.

An execution role receiving a normal direct user request returns
`REDIRECT_TO_MANAGEMENT`. Only a complete, redacted management manual-relay
packet is accepted. Checkpoints do not complete tasks; independent acceptance
must follow an execution report.

## Retention and safety

The controlled `.agent-workflow` root contains contracts, state, reports,
handoffs, evidence, and temporary output. `retention.py` registers only
Skill-owned artifacts and records owner, kind, generation, creation time, and
canonical/previous/ephemeral/retained flags. After management-confirmed handoff
or independently accepted completion, use `next-action` to drive cleanup
dry-run, inspect its list, then apply it with explicit Git-current confirmation.
The CLI does not run cleanup in the background. Use `retention-rotate` to mark
a registered generation current; the adjacent generation becomes previous.

Cleanup keeps current, previous, and retained items and removes only registered
older or ephemeral Skill artifacts inside the controlled root. It never removes
user source, project files, real data, configuration, credentials, sessions,
dependencies, Git objects, commits, tags, releases, unregistered files, or
host-tool output. The default retention window is two generations.

All external text is redacted before it enters events or handoffs. Never place
API keys, tokens, passwords, cookies, JWTs, or authorization values in a
contract, fixture, report, receipt, or public repository.

## Development

No runtime dependency is required beyond Python 3.10+.

```text
python -m unittest discover -s tests -v
python packages/universal-agent-workflow/scripts/uaw.py selftest
python packages/universal-agent-workflow/scripts/uaw.py validate-install \
  --skill-dir packages/universal-agent-workflow
```

The CI workflow runs the same checks. The package stores universal rules in a
validated JSON policy and keeps project-specific history only in task
continuity bundles.

## License

MIT. See `LICENSE`.
