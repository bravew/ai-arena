# Runners and sandbox

## Responsibilities and sources of truth

| Part | Responsibility | Source |
| --- | --- | --- |
| Docker sandbox | Creates one internal Docker network and a resource-limited container per trial; exposes only the gateway and trial token; removes container and network at context exit | [`DockerSandbox`](../../src/arena/sandbox/docker.py) |
| Sandbox contract | Validates trial identity, image, command, workspace, gateway URL and token, plus positive resource limits | [`SandboxSpec`](../../src/arena/sandbox/docker.py), [`SandboxLimits`](../../src/arena/sandbox/docker.py) |

## Runtime path

1. The caller supplies a `SandboxSpec` with the trial workspace, image, command, gateway container and trial-scoped token.
2. `DockerSandbox.run` creates a per-trial bridge network with Docker's `--internal` flag.
3. It connects the gateway container to that network under the gateway URL hostname, then starts the agent container on the same network.
4. The agent container receives the gateway URL and trial token, but no provider API key; its root filesystem is read-only, `/tmp` is a bounded tmpfs and the workspace is the only writable bind mount.
5. Docker enforces CPU, memory and PID limits. `asyncio.timeout` enforces the execution time limit.
6. On normal exit, error, timeout or cancellation, `DockerSandbox.run` removes the agent container, disconnects the gateway and removes the network.

## Constraints and failure behavior

- The trial network is internal and the sandbox API does not support host networking or arbitrary network selection. Only the joined gateway container is made reachable.
- The caller must identify the gateway Docker container separately from the gateway URL. The gateway URL hostname is used as its network alias.
- Provider keys stay outside the container. The only credential passed to a trial container is `ARENA_GATEWAY_TOKEN`.
- A missing Docker daemon, invalid image, network failure or container creation failure raises `DockerCommandError`; the context still attempts cleanup for resources it created.
- A wall-time limit raises `TimeoutError` and removes the container and network. Cancellation propagates after shielded teardown.
- Workspace files are writable for agent output. The caller must provide a trial-scoped directory; this backend does not validate workspace contents or impose a disk quota.

## Verification

```sh
uv run pytest tests/sandbox
```

The fake Docker interface exercises command configuration and teardown without requiring a Docker daemon. A real container connectivity test still needs a Linux Docker runtime and gateway image/container; it is not part of the unit suite yet.
