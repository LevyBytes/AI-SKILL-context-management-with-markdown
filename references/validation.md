# Validation

Open when preparing an explicit setup manifest or proving an emitted workspace against it.

## Manifest first

Start with the schema-4 public-default [single-root manifest](../assets/setup-manifest.template.json) or the [split-root manifest](../assets/split-root.setup-manifest.template.json), copy it into the selected evidence directory, replace placeholders through a JSON-aware writer and strictly parse it before project configuration.

The manifest declares topology, optional project-name leading text, paths, both provider matrices, optional Python settings, required folders, router ownership, the `FEATURES.md` index and expanded feature documents, optional launcher behavior, PATH policy and preservation expectations.

Keep machine-specific absolute paths outside the portable skill.

## Deterministic validator

Run `scripts\validate-project-setup.py` with Python 3.12 or newer, an explicit context root and manifest path.

Reject duplicate JSON keys, nonstandard constants and malformed TOML; use standard-library `tomllib` from the selected interpreter.

Resolve workspace folders before comparing order, require every declared topology root to exist, validate only declared Python and provider contracts, and check provider access, Codex temp inheritance and temp settings independently.

Require the workspace's `files.eol` setting and manifest policy to select LF for the generalized text-file default.

Check required context documents and actual inline Markdown router links according to declared router ownership; comments, code spans, CommonMark fences, malformed links and raw path substrings are not links.

Require every declared expanded feature to be a unique `FEATURE_<subject>.md` sibling of the declared `FEATURES.md` index, to appear in `required_documents` and to have a real Markdown link from that index. Replace the manifest template's `{subject}` marker before validation; a subject is nonempty, contains no whitespace or unresolved braces and does not end in `_FEATURE`. Allow an empty feature-document list before any subject expands; reject suffix-style or dual-direction aliases.

When the Windows launcher profile is enabled, require a root-level `.cmd` file with BOM-less LF bytes, align its workspace and Claude-temp targets with the manifest, and verify the exact conditional Code.exe/fallback control flow without launching it.

Reject persistent PATH mutation, provider PATH replacement and process mutation not bound to an explicitly reviewed file SHA-256.

Remain read-only and verify inspected bytes are unchanged after validation.

## Status classes

- `passed`: the named observable check succeeded.
- `failed`: setup violates the manifest or input cannot be parsed safely.
- `skipped`: a check is intentionally inapplicable under the selected profile.
- `not-run`: evidence was not obtained, such as GUI launch, terminal runtime or repeated setup execution.

Print every result with its reason and separate counts; exit nonzero when a required check fails.

## Bounded fixtures

Exercise valid zero-feature, prefix-feature, single-root, split-root, generated-router and hand-maintained-router fixtures plus negative cases for legacy schema, invalid JSON, suffix-style, dual-direction, unresolved-placeholder or nested feature names, missing feature membership or links, wrong or missing roots, physical containment escapes, unauthorized roots, broken router links, workspace EOL drift, launcher suffix or command drift, CRLF and PATH mutation. Require each focused feature fixture to fail with only its intended feature-contract code.

Keep fixtures under an approved evidence or temp directory and never target production data, proprietary archives or external installations.

Assert each expected failure code and nonzero validation exit rather than accepting any failure.

## Reporting boundary

Record interpreter versions, exact commands and separate passed, failed, skipped and not-run results in the selected evidence record.

A positive validation does not establish source builds, Git state, provider terminal runtime, GUI launch, deployment, remote CI, publication or semantic project acceptance.
