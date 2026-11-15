#!/usr/bin/env python3
"""Validate a Windows project-context setup against an explicit manifest."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import sys
import tempfile
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable
from unittest import mock


STATUSES = ("passed", "failed", "skipped", "not-run")


class StrictJsonError(ValueError):
    """Raised when JSON is not strict enough for project configuration."""


def reject_constant(value: str) -> None:
    raise StrictJsonError(f"non-standard JSON constant: {value}")


def unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise StrictJsonError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def strict_json(data: bytes, label: str) -> Any:
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise StrictJsonError(f"{label} is not UTF-8: {exc}") from exc
    try:
        return json.loads(
            text,
            object_pairs_hook=unique_object,
            parse_constant=reject_constant,
        )
    except (json.JSONDecodeError, StrictJsonError) as exc:
        raise StrictJsonError(f"{label}: {exc}") from exc


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def canonical(path: str | Path) -> str:
    absolute = os.path.abspath(os.fspath(path))
    return os.path.normcase(os.path.normpath(os.path.realpath(absolute)))


def is_within(root: str | Path, candidate: str | Path) -> bool:
    try:
        return os.path.commonpath((canonical(root), canonical(candidate))) == canonical(root)
    except ValueError:
        return False


def same_path(left: str | Path, right: str | Path) -> bool:
    return canonical(left) == canonical(right)


def project_path(root: Path, relative: str) -> Path:
    candidate_text = relative.replace("/", os.sep)
    candidate = Path(candidate_text)
    if candidate.is_absolute():
        raise ValueError(f"project-relative path is absolute: {relative}")
    resolved = Path(os.path.abspath(root / candidate))
    if not is_within(root, resolved):
        raise ValueError(f"project-relative path escapes root: {relative}")
    return resolved


def require_mapping(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be an object")
    return value


def require_list(value: Any, label: str) -> list[Any]:
    if not isinstance(value, list):
        raise ValueError(f"{label} must be an array")
    return value


@dataclass(frozen=True)
class Check:
    status: str
    code: str
    detail: str


class Report:
    def __init__(self) -> None:
        self.checks: list[Check] = []

    def add(self, status: str, code: str, detail: str) -> None:
        if status not in STATUSES:
            raise ValueError(f"unknown status: {status}")
        self.checks.append(Check(status, code, detail))

    def pass_(self, code: str, detail: str) -> None:
        self.add("passed", code, detail)

    def fail(self, code: str, detail: str) -> None:
        self.add("failed", code, detail)

    def skip(self, code: str, detail: str) -> None:
        self.add("skipped", code, detail)

    def not_run(self, code: str, detail: str) -> None:
        self.add("not-run", code, detail)

    def count(self, status: str) -> int:
        return sum(check.status == status for check in self.checks)

    @property
    def failed_codes(self) -> set[str]:
        return {check.code for check in self.checks if check.status == "failed"}


class Inputs:
    def __init__(self) -> None:
        self.initial: dict[Path, str] = {}

    def read(self, path: Path) -> bytes:
        data = path.read_bytes()
        self.initial.setdefault(path, sha256(data))
        return data

    def unchanged(self, report: Report) -> None:
        changed: list[str] = []
        missing: list[str] = []
        for path, before in self.initial.items():
            if not path.is_file():
                missing.append(str(path))
                continue
            if sha256(path.read_bytes()) != before:
                changed.append(str(path))
        if missing or changed:
            report.fail(
                "validation_read_only",
                f"inspected inputs changed or disappeared; changed={changed}, missing={missing}",
            )
        else:
            report.pass_(
                "validation_read_only",
                f"all {len(self.initial)} inspected input files retained their SHA-256 values",
            )


def load_json(path: Path, label: str, inputs: Inputs, report: Report, code: str) -> Any:
    try:
        value = strict_json(inputs.read(path), label)
    except (OSError, StrictJsonError) as exc:
        report.fail(code, str(exc))
        return None
    report.pass_(code, f"{label} parsed as strict JSON")
    return value


def load_toml(path: Path, inputs: Inputs, report: Report) -> Any:
    try:
        value = tomllib.loads(inputs.read(path).decode("utf-8"))
    except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError) as exc:
        report.fail("codex_toml", f"Codex TOML parse failed: {exc}")
        return None
    report.pass_("codex_toml", "Codex configuration parsed with Python tomllib")
    return value


def compare(report: Report, code: str, actual: Any, expected: Any, detail: str) -> None:
    if actual == expected:
        report.pass_(code, detail)
    else:
        report.fail(code, f"{detail}; expected={expected!r}, actual={actual!r}")


def canonical_list(values: Iterable[Any]) -> list[str]:
    return [canonical(str(value)) for value in values]


def canonical_set(values: Iterable[Any]) -> set[str]:
    return set(canonical_list(values))


def compare_unique_path_set(
    report: Report,
    code: str,
    actual: Iterable[Any],
    expected: Iterable[Any],
    detail: str,
) -> None:
    actual_paths = canonical_list(actual)
    expected_paths = canonical_list(expected)
    actual_duplicates = sorted({path for path in actual_paths if actual_paths.count(path) > 1})
    expected_duplicates = sorted({path for path in expected_paths if expected_paths.count(path) > 1})
    if actual_duplicates or expected_duplicates:
        report.fail(
            f"{code}_duplicates",
            f"provider roots must be unique; configured={actual_duplicates}, manifest={expected_duplicates}",
        )
    else:
        report.pass_(f"{code}_duplicates", "configured and manifest provider roots are unique")
    compare(report, code, set(actual_paths), set(expected_paths), detail)


WORKSPACE_TOKEN = re.compile(r"\$\{workspaceFolder:([^}]+)\}")


def expand_workspace_value(value: str, folders: dict[str, Path]) -> str:
    def replace(match: re.Match[str]) -> str:
        name = match.group(1)
        if name not in folders:
            raise ValueError(f"unknown workspace folder token: {name}")
        return str(folders[name])

    return WORKSPACE_TOKEN.sub(replace, value)


PERSISTENT_PATH_PATTERNS = (
    re.compile(r"\bsetx(?:\.exe)?\b[^\r\n]*\bpath\b", re.IGNORECASE),
    re.compile(
        r"SetEnvironmentVariable\s*\([^\r\n]*['\"]Path['\"][^\r\n]*(?:['\"](?:User|Machine)['\"]|EnvironmentVariableTarget\]::(?:User|Machine))",
        re.IGNORECASE,
    ),
    re.compile(r"\breg(?:\.exe)?\s+add\b[^\r\n]*\bpath\b", re.IGNORECASE),
)
PROCESS_PATH_PATTERNS = (
    re.compile(r"\$env:Path\s*(?:\+|-|\*|/)?=", re.IGNORECASE),
    re.compile(r"\$\{\s*env:Path\s*\}\s*(?:\+|-|\*|/)?=", re.IGNORECASE),
    re.compile(r"\bset\s+\"?PATH\s*=", re.IGNORECASE),
    re.compile(r"(?<![\w.:$])@?path(?:\.exe)?(?:\s*=|\s+)(?=[^\s\r\n])", re.IGNORECASE),
    re.compile(
        r"\b(?:Set|New|Remove|Clear)-(?:Item|Content)\b[^\r\n]*(?:Env:Path|Env:\{Path\})",
        re.IGNORECASE,
    ),
    re.compile(
        r"SetEnvironmentVariable\s*\([^\r\n]*['\"]Path['\"][^\r\n]*(?:['\"]Process['\"]|EnvironmentVariableTarget\]::Process)",
        re.IGNORECASE,
    ),
)


def matching_patterns(text: str, patterns: Iterable[re.Pattern[str]]) -> list[str]:
    return [pattern.pattern for pattern in patterns if pattern.search(text)]


INLINE_MARKDOWN_LINK = re.compile(
    r"(?<!!)\[[^\]\n]+\]\(\s*(?:<([^>\n]+)>|([^\s)\n]+))"
    r"(?:\s+(?:\"[^\"\n]*\"|'[^'\n]*'|\([^()\n]*\)))?\s*\)"
)


def strip_inline_code(line: str) -> str:
    visible: list[str] = []
    index = 0
    while index < len(line):
        if line[index] != "`":
            visible.append(line[index])
            index += 1
            continue
        end = index
        while end < len(line) and line[end] == "`":
            end += 1
        delimiter = line[index:end]
        closing = line.find(delimiter, end)
        if closing < 0:
            visible.append(delimiter)
            index = end
            continue
        visible.append(" " * (closing + len(delimiter) - index))
        index = closing + len(delimiter)
    return "".join(visible)


def markdown_link_targets(text: str, router: Path, root: Path) -> set[str]:
    without_comments = re.sub(r"<!--.*?-->", "", text, flags=re.DOTALL)
    visible_lines: list[str] = []
    fence: tuple[str, int] | None = None
    for line in without_comments.splitlines():
        marker = re.match(r"^ {0,3}(`{3,}|~{3,})(.*)$", line)
        if marker:
            run = marker.group(1)
            token = run[0]
            if fence is None:
                fence = (token, len(run))
            elif fence[0] == token and len(run) >= fence[1] and not marker.group(2).strip():
                fence = None
            continue
        if fence is None:
            visible_lines.append(strip_inline_code(line))
    targets: set[str] = set()
    for match in INLINE_MARKDOWN_LINK.finditer("\n".join(visible_lines)):
        destination = (match.group(1) or match.group(2)).split("#", 1)[0]
        if not destination or "://" in destination or destination.startswith(("mailto:", "#")):
            continue
        candidate = Path(destination.replace("/", os.sep))
        if candidate.is_absolute():
            raise ValueError(f"router contains an absolute local link: {destination}")
        resolved = Path(os.path.abspath(router.parent / candidate))
        if not is_within(root, resolved):
            raise ValueError(f"router link escapes the context root: {destination}")
        targets.add(canonical(resolved))
    return targets


def validate_setup(manifest_path: Path, project_root: Path) -> Report:
    report = Report()
    inputs = Inputs()
    root = Path(os.path.abspath(project_root))

    manifest = load_json(manifest_path, "setup manifest", inputs, report, "manifest_json")
    if manifest is None:
        inputs.unchanged(report)
        return report

    try:
        manifest = require_mapping(manifest, "setup manifest")
        compare(report, "manifest_schema", manifest.get("schema_version"), 3, "manifest schema version is supported")
        project = require_mapping(manifest.get("project"), "project")
        paths = require_mapping(manifest.get("paths"), "paths")
        workspace_contract = require_mapping(manifest.get("workspace"), "workspace")
        providers = require_mapping(manifest.get("providers"), "providers")
        context = require_mapping(manifest.get("context"), "context")
        launcher_contract = require_mapping(manifest.get("launcher"), "launcher")
        path_policy = require_mapping(manifest.get("path_policy"), "path_policy")
        preservation = require_mapping(manifest.get("preservation"), "preservation")
    except ValueError as exc:
        report.fail("manifest_shape", str(exc))
        inputs.unchanged(report)
        return report
    report.pass_("manifest_shape", "required manifest sections are objects")

    try:
        context_root = str(project.get("context_root", ""))
        source = str(project.get("source_root", ""))
        name_prefix = project.get("name_prefix")
        auxiliaries = require_list(project.get("auxiliary_roots", []), "project.auxiliary_roots")
        if not context_root or not source:
            raise ValueError("project.context_root and project.source_root must be nonempty")
        if not isinstance(name_prefix, str):
            raise ValueError("project.name_prefix must be a string; use an empty string for the public default")
        context_canonical = canonical(context_root)
        source_canonical = canonical(source)
        auxiliary_values = [str(value) for value in auxiliaries]
        auxiliary_canonical = canonical_list(auxiliary_values)
        duplicated_auxiliaries = sorted({path for path in auxiliary_canonical if auxiliary_canonical.count(path) > 1})
        role_collisions = sorted(
            path for path in set(auxiliary_canonical) if path in {context_canonical, source_canonical}
        )
        if duplicated_auxiliaries or role_collisions:
            raise ValueError(
                "auxiliary roots must be unique and distinct from context/source roots; "
                f"duplicates={duplicated_auxiliaries}, role_collisions={role_collisions}"
            )
        expected_roots = [context_root]
        if source_canonical != context_canonical:
            expected_roots.append(source)
        expected_roots.extend(auxiliary_values)
        declared_order = require_list(workspace_contract.get("folder_order", []), "workspace.folder_order")
    except ValueError as exc:
        root_error = str(exc)
        if "auxiliary roots" in root_error:
            root_code = "manifest_auxiliary_roots"
        elif "name_prefix" in root_error:
            root_code = "project_name_prefix"
        else:
            root_code = "manifest_root_fields"
        report.fail(root_code, root_error)
        inputs.unchanged(report)
        return report
    compare(report, "project_root", canonical(root), canonical(context_root), "CLI project root matches manifest context root")
    report.pass_(
        "project_name_prefix",
        "project name prefix is explicitly declared" if name_prefix else "project uses the prefix-free public default",
    )
    compare(
        report,
        "manifest_root_order",
        canonical_list(declared_order),
        canonical_list(expected_roots),
        "manifest orders context, distinct source and approved auxiliaries exactly",
    )
    missing_topology_roots = [value for value in expected_roots if not Path(os.path.abspath(value)).is_dir()]
    if missing_topology_roots:
        report.fail("topology_roots_exist", f"declared topology roots are missing or not directories: {missing_topology_roots}")
    else:
        report.pass_("topology_roots_exist", "all declared topology roots exist as directories")
    missing_provider_contracts = [name for name in ("claude", "codex") if not isinstance(providers.get(name), dict)]
    if missing_provider_contracts:
        report.fail("manifest_provider_contracts", f"explicit provider matrices are required: {missing_provider_contracts}")
    else:
        report.pass_("manifest_provider_contracts", "Claude and Codex provider matrices are explicit objects")

    try:
        workspace_path = project_path(root, str(paths["workspace"]))
        claude_path = project_path(root, str(paths["claude_settings"]))
        codex_path = project_path(root, str(paths["codex_config"]))
        launcher_enabled = launcher_contract.get("enabled")
        if not isinstance(launcher_enabled, bool):
            raise ValueError("launcher.enabled must be a boolean")
        launcher_path: Path | None = None
        if launcher_enabled:
            launcher_value = paths.get("launcher")
            if not isinstance(launcher_value, str) or not launcher_value.strip():
                raise ValueError("enabled launcher requires paths.launcher")
            launcher_path = project_path(root, launcher_value)
    except (KeyError, ValueError) as exc:
        report.fail("launcher_path" if "launcher" in str(exc) else "configured_paths", str(exc))
        inputs.unchanged(report)
        return report
    report.pass_("configured_paths", "configuration paths and any enabled launcher remain inside the context root")

    workspace = load_json(workspace_path, "VS Code workspace", inputs, report, "workspace_json")
    claude = load_json(claude_path, "Claude settings", inputs, report, "claude_json")
    codex = load_toml(codex_path, inputs, report)

    workspace_raw = ""
    claude_raw = ""
    codex_raw = ""
    launcher_raw = ""
    workspace_folders: dict[str, Path] = {}
    if workspace is not None:
        try:
            workspace_raw = inputs.read(workspace_path).decode("utf-8")
            workspace = require_mapping(workspace, "workspace file")
            folder_entries = require_list(workspace.get("folders"), "workspace.folders")
            actual_roots: list[str] = []
            actual_names: list[str] = []
            seen_folder_names: set[str] = set()
            for entry_value in folder_entries:
                entry = require_mapping(entry_value, "workspace folder")
                name = str(entry["name"])
                folded_name = name.strip().casefold()
                if not folded_name or folded_name in seen_folder_names:
                    raise ValueError(f"workspace folder names must be nonempty and unique: {name!r}")
                seen_folder_names.add(folded_name)
                resolved = Path(os.path.abspath(workspace_path.parent / str(entry["path"])))
                actual_names.append(name)
                actual_roots.append(canonical(resolved))
                workspace_folders[name] = resolved
            expected_canonical = canonical_list(declared_order)
            compare(report, "workspace_root_order", actual_roots, expected_canonical, "resolved workspace roots match exact manifest order")
            authorized = canonical_set(expected_roots)
            unexpected = [value for value in actual_roots if value not in authorized]
            if unexpected:
                report.fail("workspace_authorized_roots", f"workspace contains unauthorized roots: {unexpected}")
            elif len(actual_roots) != len(expected_roots):
                report.fail("workspace_authorized_roots", "workspace root count differs from the explicit approved root count")
            else:
                report.pass_("workspace_authorized_roots", "workspace contains only context, distinct source and approved auxiliary roots")
            compare(
                report,
                "workspace_folder_names",
                actual_names,
                require_list(workspace_contract.get("folder_names", []), "workspace.folder_names"),
                "workspace folder names match the manifest",
            )
            settings = require_mapping(workspace.get("settings"), "workspace.settings")
            compare(report, "window_title", settings.get("window.title"), workspace_contract.get("window_title"), "window title has the manifest identity")
            terminal_value = settings.get("terminal.integrated.cwd")
            compare(report, "terminal_cwd_value", terminal_value, workspace_contract.get("terminal_cwd"), "terminal cwd setting matches the manifest")
            file_eol_expected = workspace_contract.get("file_eol")
            if file_eol_expected != "\n":
                report.fail("workspace_file_eol_policy", "workspace.file_eol must select LF as the portable default")
            else:
                report.pass_("workspace_file_eol_policy", "manifest selects LF as the portable text default")
                compare(
                    report,
                    "workspace_file_eol",
                    settings.get("files.eol"),
                    file_eol_expected,
                    "VS Code files.eol matches the manifest LF default",
                )
            try:
                terminal_resolved = expand_workspace_value(str(terminal_value), workspace_folders)
                terminal_expected = workspace_contract.get("terminal_cwd_resolved")
                if not isinstance(terminal_expected, str) or not terminal_expected:
                    raise ValueError("workspace.terminal_cwd_resolved must be a nonempty path")
                compare(report, "terminal_cwd_resolved", canonical(terminal_resolved), canonical(terminal_expected), "terminal cwd resolves to the explicit manifest path")
            except ValueError as exc:
                report.fail("terminal_cwd_resolved", str(exc))
            python_expected = workspace_contract.get("python_interpreter")
            if python_expected is None:
                report.skip("python_path_resolved", "manifest does not select a Python interpreter")
                report.skip("python_interpreter_exists", "manifest does not select a Python interpreter")
            elif not isinstance(python_expected, str) or not python_expected:
                report.fail("python_path_resolved", "workspace.python_interpreter must be null or a nonempty path")
            else:
                python_value = str(settings.get("python.defaultInterpreterPath", ""))
                try:
                    python_resolved = expand_workspace_value(python_value, workspace_folders)
                    compare(
                        report,
                        "python_path_resolved",
                        canonical(python_resolved),
                        canonical(python_expected),
                        "workspace Python path resolves to the explicit interpreter",
                    )
                    if Path(python_resolved).is_file():
                        report.pass_("python_interpreter_exists", f"configured interpreter exists: {python_resolved}")
                    else:
                        report.fail("python_interpreter_exists", f"configured interpreter is missing: {python_resolved}")
                except ValueError as exc:
                    report.fail("python_path_resolved", str(exc))
            activation_expected = workspace_contract.get("automatic_python_activation")
            if activation_expected is None:
                report.skip("python_auto_activation", "manifest does not select an automatic Python activation policy")
            elif not isinstance(activation_expected, bool):
                report.fail("python_auto_activation", "workspace.automatic_python_activation must be null or a boolean")
            else:
                compare(
                    report,
                    "python_auto_activation",
                    settings.get("python.terminal.activateEnvironment"),
                    activation_expected,
                    "automatic Python activation matches the manifest",
                )
            exclusions = require_mapping(settings.get("search.exclude", {}), "settings.search.exclude")
            missing_exclusions = [
                item
                for item in require_list(workspace_contract.get("required_search_exclusions", []), "workspace.required_search_exclusions")
                if exclusions.get(item) is not True
            ]
            if missing_exclusions:
                report.fail("search_exclusions", f"required search exclusions are absent or false: {missing_exclusions}")
            else:
                report.pass_("search_exclusions", "all project-specific search exclusions are enabled")
        except (KeyError, TypeError, ValueError) as exc:
            report.fail("workspace_folder_names" if "workspace folder names" in str(exc) else "workspace_contract", str(exc))

    claude_contract = providers.get("claude")
    if claude is not None and isinstance(claude_contract, dict):
        try:
            claude_raw = inputs.read(claude_path).decode("utf-8")
            permissions = require_mapping(claude.get("permissions"), "Claude permissions")
            env = require_mapping(claude.get("env"), "Claude env")
            actual_roots = require_list(permissions.get("additionalDirectories"), "Claude additionalDirectories")
            expected_access = require_list(claude_contract.get("access_roots"), "Claude manifest access_roots")
            compare_unique_path_set(report, "claude_access", actual_roots, expected_access, "Claude access set matches its explicit provider matrix")
            actual_temp = env.get("CLAUDE_CODE_TMPDIR")
            expected_temp = claude_contract.get("temp_dir")
            if not isinstance(actual_temp, str) or not actual_temp.strip() or not isinstance(expected_temp, str) or not expected_temp.strip():
                raise ValueError("Claude configured and manifest temp directories must be nonempty strings")
            compare(report, "claude_temp", canonical(actual_temp), canonical(expected_temp), "Claude temp directory matches the manifest")
            compare(report, "claude_write_block", permissions.get("blockWritesOutsideWorkingDirectories"), claude_contract.get("block_writes_outside"), "Claude outside-write blocking matches the manifest")
            actual_denials = require_list(permissions.get("deny"), "Claude deny")
            required_denials = require_list(claude_contract.get("required_denials"), "Claude required_denials")
            missing_denials = [item for item in required_denials if item not in actual_denials]
            if missing_denials:
                report.fail("claude_git_denials", f"required Claude denials are missing: {missing_denials}")
            else:
                report.pass_("claude_git_denials", "all manifest-required Claude Git and metadata denials are present")
        except (TypeError, ValueError) as exc:
            report.fail("claude_contract", str(exc))
    elif claude is not None:
        report.fail("claude_contract", "providers.claude must be an object")

    codex_contract = providers.get("codex")
    if codex is not None and isinstance(codex_contract, dict):
        try:
            codex_raw = inputs.read(codex_path).decode("utf-8")
            sandbox = require_mapping(codex.get("sandbox_workspace_write"), "Codex sandbox_workspace_write")
            shell_set = require_mapping(
                require_mapping(codex.get("shell_environment_policy"), "Codex shell_environment_policy").get("set"),
                "Codex shell_environment_policy.set",
            )
            actual_roots = require_list(sandbox.get("writable_roots"), "Codex writable_roots")
            expected_access = require_list(codex_contract.get("access_roots"), "Codex manifest access_roots")
            compare_unique_path_set(report, "codex_access", actual_roots, expected_access, "Codex writable-root set matches its explicit provider matrix")
            exclude_tmpdir_expected = codex_contract.get("exclude_tmpdir_env_var")
            if not isinstance(exclude_tmpdir_expected, bool):
                raise ValueError("Codex manifest exclude_tmpdir_env_var must be a boolean")
            compare(
                report,
                "codex_exclude_tmpdir_env",
                sandbox.get("exclude_tmpdir_env_var"),
                exclude_tmpdir_expected,
                "Codex temporary-directory inheritance policy matches the manifest",
            )
            configured_temp = shell_set.get("TEMP")
            configured_tmp = shell_set.get("TMP")
            manifest_temp = codex_contract.get("temp_dir")
            if (
                not isinstance(configured_temp, str)
                or not configured_temp.strip()
                or not isinstance(configured_tmp, str)
                or not configured_tmp.strip()
                or not isinstance(manifest_temp, str)
                or not manifest_temp.strip()
            ):
                raise ValueError("Codex configured TEMP/TMP and manifest temp directory must be nonempty strings")
            expected_temp = canonical(manifest_temp)
            compare(report, "codex_temp", canonical(configured_temp), expected_temp, "Codex TEMP matches the manifest")
            compare(report, "codex_tmp", canonical(configured_tmp), expected_temp, "Codex TMP matches the manifest")
            network_expected = codex_contract.get("network_access")
            if network_expected is None:
                report.skip("codex_network", "manifest leaves Codex network access as a project decision")
            else:
                compare(report, "codex_network", sandbox.get("network_access"), network_expected, "Codex network decision matches the manifest")
        except (TypeError, ValueError) as exc:
            report.fail("codex_contract", str(exc))
    elif codex is not None:
        report.fail("codex_contract", "providers.codex must be an object")

    try:
        required_folders = require_list(manifest.get("required_folders"), "required_folders")
        missing_folders = [item for item in required_folders if not project_path(root, str(item)).is_dir()]
        if missing_folders:
            report.fail("required_folders", f"required folders are missing: {missing_folders}")
        else:
            report.pass_("required_folders", "all manifest-required folders exist")
    except ValueError as exc:
        report.fail("required_folders", str(exc))

    try:
        router_ownership = context.get("router_ownership")
        if router_ownership not in {"generated", "hand-maintained"}:
            raise ValueError("context.router_ownership must be 'generated' or 'hand-maintained'")
        report.pass_("router_ownership", f"router ownership mode is explicit: {router_ownership}")
        router_source_value = context.get("router_source")
        if router_ownership == "generated":
            if not isinstance(router_source_value, str) or not router_source_value.strip():
                raise ValueError("generated router ownership requires a nonempty context.router_source")
            router_source = project_path(root, router_source_value)
            if router_source.is_file():
                report.pass_("router_source", f"generated-router authority/source exists: {router_source}")
            else:
                report.fail("router_source", f"generated-router authority/source is missing: {router_source}")
        elif router_source_value is None:
            report.skip("router_source", "hand-maintained routers have no generated source")
        else:
            raise ValueError("hand-maintained router ownership requires context.router_source to be null")
        required_documents = require_list(context.get("required_documents"), "context.required_documents")
        missing_documents = [item for item in required_documents if not project_path(root, str(item)).is_file()]
        if missing_documents:
            report.fail("required_context_documents", f"required context documents are missing: {missing_documents}")
        else:
            report.pass_("required_context_documents", "all required context documents exist")
        router_targets = require_mapping(context.get("router_targets"), "context.router_targets")
        missing_targets: dict[str, list[str]] = {}
        for router_name, target_values in router_targets.items():
            router = project_path(root, str(router_name))
            if not router.is_file():
                missing_targets[str(router_name)] = ["<router missing>"]
                continue
            router_text = inputs.read(router).decode("utf-8")
            linked_targets = markdown_link_targets(router_text, router, root)
            absent: list[str] = []
            for target in require_list(target_values, f"router targets for {router_name}"):
                target_name = str(target)
                target_path = project_path(root, target_name)
                if not target_path.is_file():
                    absent.append(f"{target_name} (target file missing)")
                elif canonical(target_path) not in linked_targets:
                    absent.append(f"{target_name} (actual Markdown link missing)")
            if absent:
                missing_targets[str(router_name)] = absent
        if missing_targets:
            report.fail("router_targets", f"router targets are missing: {missing_targets}")
        else:
            report.pass_("router_targets", "all routers contain actual links to their manifest-required targets")
    except (OSError, UnicodeDecodeError, ValueError) as exc:
        report.fail("context_contract", str(exc))

    launcher_bytes = b""
    launcher_raw = ""
    if launcher_enabled:
        assert launcher_path is not None
        if launcher_path.suffix.casefold() == ".cmd":
            report.pass_("launcher_extension", "enabled Windows launcher uses the .cmd suffix")
        else:
            report.fail("launcher_extension", f"enabled Windows launcher must use the .cmd suffix: {launcher_path}")
        if same_path(launcher_path.parent, root):
            report.pass_("launcher_root", "launcher is stored directly in the context root")
        else:
            report.fail("launcher_root", f"launcher must be stored directly in the context root: {launcher_path}")
        workspace_relative_value = launcher_contract.get("workspace_relative")
        try:
            if not isinstance(workspace_relative_value, str) or not workspace_relative_value.strip():
                raise ValueError("launcher.workspace_relative must be a nonempty relative path")
            launcher_workspace_target = project_path(launcher_path.parent, workspace_relative_value)
            compare(
                report,
                "launcher_workspace_target",
                canonical(launcher_workspace_target),
                canonical(workspace_path),
                "launcher workspace target matches paths.workspace",
            )
        except ValueError as exc:
            report.fail("launcher_workspace_target", str(exc))
        temp_relative_value = launcher_contract.get("temp_relative")
        try:
            if not isinstance(temp_relative_value, str) or not temp_relative_value.strip():
                raise ValueError("launcher.temp_relative must be a nonempty relative path")
            launcher_temp_target = project_path(launcher_path.parent, temp_relative_value)
            if not isinstance(claude_contract, dict):
                raise ValueError("launcher temp alignment requires providers.claude")
            claude_temp_target = claude_contract.get("temp_dir")
            if not isinstance(claude_temp_target, str) or not claude_temp_target.strip():
                raise ValueError("providers.claude.temp_dir must be a nonempty path")
            compare(
                report,
                "launcher_temp_target",
                canonical(launcher_temp_target),
                canonical(claude_temp_target),
                "launcher temp target matches providers.claude.temp_dir",
            )
        except ValueError as exc:
            report.fail("launcher_temp_target", str(exc))
        if launcher_path.is_file():
            try:
                launcher_bytes = inputs.read(launcher_path)
                launcher_raw = launcher_bytes.decode("utf-8")
                report.pass_("launcher_utf8", "launcher is UTF-8 decodable")
            except (OSError, UnicodeDecodeError) as exc:
                report.fail("launcher_utf8", str(exc))
        else:
            report.fail("launcher_exists", f"launcher is missing: {launcher_path}")
        if launcher_bytes:
            if launcher_contract.get("require_bomless") is not True:
                report.fail("launcher_bom_policy", "manifest must require a BOM-less launcher")
            elif launcher_bytes.startswith(b"\xef\xbb\xbf"):
                report.fail("launcher_bom", "launcher has a UTF-8 byte order mark")
            else:
                report.pass_("launcher_bom", "launcher is BOM-less")
            lf_ok = b"\n" in launcher_bytes and b"\r" not in launcher_bytes
            if launcher_contract.get("line_endings") != "lf":
                report.fail("launcher_lf_policy", "manifest must require LF launcher line endings")
            elif not lf_ok:
                report.fail("launcher_lf", "launcher must use LF line endings without carriage returns")
            else:
                report.pass_("launcher_lf", "launcher uses LF without carriage returns")
            relative_workspace = str(workspace_relative_value).replace("/", "\\")
            relative_temp = str(temp_relative_value).replace("/", "\\")
            launcher_lines = [line.strip() for line in launcher_raw.splitlines() if line.strip()]
            executable_lines = [
                line
                for line in launcher_lines
                if not line.casefold().startswith(("rem ", "::", "echo "))
            ]
            folded_lines = {line.casefold() for line in executable_lines}
            required_lines = [
                "setlocal EnableExtensions DisableDelayedExpansion",
                f'set "WS=%~dp0{relative_workspace}"',
                'cd /d "%~dp0"',
                f'set "CLAUDE_CODE_TMPDIR=%~dp0{relative_temp}"',
                'if not exist "%CLAUDE_CODE_TMPDIR%\\" mkdir "%CLAUDE_CODE_TMPDIR%"',
                'set "VSCODE_EXE="',
                'start "" "%VSCODE_EXE%" "%WS%"',
                'start "" "%WS%"',
                "endlocal",
            ]
            absent = [line for line in required_lines if line.casefold() not in folded_lines]
            expected_prefix = [
                "@echo off",
                "setlocal EnableExtensions DisableDelayedExpansion",
                f'set "WS=%~dp0{relative_workspace}"',
                'cd /d "%~dp0"',
                f'set "CLAUDE_CODE_TMPDIR=%~dp0{relative_temp}"',
                'if not exist "%CLAUDE_CODE_TMPDIR%\\" mkdir "%CLAUDE_CODE_TMPDIR%"',
                'set "VSCODE_EXE="',
            ]
            expected_resolver = (
                'for /f "delims=" %%I in (\'where code.cmd 2^>nul\') '
                'do if exist "%%~dpI..\\Code.exe" set "VSCODE_EXE=%%~dpI..\\Code.exe"'
            )
            folded_executable = [line.casefold() for line in executable_lines]
            if folded_executable[: len(expected_prefix)] != [line.casefold() for line in expected_prefix]:
                absent.append("exact launcher command prefix")
            if launcher_contract.get("resolve_code_from_code_cmd") is not True:
                absent.append("manifest resolve_code_from_code_cmd=true")
            elif (
                len(executable_lines) <= len(expected_prefix)
                or executable_lines[len(expected_prefix)].casefold() != expected_resolver.casefold()
            ):
                absent.append("exact executable for /f resolver assigning VSCODE_EXE from where code.cmd")
            expected_tail = [
                "if defined VSCODE_EXE (",
                'start "" "%VSCODE_EXE%" "%WS%"',
                ") else (",
                'start "" "%WS%"',
                ")",
                "endlocal",
            ]
            if folded_executable[-len(expected_tail) :] != [line.casefold() for line in expected_tail]:
                absent.append("exact conditional Code.exe/fallback control-flow tail")
            expected_line_count = len(expected_prefix) + 1 + len(expected_tail)
            if len(executable_lines) != expected_line_count:
                absent.append(f"exactly {expected_line_count} executable launcher lines; found {len(executable_lines)}")
            start_count = sum(line.casefold().startswith("start ") for line in executable_lines)
            if start_count != 2:
                absent.append(f"exactly two conditional start commands; found {start_count}")
            if absent:
                report.fail("launcher_behavior", f"launcher is missing required executable lines: {absent}")
            else:
                report.pass_("launcher_behavior", "launcher contains all manifest-required executable lines")
        if launcher_contract.get("launch_during_validation") is False:
            report.skip("gui_launch", "manifest forbids GUI launch during validation")
        else:
            report.fail("gui_launch_policy", "validator refuses to launch a GUI; set launch_during_validation to false")
    else:
        report.skip("launcher_extension", "launcher validation is disabled by the manifest")
        report.skip("launcher_root", "launcher validation is disabled by the manifest")
        report.skip("launcher_workspace_target", "launcher validation is disabled by the manifest")
        report.skip("launcher_temp_target", "launcher validation is disabled by the manifest")
        report.skip("launcher_utf8", "launcher validation is disabled by the manifest")
        report.skip("launcher_bom", "launcher validation is disabled by the manifest")
        report.skip("launcher_lf", "launcher validation is disabled by the manifest")
        report.skip("launcher_behavior", "launcher validation is disabled by the manifest")
        report.skip("gui_launch", "no launcher is configured")
    report.not_run("terminal_runtime", "provider terminal and Developer PowerShell runtime were not launched")

    approved_mutations: dict[str, str] = {}
    try:
        approval_entries = require_list(
            path_policy.get("approved_existing_process_mutations", []),
            "approved existing process mutations",
        )
        for value in approval_entries:
            entry = require_mapping(value, "approved process mutation")
            relative = str(entry.get("file", "")).replace("\\", "/").casefold()
            expected_hash = str(entry.get("sha256", "")).casefold()
            if not relative or not re.fullmatch(r"[a-f0-9]{64}", expected_hash):
                raise ValueError("approved process mutation requires file and lowercase SHA-256")
            if relative in approved_mutations:
                raise ValueError(f"duplicate approved process mutation file: {relative}")
            project_path(root, relative)
            approved_mutations[relative] = expected_hash
        report.pass_("process_path_approval_shape", "process PATH exceptions are file-and-SHA-256 bound")
    except (TypeError, ValueError) as exc:
        report.fail("process_path_approval_shape", str(exc))
    if path_policy.get("allow_persistent_mutation") is not False:
        report.fail("persistent_path_policy", "broad persistent PATH authorization is unsupported; exact project authority is required")
    else:
        report.pass_("persistent_path_policy", "manifest forbids persistent PATH mutation")
    path_sources = {
        str(paths.get("workspace", "")).replace("\\", "/"): workspace_raw,
        str(paths.get("claude_settings", "")).replace("\\", "/"): claude_raw,
        str(paths.get("codex_config", "")).replace("\\", "/"): codex_raw,
    }
    if launcher_enabled:
        path_sources[str(paths.get("launcher", "")).replace("\\", "/")] = launcher_raw
    configured_path_keys: dict[str, list[str]] = {relative: [] for relative in path_sources}
    if isinstance(workspace, dict) and isinstance(workspace.get("settings"), dict):
        workspace_settings = workspace["settings"]
        workspace_path_settings: list[str] = []
        for setting_name, setting_value in workspace_settings.items():
            folded_name = str(setting_name).casefold()
            if folded_name in {
                "terminal.integrated.env.windows",
                "terminal.integrated.env.linux",
                "terminal.integrated.env.osx",
            } and isinstance(setting_value, dict):
                if any(str(key).casefold() == "path" for key in setting_value):
                    workspace_path_settings.append(str(setting_name))
            if folded_name.startswith("terminal.integrated.profiles.") and isinstance(setting_value, dict):
                for profile_name, profile_value in setting_value.items():
                    profile_env = profile_value.get("env") if isinstance(profile_value, dict) else None
                    if isinstance(profile_env, dict) and any(str(key).casefold() == "path" for key in profile_env):
                        workspace_path_settings.append(f"{setting_name}.{profile_name}.env")
            if folded_name.startswith("terminal.integrated.automationprofile.") and isinstance(setting_value, dict):
                automation_env = setting_value.get("env")
                if isinstance(automation_env, dict) and any(str(key).casefold() == "path" for key in automation_env):
                    workspace_path_settings.append(f"{setting_name}.env")
        if workspace_path_settings:
            configured_path_keys[str(paths.get("workspace", "")).replace("\\", "/")].extend(
                f"VS Code {setting}.PATH" for setting in workspace_path_settings
            )
    if isinstance(claude, dict) and isinstance(claude.get("env"), dict):
        if any(str(key).casefold() == "path" for key in claude["env"]):
            configured_path_keys[str(paths.get("claude_settings", "")).replace("\\", "/")].append("Claude env.PATH")
    if isinstance(codex, dict):
        shell_policy = codex.get("shell_environment_policy")
        shell_set = shell_policy.get("set") if isinstance(shell_policy, dict) else None
        if isinstance(shell_set, dict) and any(str(key).casefold() == "path" for key in shell_set):
            configured_path_keys[str(paths.get("codex_config", "")).replace("\\", "/")].append("Codex shell_environment_policy.set.PATH")
    used_approvals: set[str] = set()
    for relative, text in path_sources.items():
        persistent = matching_patterns(text, PERSISTENT_PATH_PATTERNS)
        if persistent:
            report.fail("persistent_path_mutation", f"persistent PATH mutation detected in {relative}: {persistent}")
        else:
            report.pass_(f"persistent_path_absent_{Path(relative).name}", f"no persistent PATH mutation detected in {relative}")
        process = matching_patterns(text, PROCESS_PATH_PATTERNS) + configured_path_keys.get(relative, [])
        normalized_relative = relative.casefold()
        if process and launcher_path is not None and same_path(project_path(root, relative), launcher_path):
            report.fail("launcher_path_mutation", f"launcher mutates process PATH: {process}")
        elif process and normalized_relative in approved_mutations:
            actual_hash = sha256(inputs.read(project_path(root, relative)))
            if actual_hash == approved_mutations[normalized_relative]:
                used_approvals.add(normalized_relative)
                report.pass_("approved_existing_process_path", f"preserved process PATH mutation matches the reviewed SHA-256 for {relative}")
            else:
                report.fail(
                    "changed_process_path_approval",
                    f"process PATH mutation file changed since review: {relative}; expected={approved_mutations[normalized_relative]}, actual={actual_hash}",
                )
        elif process:
            report.fail("unapproved_process_path_mutation", f"unapproved process PATH mutation detected in {relative}: {process}")
        else:
            report.pass_(f"process_path_absent_{Path(relative).name}", f"no process PATH mutation detected in {relative}")
    stale_approvals = sorted(set(approved_mutations) - used_approvals)
    if stale_approvals:
        report.fail("stale_process_path_approval", f"manifested process PATH exceptions do not match a current mutation: {stale_approvals}")

    try:
        compare(report, "preserve_unlisted_content", preservation.get("preserve_unlisted_content"), True, "manifest requires preservation of unlisted content")
        compare(report, "idempotence_expectation", preservation.get("setup_is_idempotent"), True, "manifest requires idempotent setup")
        must_exist = require_list(preservation.get("must_exist", []), "preservation.must_exist")
        missing_preserved = [item for item in must_exist if not project_path(root, str(item)).exists()]
        if missing_preserved:
            report.fail("preserved_paths", f"manifest-preserved paths are missing: {missing_preserved}")
        else:
            report.pass_("preserved_paths", "all manifest-preserved paths remain present")
    except ValueError as exc:
        report.fail("preservation_contract", str(exc))
    report.not_run("setup_idempotence", "no setup generator was executed twice; only its declared expectation was checked")

    inputs.unchanged(report)
    return report


def emit(report: Report, prefix: str = "") -> None:
    for check in report.checks:
        print(f"{prefix}{check.status.upper():8} {check.code}: {check.detail}")
    print(f"{prefix}SUMMARY passed={report.count('passed')} failed={report.count('failed')} skipped={report.count('skipped')} not-run={report.count('not-run')}")


DENIALS = [
    "Edit(/.git/**)",
    "Bash(git commit *)",
    "Bash(git push *)",
    "Bash(git reset *)",
    "Bash(git add *)",
    "PowerShell(git commit *)",
    "PowerShell(git push *)",
    "PowerShell(git reset *)",
    "PowerShell(git add *)",
]


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8", newline="\n")


def build_fixture(container: Path, mutation: str | None) -> tuple[Path, Path]:
    project = container / "project"
    source = project if mutation == "single-root" else container / "source"
    auxiliary = container / "auxiliary"
    rogue = container / "rogue"
    claude_temp = project / ".agent-temp" / "claude"
    codex_temp = project / ".agent-temp" / "codex"
    fixture_folders = [
        project / ".claude",
        project / ".codex",
        project / "docs",
        project / "_workspace",
        project / "keep",
        claude_temp,
        codex_temp,
    ]
    if source != project and mutation != "missing-source-root":
        fixture_folders.append(source)
    if mutation != "missing-auxiliary-root":
        fixture_folders.append(auxiliary)
    for folder in fixture_folders:
        folder.mkdir(parents=True, exist_ok=True)

    folder_entries = [{"name": "Fixture", "path": ".."}]
    if source != project:
        folder_entries.append({"name": "source", "path": "../../source"})
    folder_entries.append({"name": "auxiliary", "path": "../../auxiliary"})
    if mutation == "wrong-root-order":
        folder_entries[0], folder_entries[1] = folder_entries[1], folder_entries[0]
    if mutation == "unauthorized-root":
        rogue.mkdir(parents=True, exist_ok=True)
        folder_entries.append({"name": "rogue", "path": "../../rogue"})
    if mutation == "duplicate-workspace-name":
        folder_entries[-1]["name"] = "fixture"
    workspace = {
        "folders": folder_entries,
        "settings": {
            "window.title": "fixture${separator}${appName}",
            "terminal.integrated.cwd": "${workspaceFolder:Fixture}",
            "files.eol": "\n",
            "python.defaultInterpreterPath": sys.executable,
            "python.terminal.activateEnvironment": False,
            "search.exclude": {"**/scratch": True},
        },
    }
    if mutation == "workspace-crlf-eol":
        workspace["settings"]["files.eol"] = "\r\n"
    if mutation == "workspace-path-append":
        workspace["settings"]["terminal.integrated.profiles.windows"] = {
            "Bad": {"path": "powershell.exe", "args": ["-Command", "$env:Path += ';C:\\bad'"]}
        }
    if mutation == "workspace-terminal-path-key":
        workspace["settings"]["terminal.integrated.env.windows"] = {"PATH": "C:\\bad"}
    if mutation == "workspace-profile-path-key":
        workspace["settings"]["terminal.integrated.profiles.windows"] = {
            "Bad": {"path": "powershell.exe", "env": {"PATH": "C:\\bad"}}
        }
    if mutation == "workspace-automation-path-key":
        workspace["settings"]["terminal.integrated.automationProfile.windows"] = {
            "path": "powershell.exe",
            "env": {"PATH": "C:\\bad"},
        }
    if mutation == "workspace-cmd-path-command":
        workspace["settings"]["terminal.integrated.profiles.windows"] = {
            "Bad": {"path": "cmd.exe", "args": ["/k", "path C:\\bad"]}
        }
    if mutation == "workspace-braced-env-path":
        workspace["settings"]["terminal.integrated.profiles.windows"] = {
            "Bad": {"path": "powershell.exe", "args": ["-Command", "${env:Path} = 'C:\\bad'"]}
        }
    if mutation == "workspace-set-item-env-path":
        workspace["settings"]["terminal.integrated.profiles.windows"] = {
            "Bad": {"path": "powershell.exe", "args": ["-Command", "Set-Item Env:Path 'C:\\bad'"]}
        }
    if mutation == "typed-persistent-path":
        workspace["settings"]["terminal.integrated.profiles.windows"] = {
            "Bad": {
                "path": "powershell.exe",
                "args": [
                    "-Command",
                    "[Environment]::SetEnvironmentVariable('Path','C:\\bad',[EnvironmentVariableTarget]::User)",
                ],
            }
        }
    workspace_relative = "_workspace/project.code-workspace"
    workspace_path = project / "_workspace" / "project.code-workspace"
    if mutation == "invalid-json":
        workspace_path.write_text('{"folders": [}\n', encoding="utf-8", newline="\n")
    else:
        write_json(workspace_path, workspace)

    claude_env = {"CLAUDE_CODE_TMPDIR": str(claude_temp)}
    if mutation == "provider-path-key":
        claude_env["PATH"] = "C:\\bad"
    provider_roots = list(dict.fromkeys((project, source, auxiliary)))
    configured_provider_roots = (
        [*provider_roots, provider_roots[0]] if mutation == "duplicate-provider-root" else provider_roots
    )
    write_json(
        project / ".claude" / "settings.json",
        {
            "env": claude_env,
            "permissions": {
                "additionalDirectories": [str(path) for path in configured_provider_roots],
                "blockWritesOutsideWorkingDirectories": True,
                "deny": [] if mutation == "single-root" else DENIALS,
            },
        },
    )
    quoted_roots = ", ".join(json.dumps(str(item)) for item in configured_provider_roots)
    exclude_tmpdir = "false" if mutation == "codex-tmpdir-inheritance" else "true"
    codex_text = (
        "[sandbox_workspace_write]\n"
        f"exclude_tmpdir_env_var = {exclude_tmpdir}\n"
        "network_access = true\n"
        f"writable_roots = [{quoted_roots}]\n\n"
        "[shell_environment_policy.set]\n"
        f"TEMP = {json.dumps(str(codex_temp))}\n"
        f"TMP = {json.dumps(str(codex_temp))}\n"
    )
    (project / ".codex" / "config.toml").write_text(codex_text, encoding="utf-8", newline="\n")

    launcher_enabled = mutation != "launcher-disabled"
    launcher_filename = "open_fixture_vscode.txt" if mutation == "launcher-wrong-extension" else "open_fixture_vscode.cmd"
    if launcher_enabled:
        resolver_line = "for /f \"delims=\" %%I in ('where code.cmd 2^>nul') do if exist \"%%~dpI..\\Code.exe\" set \"VSCODE_EXE=%%~dpI..\\Code.exe\""
        if mutation == "inert-launcher-behavior":
            resolver_line = "rem where code.cmd ..\\Code.exe"
        if mutation == "launcher-resolver-injection":
            resolver_line += " & echo injected"
        launcher_lines = [
            "@echo off",
            "setlocal EnableExtensions DisableDelayedExpansion",
            'set "WS=%~dp0_workspace\\project.code-workspace"',
            'cd /d "%~dp0"',
            'set "CLAUDE_CODE_TMPDIR=%~dp0.agent-temp\\claude"',
            'if not exist "%CLAUDE_CODE_TMPDIR%\\" mkdir "%CLAUDE_CODE_TMPDIR%"',
            'set "VSCODE_EXE="',
            resolver_line,
            "if defined VSCODE_EXE (",
        ]
        if mutation == "launcher-path-mutation":
            launcher_lines.append('set "PATH=C:\\unapproved"')
        if mutation == "launcher-cmd-path-mutation":
            launcher_lines.append("path C:\\unapproved")
        if mutation == "inert-launcher-behavior":
            launcher_lines.append('start "" "C:\\Windows\\notepad.exe"')
        else:
            launcher_lines.append('start "" "%VSCODE_EXE%" "%WS%"')
        launcher_lines.extend((") else (", 'start "" "%WS%"', ")"))
        if mutation == "launcher-double-start":
            launcher_lines.append('start "" "%WS%"')
        launcher_lines.append("endlocal")
        newline = "\r\n" if mutation == "launcher-crlf" else "\n"
        (project / launcher_filename).write_bytes((newline.join(launcher_lines) + newline).encode("utf-8"))

    context_documents = (
        "PROJECT_BRIEF.md",
        "FEATURES.md",
        "CONTEXT_MANAGEMENT_FEATURE.md",
        "docs/DECISIONS.md",
        "CHECKPOINT.md",
    )
    for relative in context_documents:
        path = project / Path(relative)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"# {Path(relative).stem}\n", encoding="utf-8", newline="\n")
    (project / "docs" / "ROUTER_SOURCE.md").write_text("# Router source\n", encoding="utf-8", newline="\n")
    router_text = "\n".join(f"- [{Path(item).stem}]({item})" for item in context_documents) + "\n"
    if mutation == "router-comment-only":
        router_text = f"<!-- [{Path(context_documents[0]).stem}]({context_documents[0]}) -->\n" + "\n".join(
            f"- [{Path(item).stem}]({item})" for item in context_documents[1:]
        ) + "\n"
    if mutation == "router-fenced-only":
        router_text = f"```markdown\n[{Path(context_documents[0]).stem}]({context_documents[0]})\n```\n" + "\n".join(
            f"- [{Path(item).stem}]({item})" for item in context_documents[1:]
        ) + "\n"
    if mutation == "router-inline-code-only":
        router_text = f"`[{Path(context_documents[0]).stem}]({context_documents[0]})`\n" + "\n".join(
            f"- [{Path(item).stem}]({item})" for item in context_documents[1:]
        ) + "\n"
    if mutation == "router-malformed-link":
        router_text = f"[{Path(context_documents[0]).stem}]({context_documents[0]}\n" + "\n".join(
            f"- [{Path(item).stem}]({item})" for item in context_documents[1:]
        ) + "\n"
    if mutation == "router-nested-fence-length":
        router_text = (
            f"````markdown\n```markdown\n[{Path(context_documents[0]).stem}]({context_documents[0]})\n```\n````\n"
            + "\n".join(f"- [{Path(item).stem}]({item})" for item in context_documents[1:])
            + "\n"
        )
    (project / "AGENTS.md").write_text(router_text, encoding="utf-8", newline="\n")
    (project / "CLAUDE.md").write_text(router_text, encoding="utf-8", newline="\n")

    workspace_roots = [str(project)]
    workspace_names = ["Fixture"]
    if source != project:
        workspace_roots.append(str(source))
        workspace_names.append("source")
    workspace_roots.append(str(auxiliary))
    workspace_names.append("auxiliary")
    fixture_denials = [] if mutation == "single-root" else DENIALS
    manifest = {
        "schema_version": 3,
        "project": {
            "identity": "fixture",
            "name_prefix": "" if mutation != "invalid-name-prefix" else None,
            "context_root": str(project),
            "source_root": str(source),
            "auxiliary_roots": [str(source)] if mutation == "duplicate-auxiliary-root" else [str(auxiliary)],
        },
        "paths": {
            "workspace": workspace_relative,
            "claude_settings": ".claude/settings.json",
            "codex_config": ".codex/config.toml",
            "launcher": None if mutation in {"launcher-disabled", "enabled-launcher-missing-path"} else launcher_filename,
        },
        "workspace": {
            "folder_order": workspace_roots,
            "folder_names": workspace_names,
            "window_title": "fixture${separator}${appName}",
            "terminal_cwd": "${workspaceFolder:Fixture}",
            "terminal_cwd_resolved": str(project),
            "file_eol": "\n",
            "python_interpreter": None if mutation == "launcher-disabled" else sys.executable,
            "automatic_python_activation": None if mutation == "launcher-disabled" else False,
            "required_search_exclusions": ["**/scratch"],
        },
        "providers": {
            "claude": {
                "access_roots": [str(path) for path in provider_roots],
                "temp_dir": str(claude_temp),
                "block_writes_outside": True,
                "required_denials": fixture_denials,
            },
            "codex": {
                "access_roots": [str(path) for path in provider_roots],
                "temp_dir": str(codex_temp),
                "exclude_tmpdir_env_var": True,
                "network_access": True,
            },
        },
        "required_folders": [".claude", ".codex", "docs", "_workspace", ".agent-temp/claude", ".agent-temp/codex"],
        "context": {
            "router_ownership": "hand-maintained" if mutation == "hand-maintained-router" else "generated",
            "router_source": None if mutation == "hand-maintained-router" else "docs/ROUTER_SOURCE.md",
            "required_documents": list(context_documents),
            "router_targets": {"AGENTS.md": list(context_documents), "CLAUDE.md": list(context_documents)},
        },
        "launcher": {
            "enabled": False if mutation == "launcher-disabled" else True,
            "workspace_relative": "_workspace\\project.code-workspace",
            "temp_relative": ".agent-temp\\claude",
            "require_bomless": True,
            "line_endings": "lf",
            "resolve_code_from_code_cmd": True,
            "launch_during_validation": False,
        },
        "path_policy": {
            "allow_persistent_mutation": False,
            "approved_existing_process_mutations": [],
        },
        "preservation": {
            "setup_is_idempotent": True,
            "preserve_unlisted_content": True,
            "must_exist": ["keep"],
        },
    }
    if mutation == "enabled-launcher-missing-path":
        manifest["launcher"]["enabled"] = True
    if mutation == "launcher-workspace-mismatch":
        manifest["launcher"]["workspace_relative"] = "_workspace\\different.code-workspace"
    if mutation == "launcher-temp-traversal":
        manifest["launcher"]["temp_relative"] = "..\\outside-temp"
    if mutation == "router-missing-target":
        manifest["context"]["router_targets"]["AGENTS.md"].append("MISSING_FEATURE.md")
    if mutation == "missing-provider-matrix":
        del manifest["providers"]["claude"]
    manifest_path = container / "manifest.json"
    write_json(manifest_path, manifest)
    return manifest_path, project


def physical_containment_fixture(root: Path) -> bool:
    lexical_candidate = os.path.abspath(root / "linked-config" / "project.code-workspace")
    outside_candidate = os.path.abspath(root.parent / "outside-config" / "project.code-workspace")
    original_realpath = os.path.realpath

    def simulated_realpath(value: str | bytes | os.PathLike[str] | os.PathLike[bytes]) -> str:
        absolute = os.path.abspath(os.fspath(value))
        if os.path.normcase(absolute) == os.path.normcase(lexical_candidate):
            return outside_candidate
        return original_realpath(absolute)

    with mock.patch("os.path.realpath", side_effect=simulated_realpath):
        try:
            project_path(root, "linked-config/project.code-workspace")
        except ValueError as exc:
            return "escapes root" in str(exc)
    return False


def run_self_test(work: Path) -> int:
    work = Path(os.path.abspath(work))
    work.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix="project-setup-fixtures-", dir=work))
    if os.path.commonpath((canonical(work), canonical(temporary))) != canonical(work):
        raise RuntimeError("fixture directory escaped explicit work root")
    summary = Report()
    if physical_containment_fixture(temporary):
        summary.pass_(
            "fixture_configured_path_link_escape",
            "a physically resolved path outside the context root was rejected",
        )
    else:
        summary.fail(
            "fixture_configured_path_link_escape",
            "a simulated junction or symlink escape was not rejected",
        )
    cases = [
        ("valid", None, None),
        ("valid-single-root", "single-root", None),
        ("valid-launcher-disabled", "launcher-disabled", None),
        ("valid-hand-maintained-router", "hand-maintained-router", None),
        ("invalid-json", "invalid-json", "workspace_json"),
        ("invalid-name-prefix", "invalid-name-prefix", "project_name_prefix"),
        ("wrong-root-order", "wrong-root-order", "workspace_root_order"),
        ("unauthorized-root", "unauthorized-root", "workspace_authorized_roots"),
        ("missing-source-root", "missing-source-root", "topology_roots_exist"),
        ("missing-auxiliary-root", "missing-auxiliary-root", "topology_roots_exist"),
        ("duplicate-workspace-name", "duplicate-workspace-name", "workspace_folder_names"),
        ("duplicate-provider-root", "duplicate-provider-root", "claude_access_duplicates"),
        ("duplicate-auxiliary-root", "duplicate-auxiliary-root", "manifest_auxiliary_roots"),
        ("workspace-crlf-eol", "workspace-crlf-eol", "workspace_file_eol"),
        ("enabled-launcher-missing-path", "enabled-launcher-missing-path", "launcher_path"),
        ("launcher-wrong-extension", "launcher-wrong-extension", "launcher_extension"),
        ("launcher-crlf", "launcher-crlf", "launcher_lf"),
        ("launcher-workspace-mismatch", "launcher-workspace-mismatch", "launcher_workspace_target"),
        ("launcher-temp-traversal", "launcher-temp-traversal", "launcher_temp_target"),
        ("launcher-double-start", "launcher-double-start", "launcher_behavior"),
        ("launcher-resolver-injection", "launcher-resolver-injection", "launcher_behavior"),
        ("launcher-path-mutation", "launcher-path-mutation", "launcher_path_mutation"),
        ("launcher-cmd-path-mutation", "launcher-cmd-path-mutation", "launcher_path_mutation"),
        ("inert-launcher-behavior", "inert-launcher-behavior", "launcher_behavior"),
        ("workspace-path-append", "workspace-path-append", "unapproved_process_path_mutation"),
        ("workspace-terminal-path-key", "workspace-terminal-path-key", "unapproved_process_path_mutation"),
        ("workspace-profile-path-key", "workspace-profile-path-key", "unapproved_process_path_mutation"),
        ("workspace-automation-path-key", "workspace-automation-path-key", "unapproved_process_path_mutation"),
        ("workspace-cmd-path-command", "workspace-cmd-path-command", "unapproved_process_path_mutation"),
        ("workspace-braced-env-path", "workspace-braced-env-path", "unapproved_process_path_mutation"),
        ("workspace-set-item-env-path", "workspace-set-item-env-path", "unapproved_process_path_mutation"),
        ("typed-persistent-path", "typed-persistent-path", "persistent_path_mutation"),
        ("provider-path-key", "provider-path-key", "unapproved_process_path_mutation"),
        ("router-missing-target", "router-missing-target", "router_targets"),
        ("router-comment-only", "router-comment-only", "router_targets"),
        ("router-fenced-only", "router-fenced-only", "router_targets"),
        ("router-inline-code-only", "router-inline-code-only", "router_targets"),
        ("router-malformed-link", "router-malformed-link", "router_targets"),
        ("router-nested-fence-length", "router-nested-fence-length", "router_targets"),
        ("codex-tmpdir-inheritance", "codex-tmpdir-inheritance", "codex_exclude_tmpdir_env"),
        ("missing-provider-matrix", "missing-provider-matrix", "manifest_provider_contracts"),
    ]
    try:
        for name, mutation, expected_failure in cases:
            case_root = temporary / name
            manifest, project = build_fixture(case_root, mutation)
            result = validate_setup(manifest, project)
            failures = [check for check in result.checks if check.status == "failed"]
            if expected_failure is None:
                if failures:
                    summary.fail(f"fixture_{name}", f"valid fixture failed: {[check.code for check in failures]}")
                else:
                    summary.pass_(f"fixture_{name}", "valid fixture passed")
            elif expected_failure in result.failed_codes:
                for check in failures:
                    print(f"EXPECTED FAILED {name}/{check.code}: {check.detail}")
                summary.pass_(f"fixture_{name}", f"fixture was rejected with required code {expected_failure}")
            else:
                summary.fail(
                    f"fixture_{name}",
                    f"required failure {expected_failure} absent; observed={sorted(result.failed_codes)}",
                )
    finally:
        if os.path.commonpath((canonical(work), canonical(temporary))) == canonical(work):
            shutil.rmtree(temporary)
    emit(summary)
    return 1 if summary.count("failed") else 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, help="explicit setup manifest")
    parser.add_argument("--project-root", type=Path, help="project context/configuration root")
    parser.add_argument("--self-test", action="store_true", help="run bounded positive and negative fixtures")
    parser.add_argument("--work", type=Path, help="approved fixture work directory for --self-test")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if sys.version_info < (3, 12):
        print(f"FAILED   python_version: Python 3.12+ required; running {sys.version.split()[0]}")
        print("SUMMARY passed=0 failed=1 skipped=0 not-run=0")
        return 2
    if args.self_test:
        if args.work is None:
            print("FAILED   self_test_work: --work is required so fixtures stay under an approved root")
            print("SUMMARY passed=0 failed=1 skipped=0 not-run=0")
            return 2
        try:
            return run_self_test(args.work)
        except Exception as exc:  # Keep malformed fixture/tool failures inside the status contract.
            print(f"FAILED   self_test_exception: {type(exc).__name__}: {exc}")
            print("SUMMARY passed=0 failed=1 skipped=0 not-run=0")
            return 2
    if args.manifest is None or args.project_root is None:
        print("FAILED   arguments: --manifest and --project-root are required unless --self-test is used")
        print("SUMMARY passed=0 failed=1 skipped=0 not-run=0")
        return 2
    try:
        report = validate_setup(Path(os.path.abspath(args.manifest)), Path(os.path.abspath(args.project_root)))
    except Exception as exc:  # Refuse unsafe input without emitting a traceback-only result.
        print(f"FAILED   validator_exception: {type(exc).__name__}: {exc}")
        print("SUMMARY passed=0 failed=1 skipped=0 not-run=0")
        return 2
    report.pass_("python_version", f"validator ran with Python {sys.version.split()[0]}")
    emit(report)
    return 1 if report.count("failed") else 0


if __name__ == "__main__":
    raise SystemExit(main())
