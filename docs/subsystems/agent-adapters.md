# Agent

This reference follows the subsystem design in the development plan. Implementation modules and tests are planned but are not present in the sparse CP7 launch tree; source links point to existing plan headings until implementation lands.

## Responsibilities and sources of truth

| Part | Responsibility | Source of truth |
|---|---|---|
| Agent CLI adapters | Provide a common adapter contract for wiring a CLI, installing kits in its container paths, collecting its native transcript, and assembling sessions. | [Development plan](../DEV_PLAN.md#6-agents-and-scaffolds) |
| Delivery | Defines implementation stages and acceptance behavior. | [Checkpoint plan](../DEV_PLAN.md#13-delivery-plan-checkpoints) |
| Engineering rules | Defines reference-page structure and link conventions. | [Engineering conventions](../DEV_PLAN.md#12-engineering-conventions) |

## Runtime path

1. Preflight checks adapter and CLI compatibility.
2. The adapter installs the selected kit in the container and records refused items.
3. The trial runs through the gateway; the adapter captures the native transcript.
4. Session assembly combines transcript turns, gateway calls, and filesystem changes.

## Constraints and failure behavior

Broken wiring is classified as `errored`/`wiring`, not a task failure. Truncated transcripts produce partial sessions. Host agent configuration is never modified; refused kit items remain visible as unapplied.

- This branch does not yet contain the subsystem implementation or its tests. Future source paths are described in the plan and are not linked as existing files.
- Keep source links on stable headings or symbols; do not use line-number links.

## Verification

Run the planned subsystem check from the repository root:

```sh
uv run pytest tests/agents
```

Check all subsystem reference links with:

```sh
uv run python scripts/check_doc_links.py
```
