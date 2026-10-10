# Runners and sandbox

## Responsibilities and sources of truth

| Part | Responsibility | Source |
| --- | --- | --- |
| Docker sandbox | Creates one internal Docker network and a resource-limited container per trial; `start` leaves it idle for adapter wiring and `DockerAgentBox` runs commands and copies files through `docker exec`; removes container and network at context exit | [`DockerSandbox`](../../src/arena/sandbox/docker.py), [`DockerAgentBox`](../../src/arena/sandbox/agent_box.py) |
| Sandbox contract | Validates trial identity, image, command, workspace, gateway URL and token, plus positive resource limits | [`SandboxSpec`](../../src/arena/sandbox/docker.py), [`SandboxLimits`](../../src/arena/sandbox/docker.py) |
| Completion runner | Provides an injectable completion dispatcher and a deterministic local mock adapter | [`run_completion`](../../src/arena/runners/completion.py), [`mock_completion`](../../src/arena/runners/completion.py) |
| Scheduler | Expands deterministic task × contestant × repeat jobs, persists trial state, caches succeeded trials, resumes missing/errored work, and enforces concurrency and subscription rests | [`expand_jobs`](../../src/arena/runners/scheduler.py), [`run_jobs`](../../src/arena/runners/scheduler.py) |
| Orchestration runner | Composes best-of-n and planner-executor calls, loads `custom:<path>` modules, records child spans under the orchestration span, and rolls up child cost and cache metrics | [`Orchestrator`](../../src/arena/runners/orchestration.py), [`best_of_n`](../../src/arena/runners/orchestration.py), [`planner_executor`](../../src/arena/runners/orchestration.py), [`prepare_contestant`](../../src/arena/runners/orchestration.py), [`load_custom`](../../src/arena/runners/orchestration.py), [`roll_up`](../../src/arena/runners/orchestration.py) |
| Run CLI | Loads suites/contestants, prints plans, runs completion jobs, lists runs and resumes by run id | [`plan`](../../src/arena/cli_run.py), [`run`](../../src/arena/cli_run.py), [`list_runs`](../../src/arena/cli_run.py) |

## Runtime path

1. The caller supplies a `SandboxSpec` with the trial workspace, image, command, gateway container and trial-scoped token.
2. `DockerSandbox.run` creates a per-trial bridge network with Docker's `--internal` flag.
3. It connects the gateway container to that network under the gateway URL hostname, then starts the agent container on the same network.
4. The agent container receives the gateway URL and trial token, but no provider API key; its root filesystem is read-only, `/tmp` is a bounded tmpfs and the workspace is the only writable bind mount.
5. Docker enforces CPU, memory and PID limits. `asyncio.timeout` enforces the execution time limit.
6. On normal exit, error, timeout or cancellation, the sandbox removes the agent container, disconnects the gateway and removes the network.
7. Agent trials use `DockerSandbox.start`: it starts the limited container idle (`sleep infinity`) with `/home/agent` as a bounded tmpfs for adapter config. `DockerAgentBox` provides `write_text`, `read_text` and `execute` through `docker exec`; file content goes over stdin, never argv. The adapter wires the agent and installs the kit before the runner starts its command, so a wiring failure never starts the agent. The synchronous box matches the runner's `asyncio.to_thread` calls.
8. `arena run` loads completion task prompts and dispatches jobs through the scheduler. The current CLI wiring uses the deterministic mock adapter; a gateway adapter can be supplied to `run_jobs` without changing scheduler behavior.
9. `prepare_contestant` hashes custom strategy source into contestant parameters before trial expansion, so a source change gets a new deterministic contestant and trial cache identity. `Orchestrator.run` then selects the strategy. Best-of-n accepts at most 64 samples and runs them in batches of four before selection with a judge call, a supplied test check, or the first sample; planner-executor makes a plan call followed by an execution call; custom strategies load `async def run(context)` from `custom:<path>`.
10. Each child call passes a `CallContext` with its trial id, span id, parent span id, name and orchestration purpose to the dispatcher. The returned root span and child spans roll up cost, cache hits and unknown prices into `OrchestrationResult`, a `CompletionResult` the scheduler can record.

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
- Orchestration fails on an unknown strategy or option, malformed judge selection, unavailable test check, unreadable/invalid custom module or child call failure. Child dispatchers receive the parent span id; the database schema does not yet persist spans, so the caller must write the returned span tree and use `CallContext` to tag child `Call` records.
- A custom module is user Python code loaded into the arena process. Call `prepare_contestant` before expanding trial jobs or checking the scheduler cache; it binds the module's SHA-256 digest into `Contestant.params` and therefore contestant/trial identity. The runner checks the source again before execution and fails if it changed after planning. `OrchestrationResult.strategy_digest` also records the digest.
- Best-of-n requires `1 <= n <= 64` and dispatches at most four samples concurrently in sequential batches. Invalid bounds fail before any child call starts.

- `docker run --mount` accepts only `key=value` fields. The workspace mount used to carry a bare `rw` field, which a real Docker daemon rejects; #27 fixed it and `tests/sandbox/test_docker.py` pins the accepted mount format.
- URL MCP servers are registered in the daemon-side proxy with their resolved headers. The container receives only the proxy URL and its trial token. Redirects are returned to the caller, never followed with a daemon-held header.
- This CP3 branch does not yet include the CP2 gateway server, so `McpProxy` is transport-agnostic and has no HTTP route mounted here. The epic sync must mount `/mcp/<name>` behind trial-token auth and send `McpSpan` events to the call/session span store.
- The adapter supplies the agent-specific MCP settings serializer through `KitTarget`; this module does not claim one JSON/TOML layout fits every agent. Command MCP servers are represented for serializers but still run inside the container from a pinned package or image.
- Git-backed skills are refused by `install_kit` until a pinned checkout is supplied to the installer. Local skills are copied and their bytes checked in the container.

## Verification

```sh
uv run pytest tests/kits tests/gateway tests/agents tests/sandbox
```

The MCP proxy uses an injected upstream transport; a standard-library buffered HTTP implementation is included for small runs and tests.
