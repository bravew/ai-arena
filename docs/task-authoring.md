# Task authoring

Tasks are versioned YAML documents stored at `suites/<suite>/tasks/<task>/task.yaml`.
Keep every task's prompt, fixtures, tests and reference solutions beside that file so
it can be validated and run without hidden repository-level assumptions.

## Task metadata

A minimal task looks like this:

```yaml
id: code.add-two
version: 1
kind: codegen
created_at: 2026-10-08
prompt_file: prompt.md
scorers:
  - { id: execution, weight: 1.0, command: "python -m pytest -q tests" }
```

`kind` is `codegen`, `agentic-code`, `content` or `web-artifact`. Executable tasks
(`codegen` and `agentic-code`) must include an execution scorer with one command.
`created_at` is the task's ISO date of introduction. Use relative paths from the
task directory, and keep the prompt concise, unambiguous and self-contained.

## Executable task layout

```text
my-task/
├── task.yaml
├── prompt.md
├── fixtures/             # optional starting repository files
├── tests/                # hidden tests; never copied into the contestant workspace
│   └── test_solution.py
└── solution/
    ├── oracle/            # known-good solution; must pass all task tests
    └── null/              # baseline solution; must fail at least one task test
```

Files from `fixtures/` are copied first, then the selected solution overlays them,
then `tests/` is copied into the temporary selfcheck workspace. Keep test imports
and commands relative to that workspace. The oracle validates that the task is
solvable; the null solution ensures the tests discriminate a missing or unchanged
implementation. Do not put tests or oracle code in `fixtures/`.

The task prompt should explain the requested behavior and relevant interfaces but
must not reveal hidden assertions or provide the oracle implementation. Tests are
part of the task definition and should cover the required behavior, edge cases and
regressions, not details of the oracle's coding style.

For `agentic-code`, author a small repository task with a concrete change request
and an execution scorer just as for `codegen`. The distinction is the agent's
repository-editing workflow, not a different task metadata schema.

## Selfcheck

Run all executable tasks under a suite directory:

```sh
uv run arena selfcheck --trust-task-code suites/code suites/agentic
```

Or pass individual task directories or `task.yaml` files. Selfcheck uses the
execution scorer command with the task's oracle and null solutions in separate
temporary workspaces. It prints `oracle=1.0` and `null=0.0` per task and fails
if either result is reversed, if task metadata or required files are invalid, or
if a command times out. It invokes the scorer without a shell and provides no
provider credentials.

**Trust boundary:** Selfcheck runs scorer commands, test files, fixture code and
oracle/null code directly on the host with the current user's privileges. A
temporary workspace and temporary `HOME` are conveniences, not a sandbox or
security boundary. Review suites before running them and use `--trust-task-code`
only for trusted local inputs. Running untrusted tasks requires an external
sandbox; selfcheck does not provide one.

The TypeScript starter tasks use Node.js 22.6 or later with
`--experimental-strip-types`; Go tasks require the Go toolchain, and Rust tasks
require Cargo and rustc.

Also validate task metadata and prompt paths before running:

```sh
uv run arena validate suites/code suites/agentic
```

Content and web-artifact tasks may use rubric, constraint or visual scorers, and
do not participate in the executable oracle/null selfcheck.
