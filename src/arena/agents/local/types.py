"""Shared shapes for running a vendor CLI on its own sign-in.

The stream formats parsed here follow Neumar's handling of `claude -p --output-format
stream-json` and `codex exec --json`. They are not yet captured from a real run, which needs a
model call and the maintainer's approval (CP8 issue #194). Fixtures under
fixtures/cli-contracts/*/stream.* are marked as written from that handling until then.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol


@dataclass(frozen=True)
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    reasoning_tokens: int = 0

    @property
    def total(self) -> int:
        return self.input_tokens + self.output_tokens


@dataclass(frozen=True)
class SignIn:
    """The vendor sign-in to place in a scratch home: relative path to bytes.

    Never printed or logged. The runner writes each file 0600 under the scratch home and deletes
    the home when the trial ends.
    """

    files: Mapping[str, bytes]

    def __repr__(self) -> str:
        return f"SignIn(files={sorted(self.files)})"


@dataclass(frozen=True)
class StreamSummary:
    """What a CLI's event stream said about one run."""

    events: int
    model_served: str | None
    usage: Usage | None
    final_text: str
    transcript: str
    limit_hit: bool = False
    auth_failed: bool = False
    error: str | None = None
    auxiliary_models: tuple[str, ...] = field(default_factory=tuple)


class LocalCliAdapter(Protocol):
    runtime: str

    def command(self, binary: str, model: str, prompt: str, workspace: Path) -> list[str]: ...

    def home_environment(self, home: Path, model: str) -> dict[str, str]: ...

    def parse(self, lines: Sequence[str]) -> StreamSummary: ...
