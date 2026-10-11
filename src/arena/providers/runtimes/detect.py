"""Find a vendor CLI, read its version and sign-in state, and say what to do next.

Nothing here sends a model request. Each probe is a status, version or model-list command, run
with an environment built from scratch: a leftover `ANTHROPIC_*` variable makes
`claude auth status` report a subscription sign-in that is not there, so the child never
inherits the caller's environment (docs/subsystems/provider-runtimes.md).
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
from collections.abc import Callable, Mapping, Sequence
from dataclasses import asdict, dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any, cast

from arena.providers.runtimes.specs import SPECS, ProtocolFailure, RuntimeSpec

PROBE_TIMEOUT_SECONDS = 15.0
_FORWARDED = frozenset(
    {
        "PATH",
        "HOME",
        "USER",
        "LOGNAME",
        "SHELL",
        "TMPDIR",
        "LANG",
        "LC_ALL",
        "TERM",
        "HTTP_PROXY",
        "HTTPS_PROXY",
        "NO_PROXY",
        "ALL_PROXY",
        "http_proxy",
        "https_proxy",
        "no_proxy",
        "all_proxy",
        "SSL_CERT_FILE",
        "SSL_CERT_DIR",
        "NODE_EXTRA_CA_CERTS",
        # Where the user keeps the sign-in, not a credential.
        "CODEX_HOME",
        "CLAUDE_CONFIG_DIR",
    }
)
_VERSION = re.compile(r"(\d+)\.(\d+)\.(\d+)")


class RuntimeStatus(StrEnum):
    OK = "ok"
    NOT_INSTALLED = "not_installed"
    AUTH_REQUIRED = "auth_required"
    INCOMPATIBLE_VERSION = "incompatible_version"
    UNSUPPORTED_MODEL = "unsupported_model"
    PROTOCOL_FAILURE = "protocol_failure"
    PERMISSION_FAILURE = "permission_failure"


class AuthState(StrEnum):
    AUTHENTICATED = "authenticated"
    UNAUTHENTICATED = "unauthenticated"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class ProbeResult:
    returncode: int
    stdout: str
    stderr: str


# argv, environment, timeout seconds. Raises OSError or subprocess.TimeoutExpired like `run`.
Probe = Callable[[Sequence[str], Mapping[str, str], float], ProbeResult]
Finder = Callable[[RuntimeSpec, Mapping[str, str]], str | None]


@dataclass(frozen=True)
class RuntimeReport:
    runtime: str
    vendor: str
    status: RuntimeStatus
    auth: AuthState
    binary: str | None
    version: str | None
    detail: str
    hint: str | None

    def to_dict(self) -> dict[str, Any]:
        return {
            key: str(value) if value is not None else None for key, value in asdict(self).items()
        }


def probe_environment(
    environ: Mapping[str, str], extra: Mapping[str, str] | None = None
) -> dict[str, str]:
    """The environment for a child CLI: an allowlist, never an empty key variable."""
    child = {name: value for name, value in environ.items() if name in _FORWARDED and value}
    child.update(extra or {})
    return child


def run_probe(argv: Sequence[str], env: Mapping[str, str], timeout: float) -> ProbeResult:
    completed = subprocess.run(
        list(argv),
        env=dict(env),
        capture_output=True,
        text=True,
        timeout=timeout,
        stdin=subprocess.DEVNULL,
        check=False,
    )
    return ProbeResult(completed.returncode, completed.stdout, completed.stderr)


def find_binary(spec: RuntimeSpec, environ: Mapping[str, str]) -> str | None:
    """The vendor override, then PATH, then common install dirs, then a login shell."""
    override = environ.get(spec.path_variable, "").strip()
    if override:
        path = Path(override)
        return str(path) if path.is_file() else None
    found = shutil.which(spec.binary, path=environ.get("PATH", ""))
    if found:
        return found
    home = Path(environ.get("HOME", "/nonexistent"))
    for entry in spec.common_dirs:
        directory = home / entry[2:] if entry.startswith("~/") else Path(entry)
        candidate = directory / spec.binary
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return str(candidate)
    return _login_shell_lookup(spec, environ)


def _login_shell_lookup(spec: RuntimeSpec, environ: Mapping[str, str]) -> str | None:
    shell = environ.get("SHELL", "")
    if not shell or not Path(shell).is_file():
        return None
    try:
        result = run_probe(
            [shell, "-lc", f"command -v {spec.binary}"], probe_environment(environ), 5.0
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    lines = [line for line in result.stdout.splitlines() if line.strip()]
    candidate = lines[-1].strip() if lines else ""
    return candidate if result.returncode == 0 and Path(candidate).is_file() else None


def _report(
    spec: RuntimeSpec,
    status: RuntimeStatus,
    detail: str,
    *,
    auth: AuthState = AuthState.UNKNOWN,
    binary: str | None = None,
    version: str | None = None,
    hint: str | None = None,
) -> RuntimeReport:
    return RuntimeReport(spec.runtime, spec.vendor, status, auth, binary, version, detail, hint)


def detect_version(
    spec: RuntimeSpec,
    environ: Mapping[str, str] | None = None,
    *,
    probe: Probe = run_probe,
    find: Finder = find_binary,
) -> RuntimeReport:
    """Check the binary's version without reading sign-in state."""
    source = os.environ if environ is None else environ
    binary = find(spec, source)
    if binary is None:
        return _report(
            spec,
            RuntimeStatus.NOT_INSTALLED,
            f"{spec.binary} was not found",
            hint=f"Install it: {spec.install_hint}",
        )
    env = probe_environment(source)
    try:
        version_result = probe([binary, *spec.version_args], env, PROBE_TIMEOUT_SECONDS)
    except FileNotFoundError:
        return _report(
            spec,
            RuntimeStatus.NOT_INSTALLED,
            f"{binary} does not exist",
            hint=f"Install it: {spec.install_hint}",
        )
    except PermissionError:
        return _report(
            spec,
            RuntimeStatus.PERMISSION_FAILURE,
            f"{binary} is not executable by this user",
            binary=binary,
            hint=f"Check the file mode of {binary}",
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        return _report(
            spec,
            RuntimeStatus.PROTOCOL_FAILURE,
            f"{spec.binary} --version failed: {type(error).__name__}",
            binary=binary,
        )
    match = _VERSION.search(version_result.stdout + version_result.stderr)
    if version_result.returncode != 0 or match is None:
        return _report(
            spec,
            RuntimeStatus.PROTOCOL_FAILURE,
            f"{spec.binary} --version printed no version number",
            binary=binary,
        )
    version = match.group(0)
    found_version = tuple(int(part) for part in match.groups())
    if found_version < spec.min_version:
        wanted = ".".join(str(part) for part in spec.min_version)
        return _report(
            spec,
            RuntimeStatus.INCOMPATIBLE_VERSION,
            f"{spec.binary} {version} is older than {wanted}; the recorded contract is "
            f"{spec.recorded_version}",
            binary=binary,
            version=version,
            hint=f"Update it: {spec.install_hint}",
        )
    return _report(spec, RuntimeStatus.OK, "version compatible", binary=binary, version=version)


def detect_runtime(
    spec: RuntimeSpec,
    environ: Mapping[str, str] | None = None,
    *,
    probe: Probe = run_probe,
    find: Finder = find_binary,
) -> RuntimeReport:
    """Version and sign-in state of one vendor CLI, without a model call."""
    source = os.environ if environ is None else environ
    report = detect_version(spec, source, probe=probe, find=find)
    if report.status is not RuntimeStatus.OK:
        return report
    binary, version = report.binary, report.version
    assert binary is not None
    env = probe_environment(source)
    try:
        auth = probe([binary, *spec.auth_args], env, PROBE_TIMEOUT_SECONDS)
        signed_in, detail = spec.parse_auth(auth.returncode, auth.stdout, auth.stderr)
    except ProtocolFailure as error:
        return _report(
            spec, RuntimeStatus.PROTOCOL_FAILURE, str(error), binary=binary, version=version
        )
    except PermissionError:
        return _report(
            spec,
            RuntimeStatus.PERMISSION_FAILURE,
            f"{binary} is not executable by this user",
            binary=binary,
            version=version,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        return _report(
            spec,
            RuntimeStatus.PROTOCOL_FAILURE,
            f"sign-in check failed: {type(error).__name__}",
            binary=binary,
            version=version,
        )
    if not signed_in:
        return _report(
            spec,
            RuntimeStatus.AUTH_REQUIRED,
            detail,
            auth=AuthState.UNAUTHENTICATED,
            binary=binary,
            version=version,
            hint=f"Sign in: {spec.login_hint}",
        )
    return _report(
        spec,
        RuntimeStatus.OK,
        detail,
        auth=AuthState.AUTHENTICATED,
        binary=binary,
        version=version,
    )


def detect_all(
    environ: Mapping[str, str] | None = None,
    *,
    probe: Probe = run_probe,
    find: Finder = find_binary,
) -> list[RuntimeReport]:
    return [detect_runtime(spec, environ, probe=probe, find=find) for spec in SPECS]


def read_codex_models(
    binary: str,
    environ: Mapping[str, str] | None = None,
    *,
    probe: Probe = run_probe,
) -> list[str]:
    """The slugs `codex debug models` offers, read under an empty `CODEX_HOME`.

    Hidden models are left out. Raises `ProtocolFailure` when the CLI cannot be run or its
    output is not the recorded shape.
    """
    source = os.environ if environ is None else environ
    with tempfile.TemporaryDirectory(prefix="arena-codex-home-") as scratch:
        env = probe_environment(source, {"CODEX_HOME": scratch})
        try:
            result = probe([binary, "debug", "models"], env, PROBE_TIMEOUT_SECONDS)
            raw: Any = json.loads(result.stdout)
        except (OSError, subprocess.TimeoutExpired, json.JSONDecodeError) as error:
            raise ProtocolFailure(f"model list failed: {error!r}") from error
    models: Any = cast(dict[str, Any], raw).get("models") if isinstance(raw, dict) else None
    if not isinstance(models, list):
        raise ProtocolFailure("model list has no models array")
    entries = [
        cast(dict[str, Any], item) for item in cast(list[Any], models) if isinstance(item, dict)
    ]
    return sorted(str(item["slug"]) for item in entries if item.get("visibility") == "list")


def check_model(
    report: RuntimeReport,
    model: str,
    environ: Mapping[str, str] | None = None,
    *,
    probe: Probe = run_probe,
) -> RuntimeReport:
    """Whether the CLI lists `model`. Only the Codex CLI has a list command.

    The list is read under an empty `CODEX_HOME`, because a user's config can point Codex at
    another gateway whose catalog is not ChatGPT's. A CLI with no list command passes the
    check unchanged, and a model it later refuses is a trial error, not a fallback.
    """
    spec = next((item for item in SPECS if item.runtime == report.runtime), None)
    if spec is None or report.status is not RuntimeStatus.OK or spec.runtime != "codex":
        return report
    assert report.binary is not None
    try:
        listed = read_codex_models(report.binary, environ, probe=probe)
    except ProtocolFailure as error:
        return _with(report, RuntimeStatus.PROTOCOL_FAILURE, str(error))
    if model in listed:
        return report
    return _with(
        report,
        RuntimeStatus.UNSUPPORTED_MODEL,
        f"{model} is not in the models this {spec.binary} lists: {', '.join(listed)}",
    )


def _with(report: RuntimeReport, status: RuntimeStatus, detail: str) -> RuntimeReport:
    return RuntimeReport(
        report.runtime,
        report.vendor,
        status,
        report.auth,
        report.binary,
        report.version,
        detail,
        report.hint,
    )
