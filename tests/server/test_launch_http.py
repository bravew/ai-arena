from __future__ import annotations

import json
import sys
import threading
import time
import urllib.error
import urllib.request
from collections.abc import Generator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import pytest
import yaml

from arena.server.app import create_server

PROVIDERS = """\
providers:
  - id: mock
    kind: mock
  - id: anthropic-max
    keys: []
    subscription: {vendor: claude, plan: max, import: own-sign-in, runtime: local-cli}
  - id: openai
    apis: {responses: https://api.openai.com/v1}
    keys: [{id: main, env: OPENAI_API_KEY}]
"""
CATALOG = """\
price_version: test
models:
  - ref: mock/echo
    price_per_mtok: {in: 0, out: 0}
  - ref: anthropic-max/claude-sonnet-5-5
    protocols: [anthropic]
    pricing: subscription
  - ref: openai/gpt-metered
    protocols: [responses]
    price_per_mtok: {in: 1, out: 2}
"""
TASK = """\
id: t.one
version: 1
kind: codegen
prompt_file: prompt.md
scorers: [{id: execution, weight: 1.0, command: "true"}]
created_at: "2026-10-08"
"""
FAKE = """\
import os, sys, time
args = sys.argv[1:]
assert args[0] == "run", args
open(os.environ["FAKE_ARGV"], "w").write(" ".join(args))
print("run_id=fakerun1", flush=True)
mode = os.environ.get("FAKE_MODE", "ok")
if mode == "fail":
    print("error: provider rejected the request sk-abc123secret", file=sys.stderr, flush=True)
    sys.exit(3)
if mode == "hang":
    time.sleep(60)
"""


@contextmanager
def launch_server(
    tmp_path: Path, mode: str = "ok", *, executable_path: Path | None = None
) -> Generator[tuple[str, Path, str]]:
    root = tmp_path / "project"
    (root / "suites" / "tiny" / "tasks" / "one").mkdir(parents=True)
    (root / "suites" / "tiny" / "tasks" / "one" / "task.yaml").write_text(TASK)
    (root / "contestants" / "sets").mkdir(parents=True)
    (root / "contestants" / "mock.yaml").write_text(
        "id_label: mock-a\nmodel: mock/echo\nscaffold: none\norchestration: single\n"
    )
    (root / "contestants" / "sets" / "metered.yaml").write_text(
        "matrix:\n  model: [openai/gpt-metered]\n  scaffold: [none]\norchestration: single\n"
    )
    providers, catalog = tmp_path / "providers.yaml", tmp_path / "models.yaml"
    providers.write_text(PROVIDERS)
    catalog.write_text(CATALOG)
    script = tmp_path / "fake_arena.py"
    script.write_text(FAKE)
    argv_file = tmp_path / "argv.txt"
    server = create_server(
        "127.0.0.1",
        0,
        tmp_path / "home",
        provider_config=providers,
        provider_catalog=catalog,
        provider_environ={
            "ARENA_DISCOVER_SIGNINS": "0",
            "PATH": str(executable_path) if executable_path is not None else "/usr/bin:/bin",
            "FAKE_ARGV": str(argv_file),
            "FAKE_MODE": mode,
        },
        project_root=root,
        launch_command=(sys.executable, str(script)),
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}", root, str(argv_file)
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)


def call(
    base: str, path: str, body: dict[str, Any] | None = None, *, token: str = ""
) -> tuple[int, dict[str, Any]]:
    headers = {"Content-Type": "application/json"}
    if token:
        headers["X-Arena-CSRF"] = token
        headers["Origin"] = base
    req = urllib.request.Request(
        base + path, data=json.dumps(body).encode() if body is not None else None, headers=headers
    )
    try:
        response = urllib.request.urlopen(req, timeout=5)
    except urllib.error.HTTPError as error:
        return error.code, json.loads(error.read())
    with response:
        return response.status, json.loads(response.read())


def csrf(base: str) -> str:
    return call(base, "/api/provider-settings")[1]["csrf_token"]


def wait_for(base: str, job_id: str, states: set[str]) -> dict[str, Any]:
    deadline = time.monotonic() + 8
    job: dict[str, Any] = {}
    while time.monotonic() < deadline:
        _, job = call(base, f"/api/launch/runs/{job_id}")
        if job["state"] in states:
            return job
        time.sleep(0.05)
    raise AssertionError(f"run never reached {states}: {job}")


def test_options_list_suites_sets_and_only_subscription_models(tmp_path: Path) -> None:
    with launch_server(tmp_path) as (base, _, _):
        status, options = call(base, "/api/launch/options")
        assert status == 200
        assert options["suites"] == [{"id": "tiny", "tasks": 1, "kinds": ["codegen"]}]
        assert [m["ref"] for m in options["models"]] == ["anthropic-max/claude-sonnet-5-5"]
        names = {s["name"]: s for s in options["sets"]}
        assert names["mock"]["contestants"][0]["kind"] == "mock"
        assert names["sets/metered"]["contestants"][0]["runnable"] is False
        assert options["limits"]["max_trials"] == 200


def test_saving_a_set_validates_models_and_refuses_silent_overwrite(tmp_path: Path) -> None:
    with launch_server(tmp_path) as (base, root, _):
        token = csrf(base)
        body = {"name": "pair", "models": ["anthropic-max/claude-sonnet-5-5"]}
        assert call(base, "/api/launch/sets", body)[0] == 403
        status, saved = call(base, "/api/launch/sets", body, token=token)
        assert (status, saved["name"]) == (200, "sets/pair")
        document = yaml.safe_load((root / "contestants" / "sets" / "pair.yaml").read_text())
        assert document["matrix"]["model"] == ["anthropic-max/claude-sonnet-5-5"]
        assert call(base, "/api/launch/sets", body, token=token)[0] == 422
        assert call(base, "/api/launch/sets", {**body, "overwrite": True}, token=token)[0] == 200
        for bad in (
            {"name": "../escape", "models": body["models"]},
            {"name": "x", "models": ["openai/gpt-metered"]},
            {"name": "x", "models": []},
            {"name": "x", "models": [body["models"][0]] * 2},
        ):
            assert call(base, "/api/launch/sets", bad, token=token)[0] == 422
        assert not (root.parent / "escape.yaml").exists()


def test_plan_counts_trials_and_blocks_metered_contestants(tmp_path: Path) -> None:
    with launch_server(tmp_path) as (base, _, _):
        token = csrf(base)
        _, plan = call(
            base, "/api/launch/plan", {"suite": "tiny", "set": "mock", "repeats": 3}, token=token
        )
        assert (plan["trials"], plan["startable"], plan["subscription_trials"]) == (3, True, 0)
        assert plan["scorers"] == ["execution"]
        _, plan = call(
            base,
            "/api/launch/plan",
            {"suite": "tiny", "set": "sets/metered", "repeats": 1},
            token=token,
        )
        assert plan["startable"] is False
        assert "run them in the CLI" in plan["blockers"][0]
        for bad in (
            {"suite": "tiny", "set": "mock", "repeats": 0},
            {"suite": "tiny", "set": "mock", "repeats": 11},
            {"suite": "tiny", "set": "mock", "repeats": True},
            {"suite": "../tiny", "set": "mock", "repeats": 1},
            {"suite": "tiny", "set": "../mock", "repeats": 1},
            {"suite": "missing", "set": "mock", "repeats": 1},
        ):
            assert call(base, "/api/launch/plan", bad, token=token)[0] == 422


def test_start_runs_the_cli_without_a_shell_and_reports_the_run_id(tmp_path: Path) -> None:
    with launch_server(tmp_path) as (base, root, argv_file):
        token = csrf(base)
        body = {"suite": "tiny", "set": "mock", "repeats": 2, "score_execution": False}
        assert call(base, "/api/launch/runs", body)[0] == 403
        status, job = call(base, "/api/launch/runs", body, token=token)
        assert status == 200, job
        done = wait_for(base, job["id"], {"succeeded", "failed"})
        assert (done["state"], done["run_id"], done["trials"]) == ("succeeded", "fakerun1", 2)
        argv = Path(argv_file).read_text().split()
        assert argv[:2] == ["run", str(root / "suites" / "tiny")]
        assert argv[argv.index("--repeats") + 1] == "2"
        assert "--score-execution" not in argv
        assert call(base, "/api/launch/runs")[1]["jobs"][0]["id"] == job["id"]


def test_failed_run_shows_a_redacted_error_and_can_be_resumed(tmp_path: Path) -> None:
    with launch_server(tmp_path, mode="fail") as (base, _, argv_file):
        token = csrf(base)
        body = {"suite": "tiny", "set": "mock", "repeats": 1, "score_execution": False}
        _, job = call(base, "/api/launch/runs", body, token=token)
        failed = wait_for(base, job["id"], {"failed"})
        assert failed["exit_code"] == 3
        assert "sk-abc123secret" not in json.dumps(failed)
        assert failed["message"].startswith("error:")
        status, again = call(base, f"/api/launch/runs/{job['id']}/resume", {}, token=token)
        assert status == 200, again
        wait_for(base, again["id"], {"failed"})
        argv = Path(argv_file).read_text().split()
        assert argv[argv.index("--resume") + 1] == "fakerun1"


def test_one_run_at_a_time_and_cancel_stops_the_child(tmp_path: Path) -> None:
    with launch_server(tmp_path, mode="hang") as (base, _, _):
        token = csrf(base)
        body = {"suite": "tiny", "set": "mock", "repeats": 1, "score_execution": False}
        _, job = call(base, "/api/launch/runs", body, token=token)
        status, refused = call(base, "/api/launch/runs", body, token=token)
        assert status == 422 and "already going" in refused["error"]
        assert call(base, "/api/launch/options")[1]["active_job"] == job["id"]
        assert call(base, f"/api/launch/runs/{job['id']}/cancel", {}, token=token)[0] == 200
        assert wait_for(base, job["id"], {"cancelled"})["state"] == "cancelled"


@pytest.mark.parametrize("docker_available", [False, True])
def test_score_execution_needs_docker(tmp_path: Path, docker_available: bool) -> None:
    executable_path = tmp_path / "bin"
    executable_path.mkdir()
    if docker_available:
        docker = executable_path / "docker"
        docker.write_text("#!/bin/sh\nexit 0\n")
        docker.chmod(0o755)
    with launch_server(tmp_path, executable_path=executable_path) as (base, _, argv_file):
        status, options = call(base, "/api/launch/options")
        assert status == 200 and options["docker_available"] is docker_available
        body = {"suite": "tiny", "set": "mock", "repeats": 1, "score_execution": True}
        status, result = call(base, "/api/launch/runs", body, token=csrf(base))
        if docker_available:
            assert status == 200, result
            assert wait_for(base, result["id"], {"succeeded", "failed"})["state"] == "succeeded"
            assert "--score-execution" in Path(argv_file).read_text().split()
        else:
            assert status == 422 and "Docker" in result["error"]
            assert not Path(argv_file).exists()


def test_unknown_or_malformed_run_ids_are_not_found(tmp_path: Path) -> None:
    with launch_server(tmp_path) as (base, _, _):
        assert call(base, "/api/launch/runs/0123456789abcdef")[0] == 404
        with pytest.raises(urllib.error.HTTPError):
            urllib.request.urlopen(base + "/api/launch/runs/../x", timeout=3)
