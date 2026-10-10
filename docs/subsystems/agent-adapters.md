# Agent adapters

## Responsibilities and sources of truth

| Part | Responsibility | Source |
| --- | --- | --- |
| Adapter contract | Describes each CLI's protocol, image, version, gateway wiring, kit install, command, native transcript and wiring check | [`AgentAdapter`](../../src/arena/agents/base.py) |
| Agent box | Limits adapter operations to file/config access and command execution inside the trial container | [`AgentBox`](../../src/arena/agents/base.py) |
| Agent CLI runner | Wires and starts the CLI, then classifies whether its first request used the trial token and declared protocol | [`AgentRunner`](../../src/arena/runners/agent_cli.py) |
| Contract harness | Checks a pinned CLI invocation against an injectable provider contract without a real provider | [`ContractHarness`](../../src/arena/agents/testing.py) |
| Session assembler | Merges native turn records, trial-scoped gateway Calls, skill/tool/MCP events, and filesystem diffs; emits session timeline RunEvents | [`assemble_session`](../../src/arena/agents/sessions.py) |
| Codex CLI adapter | Wires a Responses provider, installs kits in Codex paths, and maps rollout JSONL into session timelines | [`CodexCliAdapter`](../../src/arena/agents/codex_cli.py) |

## Runtime path

1. The caller supplies an adapter, task, settings, sandbox box, gateway observer, gateway endpoint, model and trial token.
2. `AgentRunner.run` reads the installed CLI version, wires gateway URL/token and model, installs an optional kit, and starts the adapter command.
3. The runner waits up to `first_step_timeout` for a call bearing the trial token and adapter protocol.
4. With a reached call, the runner collects gateway calls and any native transcript, and returns `reached` with metered flags.
5. Without a reached call, the runner calls `check()`. A failed check returns `errored` with `wiring`; a successful check returns `unmetered` for transcript-only reporting.
6. `CodexCliAdapter.wire` writes `~/.codex/config.toml` with the selected model, a custom `model_provider`, the gateway `base_url`, `wire_api = "responses"`, and an environment variable name for the trial token. `install_kit` copies instructions to `~/.codex/AGENTS.md`, skills below `~/.codex/skills/`, and MCP/settings config into Codex TOML.
7. Codex rollout JSONL is normalized by `CodexCliAdapter._normalize_rollout`, then `assemble_session` parses turns, attaches only Calls with the trial ID, and emits session timeline events.

## Constraints and failure behavior

- Adapter operations receive only the supplied `AgentBox`; adapters must write configuration inside the container and must not access the user's host configuration.
- Exceptions during version detection, wiring, kit installation, command creation, or transcript handling return `errored` with `error_class="wiring"`.
- A missing trial-token call is never treated as a task failure. A successful wiring check marks the result unmetered; a failed check marks it errored.
- Codex provider config contains the gateway URL and the name of the token environment variable. It does not contain the token or a provider credential; subscription state remains on the daemon.
- Codex `check()` parses the generated TOML and verifies its selected model, provider, Responses protocol and gateway token environment variable. Invalid or incomplete TOML returns a failed wiring check.
- Codex rollout `session_meta` and `turn_context` entries identify sessions and turns; response tool-call entries become tool calls. Skill telemetry uses skill names in rollout metadata as `listed`, reads of the installed skill path as `loaded`, and tool calls into a skill path as `invoked`. Codex does not currently emit a dedicated local skill invocation event, so these are inferred from rollout data.
- No Codex real transcript is checked in until one is recorded from an actual CLI run and redacted. Truncated rollout JSONL is retained as a partial session by `assemble_session`; complete gateway calls remain attached.
- Calls are linked only when `Call.trial_id` matches the requested trial. A transcript turn without a linked call is explicitly `unmetered`; malformed JSONL preserves any complete records and produces `status="partial"`.
- Session turns and skill events are emitted with increasing sequence numbers. The assembler returns these events for the run event writer; durable session storage requires a schema change and is not implemented here.

## Verification

```sh
uv run pytest tests/agents/test_codex_cli.py tests/agents/test_contract_harness.py
```
