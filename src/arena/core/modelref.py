"""`provider/model[:effort]`, the member syntax magpie uses for routing groups."""

from __future__ import annotations

from typing import Literal, get_args

from pydantic import BaseModel, ConfigDict, model_validator

Effort = Literal["off", "low", "medium", "high", "max"]
EFFORTS: tuple[str, ...] = get_args(Effort)


class ModelRef(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    provider: str
    model: str
    effort: Effort | None = None

    @model_validator(mode="after")
    def _non_empty(self) -> ModelRef:
        if not self.provider or "/" in self.provider or not self.model:
            raise ValueError(f"not a provider/model reference: {self!s}")
        return self

    @classmethod
    def parse(cls, text: str) -> ModelRef:
        """The provider is the part before the first `/`.

        A trailing `:level` is an effort only when it is one of the five levels, so a model
        name that itself contains a colon (`qwen/qwen3-coder:free`) stays whole.
        """
        provider, sep, rest = text.strip().partition("/")
        if not sep:
            raise ValueError(f"expected provider/model[:effort], got {text!r}")
        model, colon, tail = rest.rpartition(":")
        if colon and tail in EFFORTS:
            return cls(provider=provider, model=model, effort=tail)  # type: ignore[arg-type]
        return cls(provider=provider, model=rest)

    def __str__(self) -> str:
        base = f"{self.provider}/{self.model}"
        return f"{base}:{self.effort}" if self.effort else base
