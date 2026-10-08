# Deterministic

This reference follows the subsystem design in the development plan. Implementation modules and tests are planned but are not present in the sparse CP7 launch tree; source links point to existing plan headings until implementation lands.

## Responsibilities and sources of truth

| Part | Responsibility | Source of truth |
|---|---|---|
| Deterministic scoring and judges | Run execution, constraint, reference, and visual scorers; evaluate rubrics and pairwise comparisons; preserve evidence and judge spend. | [Development plan](../DEV_PLAN.md#8-scoring-methodology) |
| Delivery | Defines implementation stages and acceptance behavior. | [Checkpoint plan](../DEV_PLAN.md#13-delivery-plan-checkpoints) |
| Engineering rules | Defines reference-page structure and link conventions. | [Engineering conventions](../DEV_PLAN.md#12-engineering-conventions) |

## Runtime path

1. Scorers consume completed trial artifacts and task-specific references.
2. Rubric judges evaluate outputs; pairwise judging swaps order to measure position effects.
3. Scores and evidence are stored independently of trial execution so scoring can be repeated.

## Constraints and failure behavior

A missing or invalid artifact is a scoring error, not a zero-valued success. Judge calls are attributed separately as `purpose: judge`; changing a rubric version creates a separate score series.

- This branch does not yet contain the subsystem implementation or its tests. Future source paths are described in the plan and are not linked as existing files.
- Keep source links on stable headings or symbols; do not use line-number links.

## Verification

Run the planned subsystem check from the repository root:

```sh
uv run pytest tests/scorers tests/judges
```

Check all subsystem reference links with:

```sh
uv run python scripts/check_doc_links.py
```
