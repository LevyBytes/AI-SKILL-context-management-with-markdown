# Windows launcher

Open only when the selected project profile includes a Windows CMD launcher for its VS Code workspace.

## Final file contract

Render both the reusable [launcher source template](../assets/open-project-vscode.cmd.template) and the final `.cmd` as UTF-8 without a byte order mark and with LF line endings.

Do not convert project launchers to CRLF; LF keeps the artifact portable across supported editors, shells and platforms.

Begin with `setlocal EnableExtensions DisableDelayedExpansion` so environment changes remain process-local.

Resolve the manifest-declared workspace path relative to `%~dp0`, then use `cd /d "%~dp0"` to enter the launcher root.

The current launcher profile includes Claude temp routing: set `CLAUDE_CODE_TMPDIR` from the manifest-declared relative token and create the directory when absent.

Resolve `Code.exe` from the directory reported by `where code.cmd`, retain a workspace-file fallback and end with `endlocal`.

## PATH boundary

Do not use `set PATH=`, `setx`, registry edits or environment APIs that create, reorder, replace or reconstruct Process, User or Machine PATH.

Reading the existing PATH through `where code.cmd` is not a mutation.

An existing process-local reconstruction may be preserved only when project authority and the manifest identify its exact reviewed file; never generalize it into this launcher.

## Validation boundary

Validate bytes and executable command lines without running the launcher.

Do not open VS Code, infer terminal runtime behavior or claim the configured shell profile was exercised.

Treat missing `code.cmd` or `Code.exe` runtime evidence as not run, not as permission to rewrite PATH.
