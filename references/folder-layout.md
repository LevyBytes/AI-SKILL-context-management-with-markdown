# Folder layout

Open when selecting context, evidence, deliverable, workspace and temp destinations.

## Declare roles, not a universal tree

Start from the project's existing structure and assign only the roles the workflow needs.

Typical roles include provider settings, durable context, a VS Code workspace file, task evidence, maintainer-facing deliverables and provider-local temp storage.

`.claude` and `.codex` are appropriate only when the corresponding provider is enabled; `docs`, `work`, `user` and `_workspace` are conventions, not required public defaults.

No universal folder-name prefix is required. The optional `project.name_prefix` field records leading text to prepend when an initializing agent chooses project names; for example, `Acme_` and `Planner` produce `Acme_Planner`, never `Planner_Acme`. The public default is the empty string. The field is declarative and changes no path by itself, so record every resulting root and workspace folder name explicitly in the manifest.

Project-name prefixing is separate from the fixed expanded-feature convention: keep `FEATURES.md` as the index and name each expanded sibling `FEATURE_<subject>.md`.

## Single-root and split-root projects

In a single-root project, context, source and ordinary repository instructions remain together unless the project chooses otherwise.

In a split-root project, keep durable context and evidence in the context root while source-specific files remain under the source repository's instructions.

Do not move maintained source, runtime data or existing documentation merely to imitate another project's layout.

## Artifact routing

Declare the evidence directory and task naming convention in project context or the manifest.

Place reusable context where both agents can discover it, and place detailed command output or recovery material outside the always-loaded router.

Choose deliverable and temp locations explicitly; do not assume a `user` directory or `work\temp` and `work\tmp` paths.

Database engines, schemas, migrations, initialization and database-specific validation are outside this skill; use a separately selected workflow when a project needs them.

Add generation, graph, lint, runtime, session-data, project-skill or language-specific trees only when the selected workflow owns and uses them.

## Idempotence

Create only missing declared folders without deleting or replacing existing directories.

Compare planned output with existing bytes, preserve unowned content and report conflicts instead of using force as a default.

Record exact created and changed paths so a later run can distinguish preservation from accidental omission.
