# Viewer and live replay

This reference follows the subsystem design in the development plan. Implementation modules and tests are planned but are not present in the sparse CP7 launch tree; source links point to existing plan headings until implementation lands.

## Responsibilities and sources of truth

| Part | Responsibility | Source of truth |
|---|---|---|
| Comparison viewer and live replay | Render bundle-backed comparison views and replay event streams through the Live stage, with local and remote serving modes. | [Development plan](../DEV_PLAN.md#10-the-comparison-report-and-viewer) |
| Delivery | Defines implementation stages and acceptance behavior. | [Checkpoint plan](../DEV_PLAN.md#13-delivery-plan-checkpoints) |
| Engineering rules | Defines reference-page structure and link conventions. | [Engineering conventions](../DEV_PLAN.md#12-engineering-conventions) |

## Runtime path

1. The viewer loads a validated bundle and its event stream.
2. Comparison views navigate from leaderboard and matrix summaries to detailed traces and sessions.
3. Live consumes ordered events for animation or replay; static exports use replay mode.

## Constraints and failure behavior

Reduced-motion mode suppresses flight animation while preserving final state. A static export works from `file://`. Remote serving requires the run key, rejects cross-origin writes, and does not expose the gateway port off-box.

- This branch does not yet contain the subsystem implementation or its tests. Future source paths are described in the plan and are not linked as existing files.
- Keep source links on stable headings or symbols; do not use line-number links.

## Verification

Run the planned subsystem check from the repository root:

```sh
pnpm -C web test && pnpm -C web e2e
```

Check all subsystem reference links with:

```sh
uv run python scripts/check_doc_links.py
```
