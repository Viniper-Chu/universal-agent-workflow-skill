# Local work and browsers

Read this file when working with local files, installed software, or a browser.
These are defaults; explicit user destinations and repository conventions win.

## Files and Windows

- Prefer an existing output directory. Otherwise use `codex/运行残留` for scratch
  output, `codex/工作输出` for deliverables, `codex/脚本工具` for reusable helpers,
  and `codex/审查报告` for requested reports. Create only directories you use.
- Do not move an installed program, dependency directory, database, or runtime
  configuration merely to tidy the disk. Check its actual path references and
  supported relocation procedure. An empty folder can still be application-owned.
- For a requested cleanup, inspect the exact targets and their current use.
  Recursively remove or move only within the verified scope; do not follow a
  link into another directory. State what changed and what was skipped.
- Use native filesystem APIs or PowerShell with literal paths. Do not enumerate
  paths in one shell and pass them to another shell's deletion commands.
- Start background helpers hidden when they do not need user interaction.
  Keep a useful failure result rather than a window that flashes and disappears.

## Tools and browsers

- Use structured APIs or purpose-built connectors when they expose the required
  operation. Use the available in-app browser for its existing pages, Chrome
  integration for the user's Chrome session, and computer use when native UI
  interaction is needed or requested. Do not invent an unavailable tool capability.
- Reuse the selected browser and account. Opening another browser does not carry
  login state. Discover executable locations locally instead of embedding one
  person's Windows path in a public skill. Do not create unsolicited shortcuts.
- Carry existing authorization forward. Confirm an external mutation only when
  its account, destination, content, cost, or access impact is not already covered.
  A publication request authorizes publishing the prepared result to that stated
  destination; it does not authorize unrelated account or repository changes.
- RTK is optional. Use it for supported noisy commands if installed and useful.
  Run PowerShell built-ins normally. If compression obscures the error or detail
  needed for diagnosis, inspect the native output. No RTK install is required.
