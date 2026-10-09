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
| Artifact index | Which artifacts a trial produced. The blob store is keyed by digest only, so the lookup is a protocol. `arena score` uses `StoreArtifactIndex`, which reads the trial's `trial_artifacts` rows; a row it cannot parse is a `ScorerError`. | [`ArtifactIndex`](../../src/arena/scorers/base.py), [`StoreArtifactIndex`](../../src/arena/cli_score.py) |
| Registry | Resolves a scorer by `id@version`, or by `id` when only one version is registered. Never guesses between versions. `default_registry` is what `arena score` offers: `visual@1` only, because execution scorers need a sandbox and judges need the gateway. | [`ScorerRegistry`, `default_registry`](../../src/arena/scorers/registry.py) |
| Aggregation | Weighted mean of `normalized`. A failed gate caps the result at 0.3. Refuses to mix versions of one scorer. | [`aggregate_scores`](../../src/arena/scorers/base.py) |
| Re-scoring | Reads stored artifacts, runs the named scorers, upserts `scores` rows. Never runs a trial. `arena score` is the command around it. | [`score_run`, `create_score_command`](../../src/arena/cli_score.py), [`arena score`](../../src/arena/cli.py) |
| Blobs | Content-addressed artifact bytes, `artifacts/<sha256>`. | [`ArtifactStore`](../../src/arena/core/cas.py) |
| `scores` table | One row per `(trial, scorer_id, scorer_version)`. | [`001_initial.sql`](../../src/arena/core/migrations/001_initial.sql) |
| `trial_artifacts` table | One row per `(trial, path)` with its digest, MIME type and render hint. | [`002_trial_artifacts.sql`](../../src/arena/core/migrations/002_trial_artifacts.sql) |

## Runtime path

1. `arena score <run> --scorer <ref>... [--home <dir>]` is built by
   [`create_score_command`](../../src/arena/cli_score.py). The home is `--home`, else
   `ARENA_HOME`, else the current directory; the store is `<home>/arena.db` and the blobs are
   `<home>/artifacts`. The command opens the store and calls `score_run` with a
   `StoreArtifactIndex` over it.
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
- `arena score` prints `error: <message>` to stderr and exits 1 for each `ScorerError` above,
  for a `StoreError` (an unreadable store), and when `<home>/arena.db` does not exist. It never
  creates a store. A `trial_artifacts` row that does not parse is a `ScorerError` too, not a
  shorter artifact list. A trial with no rows has no artifacts.
- Re-scoring with the same `scorer_id@version` replaces that row; a new version adds a second
  row next to it.

## Visual scorer

| Part | Responsibility | Source |
| --- | --- | --- |
| Capture protocol | A browser captures a page at a named viewport and returns PNG bytes and console errors; tests inject a fake. | [`PageBrowser`, `PageCapture`](../../src/arena/scorers/visual.py) |
| Playwright adapter | Lazily imports optional Playwright, launches Chromium, opens the viewer-style frame host and returns a PNG and console errors. | [`PlaywrightBrowser`](../../src/arena/scorers/visual.py) |
| Untrusted HTML serving | Serves the frame host and selected HTML artifact from separate loopback origins. The host uses `<iframe sandbox="allow-scripts">`; the artifact response adds a restrictive CSP. Servers stop after capture, including on failures. | [`_serve`, `_host_handler`, `_artifact_handler`](../../src/arena/scorers/visual.py) |
| `visual@1` | Captures mobile (375×812), tablet (768×1024) and desktop (1440×900); stores screenshots in the artifact blob store and puts their metadata plus per-viewport console errors in score evidence. | [`VisualScorer`](../../src/arena/scorers/visual.py) |

### Visual runtime path

1. `VisualScorer` selects the only HTML artifact, or uses `config.path` when the trial has multiple candidates. Missing HTML returns a failing score; an unreadable or corrupt blob raises `ScorerError`.
2. It serves the artifact on a different loopback port from the frame host. Chromium loads the host, whose sandboxed iframe loads the artifact with scripts allowed, no same-origin permission, and a CSP that blocks network access.
3. The browser captures each viewport and records console errors. Only after all three captures succeed are PNG blobs stored; their `Artifact` records and error lists are included in evidence.
4. The deterministic check counts console error events across all three viewports. Zero errors passes with normalized score 1; otherwise it fails and reports `1 / (1 + error_count)`.

Playwright is an optional, lazily imported dependency; this change does not add it to `pyproject.toml` or `uv.lock`. To use the real browser adapter, install Playwright and Chromium in the runtime environment. Browser launch or capture failures raise `CaptureError` (a `ScorerError`) and produce no score or screenshot writes. The artifact response CSP disables network access, scripts beyond inline scripts, and same-origin access. The loopback server serves only the selected artifact path.

## Verification

```sh
uv run pytest tests/scorers/test_framework.py tests/scorers/test_visual.py
```

## Execution scorers

Scorers that run code from a trial's artifacts in a sandbox
([`execution.py`](../../src/arena/scorers/execution.py)). They apply to `codegen` and
`agentic-code` tasks. They plug into the framework above and add no new storage.

### Responsibilities and sources of truth

| Part | Responsibility | Source |
| --- | --- | --- |
| Sandbox protocol | Two methods: `put_files` and `run`. A timeout is a `CommandResult`; a sandbox that cannot start, copy or run raises `SandboxError`. The CP3 Docker backend implements it; unit tests use fakes. | [`Sandbox`](../../src/arena/scorers/execution.py) |
| Hidden tests | Copies the trial's artifacts and the task's hidden test files into a fresh sandbox and runs pytest. `normalized` is passed / (passed + failed + errored). The task's files replace a contestant file at the same path. | [`HiddenTestsScorer`](../../src/arena/scorers/execution.py) |
| Build, type-check | Pass (1.0) when the command exits 0; any other exit or a timeout is 0. Both can be gates. | [`ExitCodeScorer`](../../src/arena/scorers/execution.py) |
| Lint, security scan | Count findings with a counter. No findings scores 1.0, `n` findings score `1/(1+n)`, and `passed` is true only for none. | [`FindingsScorer`](../../src/arena/scorers/execution.py) |
| Finding counters | `count_ruff_findings` reads ruff's `Found N errors.` or `All checks passed!`. `count_json_results` takes the length of `results` in a JSON report (`bandit -f json`, `semgrep --json`). | [`count_ruff_findings`, `count_json_results`](../../src/arena/scorers/execution.py) |

Scorer IDs are `hidden-tests`, `build`, `typecheck`, `lint` and `security`, all at version `1`.
The command, timeout and hidden test files are constructor arguments, so a task chooses them.

### Runtime path

1. The scorer reads every artifact through `ScorerContext.read` (digest-checked) and rejects
   an absolute path or one containing `..`.
2. It opens a new sandbox from the `SandboxFactory`, copies the artifacts and then the task's
   files, and runs one command with the timeout.
3. It turns the result into a `Score` whose evidence holds the command, exit code and
   parsed counts. A failing build or type-check also keeps the last 2000 characters of output.
   For hidden tests, the evidence lists the passed, failed and skipped test IDs.

### Constraints and failure behavior

- The contestant's code is wrong, so the score is 0 and the trial is scored: a failing test,
  an unimportable solution (pytest reports an error), a non-zero build or type-check exit,
  a timeout of tests, build or type-check.
- Our environment is wrong, so `ScorerError` is raised and nothing is written: a `SandboxError`,
  exit 126 or 127 (the command could not start), pytest output with no summary or no tests,
  an exit code that contradicts the summary, result lines that disagree with the counts, a
  lint or security output the counter cannot read, and a lint or security timeout.
- Unknown is never 0 or "clean". An empty or unparseable security report is an error.
- `FindingsScorer` does not use the exit code. Ruff and bandit exit 1 when they find something.
- Scoring twice gives equal scores for the same output. Evidence for hidden tests contains no
  timings or raw output.
- Not covered: `1/(1+n)` is a plain decay and does not weight findings by severity. There is
  no performance benchmark scorer yet.

### Verification

```sh
uv run pytest tests/scorers/test_execution.py
```

The golden fixtures are in `tests/scorers/golden/`: the oracle solution scores 1.0, the null
solution (no artifacts) scores 0 and the buggy solution fails three named tests. They run
real pytest and ruff in a throwaway host directory (a test double, not an isolated sandbox).
Build, type-check and security use scripted results. No test needs Docker.

## Constraint and reference scorers

### Responsibilities and sources of truth

| Part | Responsibility | Source |
| --- | --- | --- |
| Constraint scorer | Deterministic word-count, required Markdown section and keyword, forbidden-content, strict JSON, JSON Schema, and Markdown structure checks. All configured checks must pass for a pass verdict; the normalized value is the fraction passed. | [`ConstraintScorer`, check models](../../src/arena/scorers/constraint.py) |
| Reference scorer | Deterministic token-multiset Dice similarity and key-point token coverage. It uses no model, embedding service, or network call. | [`ReferenceScorer`, check models](../../src/arena/scorers/reference.py) |
| Text input | Reads a named artifact through `ScorerContext.read`, which verifies its blob digest, then requires UTF-8 text. | [`read_text`](../../src/arena/scorers/constraint.py) |

### Runtime path

1. Construct a scorer with an artifact path and one or more typed checks. A reference scorer also receives the reference text.
2. `score(context)` reads the artifact bytes from the context and decodes UTF-8.
3. The scorer evaluates the checks in the configured order. JSON Schema validation uses draft 2020-12 and an empty reference registry, so external `$ref` resources are never fetched.
4. The returned `Score` carries a normalized fraction, a pass verdict requiring every check to pass, a concise rationale, and structured evidence. Key-point evidence lists covered and missed points and records matched tokens for each covered point.

### Constraints and failure behavior

- Word-count bounds are inclusive. Required sections are matched against Markdown ATX headings outside fenced code blocks, with case and whitespace normalized. Keywords use whole-word/phrase matching; forbidden terms are whole-word matches and forbidden patterns are regular expressions.
- JSON validity rejects non-standard constants such as `NaN`, syntax errors and trailing content. JSON Schema failures include stable, sorted instance paths. Markdown validity checks non-empty content, heading syntax and level jumps, and closed fenced code blocks; it is a deterministic structural check, not a complete CommonMark parser.
- Reference similarity is multiset Dice overlap over Unicode-normalized, case-folded tokens. Key-point coverage is mean token recall per point and passes a point when it meets the configured threshold. These lexical metrics do not claim semantic equivalence.
- Invalid scorer configuration, unreadable artifacts, non-UTF-8 data, malformed regular expressions and unresolved schema references raise `ScorerError`; they are not recorded as a failed content check. Remote schema references are not resolved over the network.
- Evidence is deterministic and bounded where it can grow with input size. A missing key point remains listed under `missed`, with its coverage fraction retained to show partial overlap.

### Verification

```sh
uv run pytest tests/scorers/test_constraint.py tests/scorers/test_reference.py
```

## Rubric judge

### Responsibilities and sources of truth

| Part | Responsibility | Source |
| --- | --- | --- |
| Rubric | Validates criteria, positive weights, scale, anchors, version and content digest. | [`Rubric`, `load_rubric`](../../src/arena/judges/rubric.py) |
| Pointwise judge | Reads the configured answer artifact, asks one named judge model for a JSON score and rationale per criterion, and produces a weighted score normalized to [0, 1]. The identity is `rubric-judge:<rubric id>@<rubric version>`. | [`RubricJudge`](../../src/arena/judges/rubric.py) |
| Judge transport | Sends one OpenAI chat-completions or Anthropic messages request with bearer token `arena-judge-<run_id>`; the gateway attributes it to purpose `judge`. It does not retry or change models. | [`JudgeTransport`, `HttpJudgeTransport`](../../src/arena/judges/client.py) |

### Runtime path

1. A `RubricJudge` reads its configured text artifact through `ScorerContext.read`, sends the rubric criteria, anchors, task prompt and answer to its injected transport, then validates a strict JSON verdict with one in-range integer score and a non-empty rationale per criterion.
2. The judge call carries the run's `arena-judge-<run_id>` token. The gateway attributes it to `purpose: judge` for separate ledger accounting. Unit tests use in-process fake transports, so they make no provider calls.

### Constraints and failure behavior

- A rubric change should increment the rubric version. The score identity uses the rubric version, and evidence also records the rubric SHA-256 so an edit made without a version bump stays visible. Registry resolution by `rubric-judge` ID alone is ambiguous when several versions are registered; use the explicit `rubric-judge:<id>@<version>` reference.
- A malformed, incomplete, duplicate, unknown-criterion or out-of-scale verdict raises `MalformedVerdictError`. Transport and artifact failures also abort scoring. `score_run` writes only after every score succeeds, so a judge failure writes nothing and leaves existing score rows untouched.
- Evidence records the judge model ID, the reply model when supplied, and token usage when returned.
- Not covered: no real provider or gateway call has been exercised. The mock provider (#22) and the gateway integration come later.

### Verification

```sh
uv run pytest tests/judges/test_rubric.py
```

## Pairwise judge

### Responsibilities and sources of truth

| Part | Responsibility | Source |
| --- | --- | --- |
| Pairwise judgment | Compares two stored text artifacts in both orders and returns a row keyed by Trial A, Trial B, task and judge version. A position-dependent flip is a tie with both rationales retained. | [`PairwiseJudge`, `Judgment`](../../src/arena/judges/pairwise.py) |
| Visual pairwise judge | Reads screenshot artifact paths from each trial context and sends screenshot bytes as multimodal content in both orders against a design rubric. It adapts image blocks for the transport's OpenAI or Anthropic wire protocol. | [`VisualJudge`](../../src/arena/judges/visual.py) |
| Judge transport | Uses the shared injectable transport and the run's `arena-judge-<run_id>` token. Tests use an in-process fake; no provider is called directly. | [`JudgeTransport`, `judge_request`](../../src/arena/judges/client.py) |

### Runtime path

1. `PairwiseJudge` reads each trial's configured answer artifact through `ScorerContext.read`, then asks the injected transport for a structured winner/tie verdict in original and swapped order.
2. A stable winner is mapped back to Trial A or Trial B. A changed verdict under swapping becomes a tie and is marked as position bias in the judgment evidence.
3. `VisualJudge` resolves the configured screenshot path in each context, reads the content-addressed bytes, and performs the same two-order comparison with image payloads and a design rubric.

### Constraints and failure behavior

- Each verdict must be strict JSON with exactly `winner` (`a`, `b` or `tie`) and a non-empty rationale. Invalid, missing or extra fields raise `MalformedPairwiseVerdictError`; transport and artifact failures also abort without returning a judgment.
- A judge preferring position A or B irrespective of the submission yields a tie after the swap. Evidence keeps both positional verdicts and rationales so position bias is inspectable.
- Visual judging requires an existing image artifact path per trial. Missing paths, non-image artifacts and unreadable blobs raise `ScorerError`.
- Tests inject a fake transport. No live provider or gateway call is made by this implementation's test suite.

### Verification

```sh
uv run pytest tests/judges/test_pairwise.py
```
