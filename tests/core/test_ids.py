import pytest

from arena.core.ids import canonical_json, content_id


def test_key_order_does_not_change_the_text() -> None:
    assert canonical_json({"b": 1, "a": {"d": 2, "c": 3}}) == canonical_json(
        {"a": {"c": 3, "d": 2}, "b": 1}
    )


def test_text_is_compact_and_keeps_unicode() -> None:
    assert canonical_json({"k": "é"}) == '{"k":"é"}'


def test_nan_is_refused() -> None:
    with pytest.raises(ValueError):
        canonical_json({"t": float("nan")})


def test_content_id_is_twelve_hex_chars_and_stable() -> None:
    first = content_id({"a": 1})
    assert len(first) == 12
    assert first == content_id({"a": 1})
    assert first != content_id({"a": 2})
