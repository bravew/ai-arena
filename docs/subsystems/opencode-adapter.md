# OpenCode adapter

## Responsibilities and sources of truth

| Part | Responsibility | Source |
| --- | --- | --- |
| Adapter wiring | Writes a trial-local OpenCode provider, selected model, fixed system prompt and gateway credential to `opencode.json` | [`OpenCodeAdapter.wire`](../../src/arena/agents/opencode.py) |
| Kit installation | Copies instructions and skills to the OpenCode container home, writes MCP entries into `opencode.json`, and uses the resolved kit source root supplied to the adapter | [`OpenCodeAdapter.install_kit`](../../src/arena/agents/opencode.py), [`install_kit`](../../src/arena/kits/install.py) |
| CLI and transcript | Runs headless JSON output, captures OpenCode's stdout JSONL, and parses tool and skill events | [`OpenCodeAdapter.command`](../../src/arena/agents/opencode.py), [`OpenCodeAdapter.sessions`](../../src/arena/agents/opencode.py) |
| CLI pin | Uses the released `ghcr.io/anomalyco/opencode:1.2.18` image | [`OpenCodeAdapter.image`](../../src/arena/agents/opencode.py), [OpenCode v1.2.18 release](https://github.com/anomalyco/opencode/releases/tag/v1.2.18) |

## Runtime path

1. `OpenCodeAdapter.version` reads the installed CLI version from inside the trial box.
2. `wire` writes the selected model as a neutral `arena/<model>` model, configures the OpenAI-compatible provider base URL at `<gateway>/v1`, and stores the `arena-<trial_id>` token in the container config.
3. The command sets `HOME=/home/agent`, invokes `opencode run --format json --agent arena`, and captures the newline-delimited JSON output under `/tmp` in the container.
4. `collect` reads the captured transcript. `sessions` keeps complete JSON records, marks malformed JSONL `partial`, and emits tool calls plus skill events when represented in available records.
5. When a kit is installed, `install_kit` writes instructions to `/home/agent/AGENTS.md`, skills to `/home/agent/.claude/skills/` (a single supported location), and URL or local MCP config into `/home/agent/.config/opencode/opencode.json`.

## Constraints and failure behavior

- OpenCode configuration and the gateway token are written inside the trial container only. The adapter does not access host agent configuration.
- The CLI home must be explicitly set to `/home/agent`; the pinned image defaults to root, while the sandbox provides a writable `/home/agent` tmpfs.
- Kit installation requires an explicit resolved kit source root. The current shared adapter protocol does not provide this root; its runner integration must supply it before production kit runs.
- A missing or malformed config fails `check` and returns a wiring failure through the runner. Failure to collect a transcript propagates as an adapter error; malformed complete-line JSON preserves earlier records and marks the session partial.
- The fixed system prompt is configured for every model. Byte-identical requests and `prompt_parts` across model families require the pinned-container contract test; that test is available but has not been run in this environment.
- OpenCode skill `listed` telemetry depends on structured listing data being present in transcript text. A read of `SKILL.md` and a native `skill` tool call produce `loaded` and `invoked` respectively. No real provider transcript has been recorded here.

## Verification

```sh
uv run pytest tests/agents/test_opencode.py -k 'not pinned_container_contract'
uv run pyright
uv run ruff check
```

The opt-in pinned-container test is `test_pinned_container_contract_records_only_request_metadata_and_prompt_digest`; it has not been run. No real provider smoke task was run. The pinned release was verified from the GitHub release record and the image CLI version was read as `1.2.18`; execution of the CLI against the mock provider remains unverified.
