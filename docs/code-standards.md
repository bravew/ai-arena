# Code standards

A reviewer checks each rule below, and a PR that doesn't meet one gets a comment
saying which. They come from [DEV_PLAN §12](DEV_PLAN.md#12-engineering-conventions)
and, once there are lessons, from [LESSONS.md](../LESSONS.md). How to describe
and review a change to a subsystem is in the
[subsystem references](subsystems/README.md).

## Acceptance criteria

Every change states these in its PR, with what was run and what it printed. A
check that wasn't run is listed as not run, never left out.

1. **A test that fails without the change.** Report the failing output.
   *Why: a test that passes either way proves nothing about the change.*
2. **Tests run under the `tests/testenv` temporary HOME.** No test reads or
   writes `~/.claude`, `~/.codex`, `~/.config/*` or a real `arena.db`; `testenv`
   fails the test if it does. *Why: a test that touches the real machine
   damages the user's setup and passes only on the author's.*
3. **The suite.** `uv run pytest` and `uv run pyright`. For a viewer change
   also `pnpm -C web test` and the e2e suite in Chromium and WebKit.
   *Why: the checks that matter are the ones run at the head being merged.*
4. **A check against the real thing where possible** (a real provider key, a
   real agent CLI version), or a plain statement that it wasn't run.
   *Why: mocks agree with the code that wrote them, not with the vendor.*

## Tests

- **Fixtures come from real bytes.** Provider replies, agent transcripts and
  error bodies are recorded from real calls with secrets redacted, including
  truncated and empty bodies. *Why: an invented sample only covers the cases
  its author thought of.*
- **A red test on `main` is a bug now.** "Fails the same on `main`" is not a
  baseline. Fix it at its cause, in its own commit, before building on it.
  *Why: every change after a red test ships with no signal.*

## Commits and merges

- **One commit, one goal.** A fix found on the way goes in its own commit.
  *Why: a mixed commit can't be reviewed or reverted on its own.*
- **Merge only the reviewed head, and record what was run.** Write on the PR
  what was run, at which head, then merge that head
  (`gh pr merge --match-head-commit <sha>`). A head pushed after the review is
  reviewed again. *Why: a push during review would otherwise ship code nobody
  ran, and a merge with no record can't be checked afterwards.*

## Scope of a change

- **A fix reaches every sibling of the bug.** Grep for the same pattern: every
  adapter, provider, scorer and caller of the same shape gets the fix in the
  same change. *Why: a fix that covers only the reported case needs a second
  release.*
- **A change re-derives everything built on it.** When a change alters a
  formula, a list, an id, a schema or the timing of an async step, find every
  caller, saved copy, fixture and test that assumed the old meaning, and run
  them. *Why: the old assumption fails later and somewhere else.*

## Failure handling

- **A failed read, parse or hash means "unknown", never "empty" or "equal".**
  Raise or report it. Don't return an empty list, and don't treat two
  unreadable things as equal. *Why: "empty" gets written back or acted on, and
  data is lost or a wrong result is reported as a real one.* (CP1 example: an
  unreadable `arena.db` is an error, not "no runs".)
- **Classify failures narrowly.** Different errors get different names and
  different handling; don't fold them into one catch-all. *Why: the gateway's
  failure classes decide rests and retries, and a wrong class rests a healthy
  key or hides a real fault.*

## The user's machine

- **Never write the user's real agent configs.** Agent config is written only
  inside trial containers, and tests run under `tests/testenv`. *Why: the arena
  must not change how the user's own agents behave outside a trial.*
