"""Local-only routes that define contestant sets and launch `arena run` from the viewer.

A launched run is the same `arena run` the terminal uses, started as a child process, so the web
path and the CLI score trials identically. One run at a time keeps a stray click from spending
more than one batch of subscription quota. The child's output goes to a private log; the viewer
reads only a redacted tail of it.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from collections.abc import Callable, Mapping, Sequence
from contextlib import suppress
from pathlib import Path
from typing import Any, cast

import yaml

from arena.catalog.config import load_catalog
from arena.core.models import Contestant, Task
from arena.kits.matrix import expand_matrix
from arena.providers.config import Provider, load_providers, parse_yaml
from arena.runners.scheduler import expand_jobs
from arena.server.app import ArenaHTTPServer
from arena.server.provider_settings import read_body

MAX_REPEATS = 10
MAX_TRIALS = 200
MAX_SET_MODELS = 8
_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}")
_SET_REF = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}(?:/[A-Za-z0-9][A-Za-z0-9._-]{0,63})?")
_RUN_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}")
_JOB_ID = re.compile(r"[a-f0-9]{16}")
_RUN_LINE = re.compile(r"^run_id=([A-Za-z0-9][A-Za-z0-9._-]{0,127})\s*$", re.MULTILINE)
_SECRET = re.compile(r"(?:sk-|sess-|Bearer\s+)[A-Za-z0-9_.-]+", re.IGNORECASE)
_TERMINAL = {"succeeded", "failed", "cancelled"}
_LOG_TAIL = 4000
CHILD_COMMAND = (sys.executable, "-c", "from arena.cli import app; app()")


class LaunchError(Exception):
    """A request the launcher refuses. The message is safe to show and never holds a secret."""


class RunNotFound(LaunchError):
    """No run with that id."""


def _redact(text: str) -> str:
    return _SECRET.sub("[redacted]", text)


def _atomic_write(path: Path, data: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, name = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.")
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            stream.write(data)
        os.chmod(name, 0o644)
        os.replace(name, path)
    except BaseException:
        with suppress(OSError):
            os.unlink(name)
        raise


def _load_contestants(path: Path) -> list[Contestant]:
    raw_value = parse_yaml(path)
    if not isinstance(raw_value, dict):
        raise LaunchError("contestant file must be a mapping")
    raw = cast(dict[str, Any], raw_value)
    if "matrix" in raw:
        return expand_matrix(raw)
    from arena.core.modelref import ModelRef

    config = dict(raw)
    config["label"] = config.pop("id_label", config.get("label"))
    config["model"] = ModelRef.parse(str(config["model"]))
    scaffold = config.get("scaffold", "none")
    config["scaffold"] = None if scaffold is None or scaffold == "none" else scaffold
    return [Contestant.model_validate(config)]


class Launcher:
    """Contestant sets, run plans and the single child process that executes a run."""

    def __init__(
        self,
        home: Path,
        project_root: Path,
        providers_path: Path,
        catalog_path: Path,
        environ: Mapping[str, str],
        *,
        command: Sequence[str] = CHILD_COMMAND,
    ) -> None:
        self.home = home
        self.root = project_root
        self.providers_path = providers_path
        self.catalog_path = catalog_path
        self.environ = dict(environ)
        self.command = tuple(command)
        self.jobs_dir = home / "web-runs"
        self._lock = threading.RLock()
        self._processes: dict[str, subprocess.Popen[bytes]] = {}
        self._closed = False
        self._jobs: dict[str, dict[str, Any]] = {}
        self._load_saved()

    # -- catalog of what can be run ------------------------------------------------------------

    def _providers(self) -> dict[str, Provider]:
        return {p.id: p for p in load_providers(self.providers_path).providers}

    def _kind(self, contestant: Contestant, providers: Mapping[str, Provider]) -> tuple[str, str]:
        """How a contestant would run here: `(kind, reason)`; kind is mock, subscription or none."""
        provider_id = contestant.model.provider
        if provider_id == "mock":
            return "mock", ""
        provider = providers.get(provider_id)
        if provider is None:
            return "none", f"provider {provider_id} is not configured"
        if not provider.enabled:
            return "none", f"provider {provider_id} is disabled"
        subscription = provider.subscription
        if subscription is None or subscription.runtime != "local-cli":
            return (
                "none",
                "API contestants need an agent scaffold and a budget; run them in the CLI",
            )
        if contestant.scaffold is not None:
            return "none", "subscription contestants run without a scaffold here"
        return "subscription", ""

    def options(self) -> dict[str, Any]:
        providers = self._providers()
        models: list[dict[str, Any]] = []
        for model in load_catalog(self.catalog_path).models:
            provider_id = model.ref.split("/", 1)[0]
            provider = providers.get(provider_id)
            sub = provider.subscription if provider else None
            if provider and sub and sub.runtime == "local-cli" and model.pricing == "subscription":
                models.append(
                    {
                        "ref": model.ref,
                        "provider": provider_id,
                        "vendor": sub.vendor,
                        "enabled": provider.enabled,
                    }
                )
        with self._lock:
            active = next((j for j in self._jobs.values() if j["state"] == "running"), None)
            active_id = active["id"] if active else None
        return {
            "suites": self._suites(),
            "sets": self._sets(),
            "models": models,
            "docker_available": shutil.which("docker", path=self.environ.get("PATH")) is not None,
            "limits": {"max_repeats": MAX_REPEATS, "max_trials": MAX_TRIALS},
            "active_job": active_id,
        }

    def _suites(self) -> list[dict[str, Any]]:
        found: list[dict[str, Any]] = []
        base = self.root / "suites"
        if not base.is_dir():
            return found
        for directory in sorted(p for p in base.iterdir() if p.is_dir()):
            if not _NAME.fullmatch(directory.name):
                continue
            files = sorted(directory.glob("tasks/*/task.yaml"))
            if not files:
                continue
            kinds: set[str] = set()
            for file in files:
                with suppress(OSError, ValueError, yaml.YAMLError):
                    kinds.add(Task.model_validate(parse_yaml(file)).kind)
            found.append({"id": directory.name, "tasks": len(files), "kinds": sorted(kinds)})
        return found

    def _set_paths(self) -> dict[str, Path]:
        base = self.root / "contestants"
        paths: dict[str, Path] = {}
        for pattern, prefix in (("sets/*.yaml", "sets/"), ("*.yaml", "")):
            for path in sorted(base.glob(pattern)):
                if path.is_file() and _NAME.fullmatch(path.stem):
                    paths[f"{prefix}{path.stem}"] = path
        return paths

    def _sets(self) -> list[dict[str, Any]]:
        providers = self._providers()
        result: list[dict[str, Any]] = []
        for name, path in self._set_paths().items():
            entry: dict[str, Any] = {"name": name}
            try:
                members = _load_contestants(path)
            except (OSError, ValueError, yaml.YAMLError, LaunchError):
                entry.update(contestants=[], error="this file is not a valid contestant set")
                result.append(entry)
                continue
            rows: list[dict[str, Any]] = []
            for member in members:
                kind, reason = self._kind(member, providers)
                rows.append(
                    {
                        "label": member.label or member.id[:12],
                        "model": str(member.model),
                        "runnable": kind != "none",
                        "kind": kind,
                        "reason": reason,
                    }
                )
            entry["contestants"] = rows
            result.append(entry)
        return result

    def _resolve_set(self, name: str) -> Path:
        if not _SET_REF.fullmatch(name):
            raise LaunchError("contestant set name is invalid")
        path = self._set_paths().get(name)
        if path is None:
            raise LaunchError("contestant set not found")
        return path

    def _resolve_suite(self, suite: str) -> Path:
        if not _NAME.fullmatch(suite):
            raise LaunchError("suite name is invalid")
        path = self.root / "suites" / suite
        if not path.is_dir() or not any(path.glob("tasks/*/task.yaml")):
            raise LaunchError("suite not found")
        return path

    # -- writing a contestant set --------------------------------------------------------------

    def save_set(self, name: str, models: object, *, overwrite: bool) -> dict[str, Any]:
        if not _NAME.fullmatch(name):
            raise LaunchError("name may use letters, digits, dot, dash and underscore")
        if (
            not isinstance(models, list)
            or not 1 <= len(cast(list[object], models)) <= MAX_SET_MODELS
        ):
            raise LaunchError(f"choose between 1 and {MAX_SET_MODELS} models")
        refs = cast(list[object], models)
        if not all(isinstance(ref, str) for ref in refs) or len(set(refs)) != len(refs):
            raise LaunchError("models must be distinct catalog references")
        allowed = {m["ref"] for m in self.options()["models"] if m["enabled"]}
        chosen = [cast(str, ref) for ref in refs]
        unknown = [ref for ref in chosen if ref not in allowed]
        if unknown:
            raise LaunchError("only enabled subscription models from the catalog can be added")
        path = self.root / "contestants" / "sets" / f"{name}.yaml"
        if path.exists() and not overwrite:
            raise LaunchError("a set with this name exists; choose overwrite or another name")
        document = {"matrix": {"model": chosen, "scaffold": ["none"]}, "orchestration": "single"}
        header = f"# Contestant set written from the Arena viewer: {len(chosen)} model(s).\n"
        _atomic_write(path, header + yaml.safe_dump(document, sort_keys=False))
        try:
            _load_contestants(path)
        except (ValueError, yaml.YAMLError, LaunchError):
            raise LaunchError("the saved set did not validate") from None
        return {"ok": True, "name": f"sets/{name}"}

    # -- planning ------------------------------------------------------------------------------

    def plan(self, suite: str, set_name: str, repeats: object) -> dict[str, Any]:
        if not isinstance(repeats, int) or isinstance(repeats, bool):
            raise LaunchError("repeats must be a whole number")
        if not 1 <= repeats <= MAX_REPEATS:
            raise LaunchError(f"repeats must be between 1 and {MAX_REPEATS}")
        suite_dir = self._resolve_suite(suite)
        set_path = self._resolve_set(set_name)
        try:
            tasks = [
                Task.model_validate(parse_yaml(file))
                for file in sorted(suite_dir.glob("tasks/*/task.yaml"))
            ]
            contestants = _load_contestants(set_path)
        except (OSError, ValueError, yaml.YAMLError):
            raise LaunchError("the suite or contestant set could not be read") from None
        providers = self._providers()
        kinds = [self._kind(c, providers) for c in contestants]
        blockers = sorted({reason for kind, reason in kinds if kind == "none"})
        trials = len(expand_jobs(tasks, contestants, repeats))
        if trials > MAX_TRIALS:
            blockers.append(f"{trials} trials exceeds the {MAX_TRIALS} trial limit; lower repeats")
        scorers = sorted({str(s.get("id")) for task in tasks for s in task.scorers})
        subscription = sum(1 for kind, _ in kinds if kind == "subscription")
        return {
            "suite": suite,
            "set": set_name,
            "repeats": repeats,
            "tasks": len(tasks),
            "contestants": len(contestants),
            "trials": trials,
            "subscription_trials": subscription * len(tasks) * repeats,
            "scorers": scorers,
            "startable": not blockers,
            "blockers": blockers,
        }

    # -- jobs ----------------------------------------------------------------------------------

    def _path(self, job_id: str, suffix: str) -> Path:
        return self.jobs_dir / f"{job_id}.{suffix}"

    def _save(self, job: dict[str, Any]) -> None:
        _atomic_write(self._path(job["id"], "json"), json.dumps(job, sort_keys=True))

    def _load_saved(self) -> None:
        if not self.jobs_dir.is_dir():
            return
        for file in sorted(self.jobs_dir.glob("*.json")):
            if not _JOB_ID.fullmatch(file.stem):
                continue
            try:
                loaded: object = json.loads(file.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if not isinstance(loaded, dict):
                continue
            job = cast(dict[str, Any], loaded)
            if job.get("id") != file.stem:
                continue
            if job.get("state") not in _TERMINAL:
                # The server that owned this child has gone; the run itself can be resumed.
                job["state"], job["message"] = "failed", "Arena restarted while this run was going."
            self._jobs[file.stem] = job

    def start(
        self,
        suite: str,
        set_name: str,
        repeats: object,
        *,
        score_execution: bool,
        resume_run: str | None = None,
    ) -> dict[str, Any]:
        plan = self.plan(suite, set_name, repeats)
        if not plan["startable"]:
            raise LaunchError("; ".join(plan["blockers"]))
        if score_execution and shutil.which("docker", path=self.environ.get("PATH")) is None:
            raise LaunchError("execution scoring needs Docker, which was not found")
        argv = [
            *self.command,
            "run",
            str(self._resolve_suite(suite)),
            "-c",
            str(self._resolve_set(set_name)),
            "--repeats",
            str(plan["repeats"]),
            "--db",
            str(self.home / "arena.db"),
            "--home",
            str(self.home),
            "--providers",
            str(self.providers_path),
            "--catalog",
            str(self.catalog_path),
            "--max-concurrency",
            "4",
        ]
        if score_execution:
            argv.append("--score-execution")
        if resume_run is not None:
            if not _RUN_ID.fullmatch(resume_run):
                raise LaunchError("run id is invalid")
            argv += ["--resume", resume_run]
        with self._lock:
            if self._closed:
                raise LaunchError("the server is shutting down")
            if any(j["state"] == "running" for j in self._jobs.values()):
                raise LaunchError("a run is already going; wait for it or cancel it first")
            job_id = uuid.uuid4().hex[:16]
            job: dict[str, Any] = {
                "id": job_id,
                "suite": suite,
                "set": set_name,
                "repeats": plan["repeats"],
                "trials": plan["trials"],
                "score_execution": score_execution,
                "state": "running",
                "run_id": resume_run,
                "started_at": time.time(),
                "finished_at": None,
                "exit_code": None,
                "message": "",
                "cancelled": False,
            }
            self.jobs_dir.mkdir(parents=True, exist_ok=True)
            log = os.open(self._path(job_id, "log"), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            try:
                process = subprocess.Popen(
                    argv,
                    cwd=self.root,
                    env={**self.environ, "PYTHONUNBUFFERED": "1"},
                    stdin=subprocess.DEVNULL,
                    stdout=log,
                    stderr=subprocess.STDOUT,
                    start_new_session=True,
                )
            except OSError:
                raise LaunchError("could not start the run process") from None
            finally:
                os.close(log)
            self._jobs[job_id] = job
            self._processes[job_id] = process
            self._save(job)
            threading.Thread(target=self._watch, args=(job_id, process), daemon=True).start()
            return self._public(job)

    def _watch(self, job_id: str, process: subprocess.Popen[bytes]) -> None:
        code = process.wait()
        with self._lock:
            job = self._jobs[job_id]
            self._processes.pop(job_id, None)
            job["exit_code"] = code
            job["finished_at"] = time.time()
            if job["cancelled"]:
                job["state"], job["message"] = "cancelled", "Run cancelled."
            elif code == 0:
                job["state"], job["message"] = "succeeded", "Run finished."
            else:
                job["state"] = "failed"
                job["message"] = self._failure(job_id)
            run_id = self._run_id(job_id)
            if run_id:
                job["run_id"] = run_id
            self._save(job)

    def _log(self, job_id: str) -> str:
        try:
            with self._path(job_id, "log").open("rb") as stream:
                stream.seek(0, os.SEEK_END)
                size = stream.tell()
                stream.seek(max(0, size - 64 * 1024))
                return _redact(stream.read().decode("utf-8", errors="replace"))
        except OSError:
            return ""

    def _run_id(self, job_id: str) -> str | None:
        found = _RUN_LINE.findall(self._log(job_id))
        return found[-1] if found else None

    def _failure(self, job_id: str) -> str:
        for line in reversed(self._log(job_id).splitlines()):
            if line.strip().lower().startswith("error"):
                return line.strip()[:300]
        return "The run stopped before it finished. See the log below."

    def _public(self, job: Mapping[str, Any], *, detail: bool = False) -> dict[str, Any]:
        data = {k: v for k, v in job.items() if k != "cancelled"}
        if data["state"] == "running" and not data.get("run_id"):
            data["run_id"] = self._run_id(data["id"])
        if detail:
            data["log_tail"] = self._log(data["id"])[-_LOG_TAIL:]
        return data

    def list_jobs(self) -> list[dict[str, Any]]:
        with self._lock:
            jobs = sorted(self._jobs.values(), key=lambda j: j["started_at"], reverse=True)
            return [self._public(j) for j in jobs[:20]]

    def get(self, job_id: str) -> dict[str, Any]:
        with self._lock:
            job = self._jobs.get(job_id) if _JOB_ID.fullmatch(job_id) else None
            if job is None:
                raise RunNotFound("run not found")
            return self._public(job, detail=True)

    def cancel(self, job_id: str) -> dict[str, Any]:
        with self._lock:
            job = self._jobs.get(job_id) if _JOB_ID.fullmatch(job_id) else None
            process = self._processes.get(job_id)
            if job is None:
                raise RunNotFound("run not found")
            if process is not None and job["state"] == "running":
                job["cancelled"] = True
                self._signal(process, signal.SIGINT)
                for delay, sig in ((10, signal.SIGTERM), (20, signal.SIGKILL)):
                    timer = threading.Timer(delay, self._signal, (process, sig))
                    timer.daemon = True
                    timer.start()
            return self._public(job)

    def resume(self, job_id: str, *, score_execution: bool | None = None) -> dict[str, Any]:
        with self._lock:
            job = self._jobs.get(job_id) if _JOB_ID.fullmatch(job_id) else None
            if job is None:
                raise RunNotFound("run not found")
            if job["state"] == "running":
                raise LaunchError("this run is still going")
            if not job.get("run_id"):
                raise LaunchError("this run never recorded an id, so it cannot be resumed")
            chosen = job["score_execution"] if score_execution is None else score_execution
            return self.start(
                job["suite"],
                job["set"],
                job["repeats"],
                score_execution=bool(chosen),
                resume_run=job["run_id"],
            )

    @staticmethod
    def _signal(process: subprocess.Popen[bytes], sig: signal.Signals) -> None:
        if process.poll() is None:
            with suppress(ProcessLookupError, PermissionError):
                os.killpg(process.pid, sig)

    def close(self) -> None:
        with self._lock:
            self._closed = True
            running = list(self._processes.values())
        for process in running:
            self._signal(process, signal.SIGTERM)
        for process in running:
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self._signal(process, signal.SIGKILL)


def register_launch_routes(
    server: ArenaHTTPServer,
    home: Path,
    *,
    project_root: Path,
    providers_path: Path,
    catalog_path: Path,
    environ: Mapping[str, str],
    command: Sequence[str] = CHILD_COMMAND,
) -> Launcher:
    launcher = Launcher(home, project_root, providers_path, catalog_path, environ, command=command)
    server.close_callbacks.append(launcher.close)

    def read(action: Callable[[Any, str], object]) -> Callable[[Any, str], None]:
        def handle(handler: Any, match: str) -> None:
            if not handler.local_settings_access():
                handler.send_json(403, {"error": "run controls are available only locally"})
                return
            try:
                handler.send_json(200, action(handler, match))
            except LaunchError as error:
                handler.send_json(
                    404 if isinstance(error, RunNotFound) else 422, {"error": str(error)}
                )
            except (OSError, ValueError, yaml.YAMLError):
                handler.send_json(422, {"error": "could not read the run configuration"})

        return handle

    def write(action: Callable[[dict[str, Any], str], object]) -> Callable[[Any, str], None]:
        def handle(handler: Any, match: str) -> None:
            if not handler.local_settings_access(mutation=True):
                handler.send_json(
                    403, {"error": "run controls require direct local access and CSRF"}
                )
                return
            try:
                body = read_body(handler)
                handler.send_json(200, action(body, match))
            except LaunchError as error:
                handler.send_json(
                    404 if isinstance(error, RunNotFound) else 422, {"error": str(error)}
                )
            except Exception as error:
                status = 422 if isinstance(error, (OSError, ValueError, yaml.YAMLError)) else 500
                handler.send_json(status, {"error": "the run request could not be completed"})

        return handle

    def text(body: dict[str, Any], name: str) -> str:
        value = body.get(name)
        if not isinstance(value, str) or not value:
            raise LaunchError(f"{name} is required")
        return value

    def flag(body: dict[str, Any], name: str) -> bool:
        value = body.get(name, False)
        if not isinstance(value, bool):
            raise LaunchError(f"{name} must be true or false")
        return value

    server.register_route("GET", r"/api/launch/options", read(lambda _h, _m: launcher.options()))
    server.register_route(
        "GET", r"/api/launch/runs", read(lambda _h, _m: {"jobs": launcher.list_jobs()})
    )
    server.register_route(
        "GET", r"/api/launch/runs/([a-f0-9]{16})", read(lambda _h, m: launcher.get(m))
    )
    server.register_route(
        "POST",
        r"/api/launch/sets",
        write(
            lambda b, _m: launcher.save_set(
                text(b, "name"), b.get("models"), overwrite=flag(b, "overwrite")
            )
        ),
    )
    server.register_route(
        "POST",
        r"/api/launch/plan",
        write(lambda b, _m: launcher.plan(text(b, "suite"), text(b, "set"), b.get("repeats"))),
    )
    server.register_route(
        "POST",
        r"/api/launch/runs",
        write(
            lambda b, _m: launcher.start(
                text(b, "suite"),
                text(b, "set"),
                b.get("repeats"),
                score_execution=flag(b, "score_execution"),
            )
        ),
    )
    server.register_route(
        "POST", r"/api/launch/runs/([a-f0-9]{16})/cancel", write(lambda _b, m: launcher.cancel(m))
    )
    server.register_route(
        "POST",
        r"/api/launch/runs/([a-f0-9]{16})/resume",
        write(lambda b, m: launcher.resume(m, score_execution=flag(b, "score_execution"))),
    )
    return launcher
