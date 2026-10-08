# Statistics,

This reference follows the subsystem design in the development plan. Implementation modules and tests are planned but are not present in the sparse CP7 launch tree; source links point to existing plan headings until implementation lands.

## Responsibilities and sources of truth

| Part | Responsibility | Source of truth |
|---|---|---|
| Statistics, ratings, and reports | Aggregate scores, quantify uncertainty, compare contestants, compute ratings and Pareto frontiers, and export reports and bundles. | [Development plan](../DEV_PLAN.md#13-delivery-plan-checkpoints) |
| Delivery | Defines implementation stages and acceptance behavior. | [Checkpoint plan](../DEV_PLAN.md#13-delivery-plan-checkpoints) |
| Engineering rules | Defines reference-page structure and link conventions. | [Engineering conventions](../DEV_PLAN.md#12-engineering-conventions) |

## Runtime path

1. Aggregation reads trial and score records from a run.
2. Paired methods, bootstrap intervals, ratings, and run diffs produce comparison summaries.
3. The report and export bundle include the data needed by the viewer and downstream analysis.

## Constraints and failure behavior

Synthetic fixtures with planted ground truth verify recovery and false-positive behavior. `swapped` and `unmetered` trials are excluded from headline metrics and disclosed; observational kit splits are labeled as observational.

- This branch does not yet contain the subsystem implementation or its tests. Future source paths are described in the plan and are not linked as existing files.
- Keep source links on stable headings or symbols; do not use line-number links.

## Verification

Run the planned subsystem check from the repository root:

```sh
uv run pytest tests/stats
```

Check all subsystem reference links with:

```sh
uv run python scripts/check_doc_links.py
```
