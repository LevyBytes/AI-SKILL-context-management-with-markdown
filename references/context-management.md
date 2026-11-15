# Context management

Open when establishing or repairing durable project context shared by Claude Code, Codex and human maintainers.

## Authority layers

Give each fact class one authoritative layer and route to it instead of copying detail into every provider file.

A useful profile separates stable purpose and boundaries, current objectives, cohesive subject contracts, exact project decisions, one current checkpoint, and detailed task evidence.

Names such as `PROJECT_BRIEF.md`, `DECISIONS.md` and `CHECKPOINT.md` are recommended examples, not mandatory filenames; declare the actual paths in the setup manifest. The bundled feature-expansion profile is fixed: `FEATURES.md` is the index and expanded sibling documents use `FEATURE_<subject>.md`.

Keep plans, command transcripts, fixtures and recovery material in the project-declared evidence location rather than the always-loaded router.

## Router ownership

Use trigger lines that state both when to open a target and what authority or content it provides.

Keep shared context neutral; add thin provider-specific routes only for genuine Claude or Codex differences.

If a router is generated, declare its source, edit that source and run the owning regeneration workflow; never patch generated output directly.

If a router is hand-maintained, edit it under repository instructions and keep its links and authority statements current.

Set `context.router_ownership` to `generated` or `hand-maintained`; generated mode requires `context.router_source`, while hand-maintained mode keeps that field null.

Use [the router template](../assets/context-router.md.template) as a starting shape after selecting the ownership mode.

## Semantic expansion

Split a subject when it acquires its own coherent decision history, behavior contract, implementation state, verification, wording, sources or unresolved points.

Do not split by a numerical line threshold and do not create speculative empty context documents.

After extraction, remove duplicated derived detail from objectives, checkpoints and provider routers while preserving the authoritative decision record.

Use [the workstream index](../assets/features.md.template) and [feature subject](../assets/feature-subject.md.template) templates only when that profile fits the project.

## Decisions and checkpoints

When exact user, maintainer or project-authority wording matters, retain it with a date and explicit supersession rather than silently rewriting history.

Use [the decision template](../assets/decisions.md.template) for that optional quotation layer and keep derived implications in the relevant subject document.

Keep one actionable checkpoint with the current objective, latest authority and approval boundary, completed work, exact changed paths, verification by status, next authorized action, first safe command or explicit absence, active processes, genuine blockers, reopen paths and the owning work folder; rotate prior detail to evidence.

Use [the checkpoint template](../assets/checkpoint.md.template) when those recovery fields match the selected workflow.

## Compaction and resume

Before compaction or handoff, update the current checkpoint after the latest completed operation rather than relying on a transcript summary.

Record critical identifiers, current hashes or revisions, pending external actions, required project decisions and optional workflow or rule identifiers only when the project actually uses them.

On resume, read the current router, newest checkpoint and relevant decision record, then reopen every listed source or evidence path before acting.

Treat a summary as a locator, not proof; reconcile current bytes, provider configuration, running processes and external state with the checkpoint before continuing.

## Stale-context checks

Before reporting status, read the smallest current source and evidence set that can confirm it.

Treat current source, provider configuration and external state as stronger than historical summaries or old test claims.

Report passed, failed, skipped and not-run checks separately; focused local evidence does not prove deployment, publication, remote CI or semantic acceptance.

Use [the topic catalog](topics.json) for keyword-based reference selection without loading unrelated guidance.
