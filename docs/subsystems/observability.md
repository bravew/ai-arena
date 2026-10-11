# Call

This reference follows the subsystem design in the development plan. Implementation modules and tests are planned but are not present in the sparse CP7 launch tree; source links point to existing plan headings until implementation lands.

## Responsibilities and sources of truth

| Part | Responsibility | Source of truth |
|---|---|---|
| Call ledger, events, and telemetry | Record provider calls and usage, expose ordered run events, assemble session-level evidence, and optionally export OpenTelemetry data. | [Development plan](../DEV_PLAN.md#9-observability) |
| Delivery | Defines implementation stages and acceptance behavior. | [Checkpoint plan](../DEV_PLAN.md#13-delivery-plan-checkpoints) |
| Engineering rules | Defines reference-page structure and link conventions. | [Engineering conventions](../DEV_PLAN.md#12-engineering-conventions) |

## Runtime path

1. Gateway decisions write call and usage records.
2. Runtime events receive monotonically increasing sequence numbers for long-poll consumers.
3. Session views combine calls with native agent transcripts; optional OTLP export ships spans to a collector.

## Constraints and failure behavior

A full OTLP queue drops telemetry and increments its dropped counter without slowing requests. A truncated transcript yields a partial session. Long-poll clients resume from a sequence cursor rather than assuming an empty event stream.

- This branch does not yet contain the subsystem implementation or its tests. Future source paths are described in the plan and are not linked as existing files.
- Keep source links on stable headings or symbols; do not use line-number links.

## Verification

Run the planned subsystem check from the repository root:

```sh
uv run pytest tests/obs
```

Check all subsystem reference links with:

```sh
uv run python scripts/check_doc_links.py
```
