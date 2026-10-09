# Statistics aggregation

## Responsibilities and sources of truth

| Part | Responsibility | Source |
| --- | --- | --- |
| Trial score validation and exclusions | Validate trial score input; exclude swapped, unmetered, and kit-unapplied trials from headline aggregates while counting each reason and each excluded trial | [`TrialScore`, `ExcludedTrials`, `aggregate_scores`](../../src/arena/stats/aggregate.py) |
| Task and suite aggregation | Compute per-task means and equally weighted suite estimates and confidence intervals; compute pass@k and pass^k where pass labels are present | [`TaskAggregate`, `ContestantAggregate`, `aggregate_scores`](../../src/arena/stats/aggregate.py) |
| Paired comparison | Compare shared task/repeat pairs, report task win/tie/loss and a paired interval; interval crossing zero means no detectable difference | [`Aggregation.compare`, `HeadToHead`](../../src/arena/stats/aggregate.py) |
| Cluster bootstrap | Resample tasks and repeats within sampled tasks and calculate a percentile interval with a deterministic seed | [`cluster_bootstrap_ci`](../../src/arena/stats/bootstrap.py) |

## Runtime path

1. `aggregate_scores` accepts `TrialScore` objects or mappings and validates mapping field names and types before constructing trial records.
2. It rejects invalid scores, non-positive attempt numbers, and duplicate contestant/task/attempt combinations.
3. Trials flagged `swapped`, `unmetered`, or `kit_unapplied` are omitted from task and suite headlines. Each active reason is incremented; an overlapping trial increments the unique excluded total once.
4. Eligible trial scores are averaged within each task. The suite estimate is the arithmetic mean of those task means, so tasks have equal weight regardless of repeat count.
5. For each task with complete pass labels, pass@k is the unbiased probability of at least one pass in `k` draws without replacement, `1 - C(n-c,k)/C(n,k)`. pass^k is `(c/n)**k`, the empirical chance all `k` independent repetitions pass. `k` defaults to the eligible repeat count and can be set with `pass_k`; `k` cannot exceed the count. If any pass label is missing, both are unavailable.
6. The bootstrap draws tasks with replacement, then repeats within each selected task with replacement. Percentile endpoints form the confidence interval. A fixed default seed makes repeat reports reproducible.
7. `Aggregation.compare` intersects eligible tasks and attempt numbers, bootstraps paired score differences, and classifies each shared task as a win, tie, or loss. If there are no shared tasks/repeats, it raises `ValueError`.

## Constraints and failure behavior

- Scores must be finite and within `[0, 1]`; identifiers must be non-empty; attempt and exclusion flags have strict types.
- Mapping input rejects missing required fields and unknown fields with `ValueError`.
- Exclusion footnotes report counts by reason; when multiple reasons apply to one trial, reason counts overlap but `total` counts that trial once.
- A contestant with no eligible trials has no suite estimate or interval.
- The percentile bootstrap is a simple two-stage cluster bootstrap; it does not model scorer uncertainty or missing task populations.
- pass@k and pass^k are unavailable for a task unless every eligible trial has a boolean `passed` label.

## Verification

```sh
uv run pytest tests/stats/test_aggregate.py
```
