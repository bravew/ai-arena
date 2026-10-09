# Runners and sandbox

## Responsibilities and sources of truth

| Part | Responsibility | Source |
| --- | --- | --- |
| Docker sandbox | Creates one internal Docker network and a resource-limited container per trial; exposes only the gateway and trial token; removes container and network at context exit | [`DockerSandbox`](../../src/arena/sandbox/docker.py) |
| Sandbox contract | Validates trial identity, image, command, workspace, gateway URL and token, plus positive resource limits | [`SandboxSpec`](../../src/arena/sandbox/docker.py), [`SandboxLimits`](../../src/arena/sandbox/docker.py) |
| Completion runner | Provides an injectable completion dispatcher and a deterministic local mock adapter | [`run_completion`](../../src/arena/runners/completion.py), [`mock_completion`](../../src/arena/runners/completion.py) |
| Scheduler | Expands deterministic task × contestant × repeat jobs, persists trial state, caches succeeded trials, resumes missing/errored work, and enforces concurrency and subscription rests | [`expand_jobs`](../../src/arena/runners/scheduler.py), [`run_jobs`](../../src/arena/runners/scheduler.py) |
| Run CLI | Loads suites/contestants, prints plans, runs completion jobs, lists runs and resumes by run id | [`plan`](../../src/arena/cli_run.py), [`run`](../../src/arena/cli_run.py), [`list_runs`](../../src/arena/cli_run.py) |

## Runtime path

1. The caller supplies a `SandboxSpec` with the trial workspace, image, command, gateway container and trial-scoped token.
2. `DockerSandbox.run` creates a per-trial bridge network with Docker's `--internal` flag.
3. It connects the gateway container to that network under the gateway URL hostname, then starts the agent container on the same network.
4. The agent container receives the gateway URL and trial token, but no provider API key; its root filesystem is read-only, `/tmp` is a bounded tmpfs and the workspace is the only writable bind mount.
5. Docker enforces CPU, memory and PID limits. `asyncio.timeout` enforces the execution time limit.
6. On normal exit, error, timeout or cancellation, `DockerSandbox.run` removes the agent container, disconnects the gateway and removes the network.
7. `arena run` loads the completion task prompts and dispatches jobs through the scheduler. The current CLI wiring uses the deterministic mock adapter; a gateway adapter can be supplied to `run_jobs` without changing scheduler behavior.

## Constraints and failure behavior

- The trial network is internal and the sandbox API does not support host networking or arbitrary network selection. Only the joined gateway container is made reachable.
- The caller must identify the gateway Docker container separately from the gateway URL. The gateway URL hostname is used as its network alias.
- Provider keys stay outside the container. The only credential passed to a trial container is `ARENA_GATEWAY_TOKEN`.
- A missing Docker daemon, invalid image, network failure or container creation failure raises `DockerCommandError`; the context still attempts cleanup for resources it created.
- A wall-time limit raises `TimeoutError` and removes the container and network. Cancellation propagates after shielded teardown.
- A successful deterministic trial id is treated as a cache hit on rerun. `--dry-run` returns the planned count before opening the store. `--resume` reruns non-succeeded trial rows for the selected run id.
- The scheduler catches `SubscriptionRest`, reports the remaining seconds through its countdown callback and holds the job until reset. Other jobs continue subject to global and per-contestant concurrency limits.
- If a trial fails, the scheduler waits for sibling trials to finish, marks the run `errored`, and re-raises the first failure. Resume retries non-succeeded trials; cancellation leaves the run resumable.
- The CLI currently dispatches to the mock adapter, so it does not call a provider or the gateway. Non-mock gateway execution and price-informed estimates require the gateway dispatcher and matching model catalog entries to be wired in.
- Workspace files are writable for agent output. The caller must provide a trial-scoped directory; this backend does not validate workspace contents or impose a disk quota.

## Verification

```sh
uv run pytest tests/sandbox
```

The fake Docker interface exercises command configuration and teardown without requiring a Docker daemon. A real container connectivity test still needs a Linux Docker runtime and gateway image/container; it is not part of the unit suite yet.
