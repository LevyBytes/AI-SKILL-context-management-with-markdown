# Workspace and access

Open when creating or validating VS Code folder topology and Claude or Codex access boundaries.

## Root topology

Choose one topology explicitly:

- Single-root: the context root and source root are the same repository.
- Split-root: a context or documentation root is separate from one source root.

Add auxiliary roots only when current project authority explicitly places them in scope.

The public default is single-root with an empty `project.name_prefix`. A nonempty value is declarative leading text that an initializing agent prepends to selected project names; it does not rewrite paths automatically. Kaizen and other custom profiles must declare the resulting names, split roots and additional conventions explicitly; the skill has no implicit library-specific mode.

Order workspace folders as the context root, a distinct source root when present, then approved auxiliaries; resolve every relative path against the workspace file before comparing it with the manifest.

Use [the single-root template](../assets/single-root.code-workspace.template.json) or [split-root template](../assets/split-root.code-workspace.template.json), render JSON placeholders safely and remove settings that do not apply.

## Workspace settings

Give the window title a project-specific identity and set terminal cwd to the declared working root.

Set `files.eol` to `\n` as the portable default for project text, while preserving reviewed existing bytes and any stricter repository-owned generation rule.

Configure a Python interpreter and activation behavior only when the project uses Python; do not invent a shared interpreter requirement for other projects.

Add cache and project-specific search exclusions only when they reduce generated noise without hiding maintained source.

Keep workspace folder paths relative when practical, then validate their resolved order and identity.

## Provider matrices

This skill requires explicit Claude and Codex matrices, but defines their access independently; roots and order need not match.

Use [the Claude settings template](../assets/claude-settings.template.json) and [Codex config template](../assets/codex-config.template.toml), then strictly parse emitted JSON and TOML.

Declare and verify whether Codex excludes inherited host temp variables before applying project-local `TEMP` and `TMP`; the public template enables that isolation.

Project-local temp isolation is recommended, but each temp directory is a manifest choice rather than a fixed `work` path.

The reusable Claude template enables outside-write blocking as its public safety default and leaves Git denials empty. A project may change either choice explicitly; network access, compaction, model selection, personality and extra tool roots also remain project decisions.

## PATH policy

Never persist, create, reorder, replace or reconstruct Process, User or Machine PATH without exact project authority for that mutation.

Treat `terminal.integrated.env.*` PATH keys and PATH keys nested under named or automation terminal-profile `env` maps as process PATH replacement, not ordinary workspace metadata.

Treat CMD `path` assignments and PowerShell direct, braced, environment-drive, and process-target environment assignments as the same protected mutation class.

A preserved existing process-local mutation must be named in the manifest and bound to the exact reviewed file SHA-256; preservation does not authorize copying or changing it.

## Preservation

Inspect existing workspace and provider files before setup, preserve unrelated keys and content, and make repeated execution idempotent.

Do not adopt, migrate or delete historical context, provider, workspace, installation or tool artifacts merely because they exist.

Use the manifest to name workflow-owned paths that must remain present and report idempotence as not run when it was not exercised.
