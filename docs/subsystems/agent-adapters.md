# Agent adapters

## Responsibilities and sources of truth

| Part | Responsibility | Source |
| --- | --- | --- |
| Adapter contract | Describes each CLI's protocol, image, version, gateway wiring, kit install, command, native transcript and wiring check | [`AgentAdapter`](../../src/arena/agents/base.py) |
| Agent box | Limits adapter operations to file/config access and command execution inside the trial container | [`AgentBox`](../../src/arena/agents/base.py) |
| Agent CLI runner | Wires and starts the CLI, then classifies whether its first request used the trial token and declared protocol | [`AgentRunner`](../../src/arena/runners/agent_cli.py) |
| Contract harness | Checks a pinned CLI invocation against an injectable provider contract without a real provider | [`ContractHarness`](../../src/arena/agents/testing.py) |
| Session assembler | Merges native turn records, trial-scoped gateway Calls, skill/tool/MCP events, and filesystem diffs; emits session timeline RunEvents | [`assemble_session`](../../src/arena/agents/sessions.py) |
| Aider adapter | Wires the pinned Aider CLI to the chat gateway, loads conventions with `--read`, refuses skills/MCP, and collects `.aider.chat.history.md` | [`AiderAdapter`](../../src/arena/agents/aider.py) |

## Runtime path

1. The caller supplies an adapter, task, settings, sandbox box, gateway observer, gateway endpoint, model and trial token.
2. `AgentRunner.run` reads the installed CLI version, wires gateway URL/token and model, installs an optional kit, and starts the adapter command.
3. The runner waits up to `first_step_timeout` for a call bearing the trial token and adapter protocol.
4. With a reached call, the runner collects gateway calls and any native transcript, and returns `reached` with metered flags.
5. Without a reached call, the runner calls `check()`. A failed check returns `errored` with `wiring`; a successful check returns `unmetered` for transcript-only reporting.
6. `assemble_session` parses newline-delimited native records into turns, assigns only Calls with the trial ID, attaches the filesystem diff to the final turn, and creates `session_turn` plus per-skill `skill_event` RunEvents.

## Constraints and failure behavior

- Adapter operations receive only the supplied `AgentBox`; adapters must write configuration inside the container and must not access the user's host configuration.
- Exceptions during version detection, wiring, kit installation, command creation, or transcript handling return `errored` with `error_class="wiring"`.
- A missing trial-token call is never treated as a task failure. A successful wiring check marks the result unmetered; a failed check marks it errored.
- The contract harness uses a deterministic mock provider and asserts the first request's token and protocol. Its local contract tests do not require Docker or a real provider.
- `AgentRunner` consumes a `ReachedGateway` observer and `AgentBox` operations. The Docker lifecycle adapter and live gateway query implementation are supplied by the caller.
- Calls are linked only when `Call.trial_id` matches the requested trial. A transcript turn without a linked call is explicitly `unmetered`; malformed JSONL preserves any complete records and produces `status="partial"`. A malformed first record can still be represented by gateway calls as a partial session.
- Session turns and skill events are emitted with increasing sequence numbers. The assembler returns these events for the run event writer; durable session storage requires a schema change and is not implemented here.

## Verification

```sh
uv run pytest tests/agents/test_contract_harness.py
```
