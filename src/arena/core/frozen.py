"""Deeply immutable containers, so a frozen model cannot change identity by nested mutation."""

from __future__ import annotations

from typing import Any, NoReturn


class FrozenDict(dict[str, Any]):
    """A dict that rejects every mutation. Equal to, and serialized as, a plain dict."""

    def _immutable(self) -> NoReturn:
        raise TypeError("FrozenDict is immutable")

    def __setitem__(self, key: str, value: Any) -> None:
        self._immutable()

    def __delitem__(self, key: str) -> None:
        self._immutable()

    def __ior__(self, other: Any) -> NoReturn:
        self._immutable()

    def clear(self) -> None:
        self._immutable()

    def pop(self, *args: Any) -> NoReturn:
        self._immutable()

    def popitem(self) -> NoReturn:
        self._immutable()

    def setdefault(self, *args: Any) -> NoReturn:
        self._immutable()

    def update(self, *args: Any, **kwargs: Any) -> None:
        self._immutable()


def freeze(value: Any) -> Any:
    """Copy `value`, turning dicts into FrozenDict and lists into tuples, recursively."""
    if isinstance(value, dict):
        items: dict[str, Any] = value  # type: ignore[assignment]
        return FrozenDict((key, freeze(item)) for key, item in items.items())
    if isinstance(value, (list, tuple)):
        sequence: list[Any] | tuple[Any, ...] = value  # type: ignore[assignment]
        return tuple(freeze(item) for item in sequence)
    return value


def frozen_mapping(value: dict[str, Any]) -> dict[str, Any]:
    result: dict[str, Any] = freeze(value)
    return result
