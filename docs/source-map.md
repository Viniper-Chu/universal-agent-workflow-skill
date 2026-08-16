# Source mapping for 0.0.1

The public Skill was distilled from three role-oriented workflow source
documents. Their universal requirements are represented by mandatory IDs in
the packaged runtime policy and enforced by code; project history stays in
structured task continuity. The originals are not runtime dependencies and
may be retired after release acceptance.

| Reusable source concern | 0.0.1 implementation |
| --- | --- |
| authority, roles, and management/execution separation | contract fields, role routing, peer identities |
| intake, planning, authorization, and acceptance | `make_contract`, `plan.created`, review and completion gates |
| dispatch, execution, review, correction, and handoff loop | JSONL event state machine and `next_action` |
| native versus manual collaboration | capability inventory probe and manual relay generator |
| destination migration continuity | bootstrap packet, readiness receipt, destination gate |
| evidence, reports, and audit | controlled output root, report refs, `audit` |
| secret and privacy boundaries | redaction before event/report/handoff persistence |
| current/previous artifact lifecycle | manifest registration and retention dry-run/apply |
| concise user versus precise internal status | `render_status` audiences and JSON snapshots |
| verification and self-evolution | selftest, install validation, CI, SemVer metadata |
| no external Markdown at runtime | packaged JSON policy plus code-generated bootstrap and handoff receipts |

Project-specific names, paths, ports, accounts, providers, question data, and
historical incidents were intentionally excluded.
