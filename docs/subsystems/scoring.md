# Scoring

The scorer framework: how a trial's stored artifacts become versioned scores, and how a
re-run writes them without executing the trial again. Deterministic scorers and LLM judges
plug into this framework in their own PRs and extend this page.

## Responsibilities and sources of truth

| Part | Responsibility | Source |
| --- | --- | --- |
| Score | One result for one trial from one `scorer_id@version`: `value`, `normalized` in [0, 1], `passed`, `rationale`, `evidence`. Defined once, in the core model. | [`Score`](../../src/arena/core/models.py) |
| Scorer contract | A scorer has an `id` and `version` and is deterministic for a context. It must return a score for the requested trial and its own identity. | [`Scorer`, `validate_score`](../../src/arena/scorers/base.py) |
| Scorer context | The trial ID, the trial's artifact metadata keyed by path, and read access to the blobs. `read` verifies the digest. | [`ScorerContext`](../../src/arena/scorers/base.py) |
| Artifact index | Which artifacts a trial produced. The blob store is keyed by digest only, so the runner that records artifacts supplies this lookup. | [`ArtifactIndex`](../../src/arena/scorers/base.py) |
| Registry | Resolves a scorer by `id@version`, or by `id` when only one version is registered. Never guesses between versions. | [`ScorerRegistry`](../../src/arena/scorers/registry.py) |
| Aggregation | Weighted mean of `normalized`. A failed gate caps the result at 0.3. Refuses to mix versions of one scorer. | [`aggregate_scores`](../../src/arena/scorers/base.py) |
| Re-scoring | Reads stored artifacts, runs the named scorers, upserts `scores` rows. Never runs a trial. | [`score_run`](../../src/arena/cli_score.py) |
| Blobs | Content-addressed artifact bytes, `artifacts/<sha256>`. | [`ArtifactStore`](../../src/arena/core/cas.py) |
| `scores` table | One row per `(trial, scorer_id, scorer_version)`. | [`001_initial.sql`](../../src/arena/core/migrations/001_initial.sql) |

## Runtime path

1. `arena score <run> --scorer <ref>...` builds the command with
   [`create_score_command`](../../src/arena/cli_score.py) and calls `score_run`.
2. `score_run` checks that the run exists, selects its `succeeded` and `failed` trials,
   and resolves every `--scorer` reference through the registry.
3. For each trial it asks the `ArtifactIndex` for the trial's artifacts and reads each blob
   from the `ArtifactStore`, which checks the content against its digest.
4. Each scorer runs on the trial's `ScorerContext`, and `validate_score` checks the trial and
   scorer identity of what it returned.
5. Only after every trial and scorer succeeded, all rows are written in one transaction.
   A row for the same `(trial, scorer_id, scorer_version)` is replaced.

## Constraints and failure behavior

- Different versions of a scorer are different series. A new version adds rows next to the
  old ones, and `aggregate_scores` raises `ScorerError` if given both.
- A gate must report `passed`. A gate with `passed = None` raises `ScorerError`; it is never
  counted as a pass.
- An unknown run, a run with no finished trials, no `--scorer`, an unknown or ambiguous scorer
  reference, a scorer returning another trial or identity, and a missing, unreadable or
  corrupt artifact blob all raise `ScorerError` before anything is written. Existing scores
  stay as they were.
- Trials with status `errored`, `timeout`, `queued`, `running` or `skipped` are not scored.
- Scoring never calls a model provider or runs a container. Judges that call models come from
  their own sub-issues and go through the gateway.
- The trial-to-artifact link is not in the CP1 schema yet. Until the runner provides an
  `ArtifactIndex`, `arena score` is not wired into the `arena` CLI.

## Verification

```sh
uv run pytest tests/scorers/test_framework.py
```
