# Agent adapters

## Responsibilities and sources of truth

| Part | Responsibility | Source |
| --- | --- | --- |
| Adapter contract | Describes each CLI's protocol, image, version, gateway wiring, kit install, command, native transcript and wiring check | [`AgentAdapter`](../../src/arena/agents/base.py) |
| Agent box | Limits adapter operations to file/config access and command execution inside the trial container | [`AgentBox`](../../src/arena/agents/base.py) |
| Agent CLI runner | Wires and starts the CLI, then classifies whether its first request used the trial token and declared protocol | [`AgentRunner`](../../src/arena/runners/agent_cli.py) |
| Contract harness | Checks a pinned CLI invocation against an injectable provider contract without a real provider | [`ContractHarness`](../../src/arena/agents/testing.py) |

## Runtime path

1. The caller supplies an adapter, task, settings, sandbox box, gateway observer, gateway endpoint, model and trial token.
2. `AgentRunner.run` reads the installed CLI version, wires gateway URL/token and model, installs an optional kit, and starts the adapter command.
3. The runner waits up to `first_step_timeout` for a call bearing the trial token and adapter protocol.
4. With a reached call, the runner collects gateway calls and any native transcript, and returns `reached` with metered flags.
5. Without a reached call, the runner calls `check()`. A failed check returns `errored` with `wiring`; a successful check returns `unmetered` for transcript-only reporting.

## Constraints and failure behavior

- Adapter operations receive only the supplied `AgentBox`; adapters must write configuration inside the container and must not access the user's host configuration.
- Exceptions during version detection, wiring, kit installation, command creation, or transcript handling return `errored` with `error_class="wiring"`.
- A missing trial-token call is never treated as a task failure. A successful wiring check marks the result unmetered; a failed check marks it errored.
- The contract harness uses a deterministic mock provider and asserts the first request's token and protocol. Its local contract tests do not require Docker or a real provider.
- `AgentRunner` consumes a `ReachedGateway` observer and `AgentBox` operations. The Docker lifecycle adapter and live gateway query implementation are supplied by the caller.

## Verification

```sh
uv run pytest tests/agents/test_contract_harness.py
```
