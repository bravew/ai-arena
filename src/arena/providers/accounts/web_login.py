"""Bounded, private vendor CLI login jobs. No vendor output is returned to the viewer."""

from __future__ import annotations

import os
import re
import selectors
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from collections.abc import Mapping
from contextlib import suppress
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import parse_qsl, urlsplit

from arena.agents.local.types import SignIn
from arena.providers.accounts.keychain import (
    KeychainError,
    delete_scoped_claude_keychain,
    read_scoped_claude_keychain,
)
from arena.providers.accounts.probe import (
    MAX_CREDENTIAL_BYTES,
    prepare_home,
    private_environment,
    staged_account,
    subscription_auth,
    valid_sign_in,
)
from arena.providers.accounts.store import AccountError, AccountStore
from arena.providers.runtimes import spec_for_vendor
from arena.providers.runtimes.detect import find_binary
from arena.providers.runtimes.specs import ProtocolFailure, RuntimeSpec

_TERMINAL = {"succeeded", "failed", "cancelled", "expired"}
_URL = re.compile(r"https://[^\s<>\"\x1b]+")
_DEVICE_CODE = re.compile(r"(?<![A-Z0-9])([A-Z0-9]{4}-[A-Z0-9]{4})(?![A-Z0-9])")
_CODE = re.compile(r"[A-Za-z0-9_~.+/#=-]{1,2048}\Z")
_VERSION = re.compile(r"(?<!\d)(\d+)\.(\d+)\.(\d+)(?!\d)")
_MAX_OUTPUT = 256 * 1024
_MESSAGES = {
    "starting": "Starting the vendor login.",
    "awaiting_browser": "Open the vendor link to sign in.",
    "awaiting_device": "Open the vendor link and enter the device code.",
    "awaiting_code": "Complete the vendor login and paste the authorization code.",
    "succeeded": "Subscription account connected.",
    "failed": "Login could not be completed. Check the CLI version and try again.",
    "cancelled": "Login cancelled.",
    "expired": "Login timed out. Start a new login.",
}


def safe_login_url(vendor: str, value: str) -> str | None:
    """Allow only recorded vendor auth endpoints and bounded OAuth parameters."""
    if len(value) > 8192 or any(ord(char) < 33 or ord(char) == 127 for char in value):
        return None
    try:
        url = urlsplit(value)
        if url.scheme != "https" or url.username or url.password or url.port not in {None, 443}:
            return None
    except ValueError:
        return None
    paths = (
        {
            "auth.claude.com": {"/oauth/authorize"},
            "claude.ai": {"/oauth/authorize"},
            "platform.claude.com": {"/oauth/authorize"},
        }
        if vendor == "claude"
        else {
            "auth.openai.com": {"/codex/device", "/deviceauth", "/oauth/authorize"},
            "chatgpt.com": {"/activate", "/auth/login", "/oauth/authorize"},
        }
    )
    if url.hostname not in paths or url.path not in paths[url.hostname] or url.fragment:
        return None
    allowed = {
        "client_id",
        "response_type",
        "redirect_uri",
        "scope",
        "state",
        "code_challenge",
        "code_challenge_method",
        "org",
        "organization_id",
        "codex_cli_simplified_flow",
        "id_token_add_organizations",
        "originator",
        "device_user_code",
    }
    try:
        pairs = parse_qsl(url.query, keep_blank_values=True, max_num_fields=20, strict_parsing=True)
    except ValueError:
        return None
    if len({key for key, _ in pairs}) != len(pairs):
        return None
    for key, token in pairs:
        if key not in allowed or not token or len(token) > 2048:
            return None
        if key == "redirect_uri":
            if token not in {
                "https://platform.claude.com/oauth/code/callback",
                "https://console.anthropic.com/oauth/code/callback",
                "http://localhost:1455/auth/callback",
            }:
                return None
        elif key == "scope":
            if not re.fullmatch(r"[A-Za-z0-9_: .-]{1,512}", token):
                return None
        elif not re.fullmatch(r"[A-Za-z0-9_~.+:/=-]{1,2048}", token):
            return None
    return value


@dataclass
class _Job:
    id: str
    vendor: str
    state: str = "starting"
    url: str | None = None
    user_code: str | None = None
    stop: threading.Event = field(default_factory=threading.Event, repr=False)
    process: subprocess.Popen[bytes] | None = field(default=None, repr=False)
    thread: threading.Thread | None = field(default=None, repr=False)
    code: str | None = field(default=None, repr=False)


class LoginManager:
    """Threaded service; methods return immediately except bounded cancel/close cleanup."""

    def __init__(
        self,
        home: Path,
        environ: Mapping[str, str],
        *,
        timeout: float = 300.0,
        max_jobs: int = 16,
    ) -> None:
        if not 0 < timeout <= 900 or not 1 <= max_jobs <= 128:
            raise ValueError("invalid login service bounds")
        self.home = home
        self.environ = dict(environ)
        self.store = AccountStore(home / "accounts")
        self.timeout = timeout
        self.max_jobs = max_jobs
        self._jobs: dict[str, _Job] = {}
        self._lock = threading.RLock()
        self._closed = False

    def _public(self, job: _Job) -> dict[str, str]:
        data = {
            "id": job.id,
            "vendor": job.vendor,
            "state": job.state,
            "message": _MESSAGES[job.state],
        }
        if job.state not in _TERMINAL:
            if job.url:
                data["url"] = job.url
            if job.user_code:
                data["user_code"] = job.user_code
        return data

    def start(self, vendor: str) -> dict[str, str]:
        if vendor not in {"claude", "chatgpt"}:
            raise ValueError("unsupported subscription vendor")
        with self._lock:
            if self._closed:
                raise ValueError("login service is closed")
            for existing in self._jobs.values():
                if existing.vendor == vendor and existing.thread and existing.thread.is_alive():
                    return self._public(existing)
            if len(self._jobs) >= self.max_jobs:
                retired = next(
                    (
                        key
                        for key, job in self._jobs.items()
                        if job.state in _TERMINAL and job.thread and not job.thread.is_alive()
                    ),
                    None,
                )
                if retired is None:
                    raise ValueError("login service is busy")
                del self._jobs[retired]
            job = _Job(uuid.uuid4().hex, vendor)
            self._jobs[job.id] = job
            job.thread = threading.Thread(target=self._run, args=(job,), daemon=True)
            job.thread.start()
            return self._public(job)

    def status(self, id: str) -> dict[str, str]:
        with self._lock:
            return self._public(self._get(id))

    def _get(self, id: str) -> _Job:
        job = self._jobs.get(id)
        if job is None:
            raise ValueError("unknown login job")
        return job

    def submit_code(self, id: str, code: str) -> dict[str, str]:
        if not _CODE.fullmatch(code):
            raise ValueError("invalid authorization code")
        with self._lock:
            job = self._get(id)
            if job.vendor != "claude" or job.state != "awaiting_code" or job.code is not None:
                raise ValueError("login is not waiting for a code")
            job.code = code
            return self._public(job)

    def cancel(self, id: str) -> dict[str, str]:
        with self._lock:
            job = self._get(id)
            if job.state not in _TERMINAL:
                job.state = "cancelled"
                job.stop.set()
            thread = job.thread
        if thread:
            thread.join(timeout=12.0)
        with self._lock:
            return self._public(job)

    def cancel_vendor(self, vendor: str) -> None:
        """Stop pending writes before an explicit import; keep the existing account."""
        if vendor not in {"claude", "chatgpt"}:
            raise ValueError("unsupported subscription vendor")
        with self._lock:
            ids = [job.id for job in self._jobs.values() if job.vendor == vendor]
            for id in ids:
                job = self._jobs[id]
                job.stop.set()
                if job.state not in _TERMINAL:
                    job.state = "cancelled"
        for id in ids:
            self.cancel(id)

    def disconnect(self, vendor: str) -> bool:
        self.cancel_vendor(vendor)
        with self._lock:
            return self.store.forget(vendor)

    def close(self) -> None:
        with self._lock:
            self._closed = True
            ids = list(self._jobs)
            for job in self._jobs.values():
                job.stop.set()
                if job.state not in _TERMINAL:
                    job.state = "cancelled"
        for id in ids:
            self.cancel(id)

    def _state(self, job: _Job, state: str) -> None:
        with self._lock:
            if not job.stop.is_set() and job.state not in _TERMINAL:
                job.state = state

    def _parse(self, job: _Job, text: str) -> None:
        with self._lock:
            if job.stop.is_set() or job.state in _TERMINAL:
                return
            for match in _URL.finditer(text):
                url = safe_login_url(job.vendor, match.group(0))
                if url:
                    job.url = url
                    job.state = "awaiting_browser" if job.vendor == "claude" else "awaiting_device"
            if job.vendor == "chatgpt" and job.url:
                code = _DEVICE_CODE.search(text)
                if code:
                    job.user_code = code.group(1)
                    job.state = "awaiting_device"
            if job.vendor == "claude" and job.url:
                job.state = "awaiting_code"

    def _collect(
        self,
        job: _Job,
        argv: list[str],
        env: dict[str, str],
        home: Path,
        deadline: float,
        *,
        login: bool,
    ) -> tuple[int, str]:
        process = subprocess.Popen(
            argv,
            cwd=home,
            env=env,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            start_new_session=True,
        )
        job.process = process
        total = 0
        windows: dict[int, str] = {}
        version_text = ""
        try:
            with selectors.DefaultSelector() as selector:
                assert process.stdout and process.stderr
                for stream in (process.stdout, process.stderr):
                    os.set_blocking(stream.fileno(), False)
                    selector.register(stream, selectors.EVENT_READ)
                while selector.get_map() or process.poll() is None:
                    if job.stop.is_set():
                        raise InterruptedError
                    if time.monotonic() >= deadline:
                        raise TimeoutError
                    if login:
                        with self._lock:
                            code, job.code = job.code, None
                        if code and process.stdin:
                            process.stdin.write((code + "\n").encode())
                            process.stdin.flush()
                    for key, _ in selector.select(timeout=0.05):
                        chunk = os.read(key.fd, 4096)
                        if not chunk:
                            selector.unregister(key.fileobj)
                            continue
                        total += len(chunk)
                        if total > _MAX_OUTPUT:
                            raise ValueError("vendor output exceeded limit")
                        text = chunk.decode("utf-8", errors="replace")
                        window = (windows.get(key.fd, "") + text)[-16384:]
                        windows[key.fd] = window
                        if login:
                            self._parse(job, window)
                        else:
                            version_text += text
                    if login:
                        browser_file = home / "browser-url"
                        if browser_file.is_file() and browser_file.stat().st_size <= 8192:
                            self._parse(job, browser_file.read_text(encoding="utf-8"))
                return process.wait(timeout=1), version_text
        finally:
            _terminate(process)
            for stream in (process.stdin, process.stdout, process.stderr):
                if stream:
                    stream.close()
            job.process = None

    def _run(self, job: _Job) -> None:
        scratch: Path | None = None
        env: dict[str, str] = {}
        attempted = False
        outcome = "succeeded"
        try:
            spec = spec_for_vendor(job.vendor)
            assert spec is not None
            binary = find_binary(spec, self.environ)
            if binary is None:
                raise ValueError("CLI unavailable")
            scratch = Path(tempfile.mkdtemp(prefix="arena-login-")).resolve()
            prepare_home(spec, scratch)
            env = private_environment(spec, scratch, self.environ)
            deadline = time.monotonic() + self.timeout
            rc, version = self._collect(
                job,
                [binary, "--version"],
                env,
                scratch,
                min(deadline, time.monotonic() + 15),
                login=False,
            )
            match = _VERSION.search(version)
            floor = (
                max(spec.min_version, (2, 1, 296)) if job.vendor == "claude" else spec.min_version
            )
            if rc or match is None or tuple(map(int, match.groups())) < floor:
                raise ValueError("CLI version incompatible")
            _browser_helper(scratch, env)
            argv = (
                [binary, "auth", "login", "--claudeai"]
                if job.vendor == "claude"
                else [binary, "login", "--device-auth", "-c", 'cli_auth_credentials_store="file"']
            )
            attempted = True
            rc, _ = self._collect(job, argv, env, scratch, deadline, login=True)
            if rc:
                raise ValueError("vendor login failed")
            sign_in = _read_login(spec, scratch, env)
            if not valid_sign_in(job.vendor, sign_in):
                raise ValueError("vendor credential invalid")
            if job.stop.is_set():
                raise InterruptedError
            with staged_account(spec, sign_in, env) as probe_env:
                rc, status = self._collect(
                    job,
                    [binary, *spec.auth_args],
                    probe_env,
                    Path(probe_env["HOME"]),
                    min(deadline, time.monotonic() + 15),
                    login=False,
                )
                signed_in, detail = spec.parse_auth(rc, status, "")
                if not signed_in or not subscription_auth(job.vendor, detail):
                    raise ValueError("vendor auth verification failed")
            with self._lock:
                if job.stop.is_set():
                    raise InterruptedError
                self.store.save(job.vendor, sign_in, "login")
        except InterruptedError:
            outcome = "cancelled"
        except TimeoutError:
            outcome = "expired"
        except (
            OSError,
            ValueError,
            AccountError,
            KeychainError,
            ProtocolFailure,
            subprocess.SubprocessError,
        ):
            outcome = "failed"
        finally:
            cleanup_failed = False
            if scratch:
                if attempted and job.vendor == "claude" and sys.platform == "darwin":
                    try:
                        delete_scoped_claude_keychain(scratch / ".claude", env)
                    except KeychainError:
                        cleanup_failed = True
                shutil.rmtree(scratch)
            with self._lock:
                job.code = None
                if job.state not in _TERMINAL:
                    # Cleanup precedes the terminal state, so a polled success also means the
                    # private home and any staged credential are already gone.
                    job.state = "failed" if cleanup_failed else outcome


def _read_login(spec: RuntimeSpec, home: Path, env: Mapping[str, str]) -> SignIn:
    relative = ".claude/.credentials.json" if spec.vendor == "claude" else ".codex/auth.json"
    path = home / relative
    if path.is_symlink():
        raise ValueError("vendor credential is a symlink")
    if path.is_file():
        with path.open("rb") as stream:
            return SignIn({relative: stream.read(MAX_CREDENTIAL_BYTES + 1)})
    if spec.vendor == "claude" and sys.platform == "darwin":
        return read_scoped_claude_keychain(home / ".claude", env)
    raise ValueError("vendor wrote no credentials")


def _browser_helper(home: Path, env: dict[str, str]) -> None:
    helper = home / "browser-helper"
    helper.write_text(
        '#!/bin/sh\nprintf "%s" "$1" > "$ARENA_LOGIN_BROWSER_URL_FILE"\n', encoding="utf-8"
    )
    helper.chmod(0o700)
    env.update({"BROWSER": str(helper), "ARENA_LOGIN_BROWSER_URL_FILE": str(home / "browser-url")})


def _terminate(process: subprocess.Popen[bytes]) -> None:
    # Kill descendants even if the CLI parent exited while a pipe stayed open.
    with suppress(ProcessLookupError):
        os.killpg(process.pid, signal.SIGTERM)
    with suppress(subprocess.TimeoutExpired):
        process.wait(timeout=0.5)
    with suppress(ProcessLookupError):
        os.killpg(process.pid, signal.SIGKILL)
    process.wait(timeout=1)
