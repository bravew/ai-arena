# Notes for coding agents

AI Arena compares models, agents and kits on the same tasks. The plan is in
[docs/DEV_PLAN.md](docs/DEV_PLAN.md). This page only points to what you need.

## Read first

- [Subsystem references](docs/subsystems/README.md): how each subsystem works,
  how to update its page in your PR, and how to review a change to it. Read the
  page for the subsystem you change and check its source links.
- [Code standards](docs/code-standards.md): the rules a reviewer checks.
- [LESSONS.md](LESSONS.md): what merged work got wrong and the rule that would
  have caught it.
- [DEV_PLAN.md](docs/DEV_PLAN.md): the design. §11 is the repository layout, §12
  the engineering conventions, §13 the checkpoints.

## Workflow

The full text is [DEV_PLAN §13.1](docs/DEV_PLAN.md#131-issues-branches-and-worktrees).

- One sub-issue, one worktree, one branch `cpN/<issue>-<slug>`, cut from
  `origin/epic/cpN-<slug>`:
  ```sh
  git fetch origin
  git worktree add ../ai-arena-wt/<issue> -b cpN/<issue>-<slug> origin/epic/cpN-<slug>
  ```
- The PR targets the epic branch, never `main`. Its body says `Part of #<epic>`
  and `Closes #<issue>`.
- `Closes` only fires on merges into `main`, so close the sub-issue by hand
  after the PR merges into the epic branch.
- A sub-issue lists the files it owns. Don't edit files another open sub-issue
  owns; say so in the PR and wait for that issue to merge.
- When every sub-issue of an epic is closed, open one PR from `epic/cpN-<slug>`
  into `main`, with the checkpoint's verify commands and their output.

## Schema owner

SQLite migrations, `docs/bundle-schema.json` and `docs/event-schema.json`
change only through the CP1 schema owner (the maintainer, bravew). If your work
needs a change to one of them, ask for it in your PR description. Don't edit
them.

## Commands

```sh
uv sync
uv run pytest
uv run pyright
uv run ruff check
```

Tests run under the temporary HOME from `tests/testenv`; see the
[code standards](docs/code-standards.md).

## Where the arena differs from magpie

The arena borrows magpie's patterns (DEV_PLAN §2), with three deliberate
differences:

- **No cross-model fallback.** A trial never switches model partway, because
  that changes the contestant.
- **Hooks fail closed.** A hook that throws or times out errors the call. A
  silent no-op would change the experiment.
- **Agent config is written only inside trial containers**, never on the host.
  Nothing reads or writes the user's real agent configs.
