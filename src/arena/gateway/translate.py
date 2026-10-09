"""Protocol request translation boundary for gateway relays."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any

ProtocolTranslator = Callable[[str, str, Mapping[str, Any]], Mapping[str, Any]]
StructuredOutputMapper = Callable[[str, str, Mapping[str, Any]], Mapping[str, Any]]

# Caller constraints must be represented using target-protocol fields.
_STRUCTURED_OUTPUT_FIELDS = ("response_format", "json_schema", "structured_outputs")


class TranslationError(ValueError):
    """The requested protocol conversion could not be completed safely."""


def translate_request(
    payload: Mapping[str, Any],
    *,
    source_protocol: str,
    target_protocol: str,
    translator: ProtocolTranslator,
    structured_output_mapper: StructuredOutputMapper | None = None,
) -> dict[str, Any]:
    """Translate a decoded JSON request and reject lost structured-output constraints.

    Translation adapters must express structured-output constraints in the target
    protocol. The source-format field is never copied back into the target request.
    """
    if not source_protocol or not target_protocol:
        raise TranslationError("source and target protocols are required")
    if source_protocol == target_protocol:
        return dict(payload)

    structured = {key: payload[key] for key in _STRUCTURED_OUTPUT_FIELDS if key in payload}
    try:
        translated = dict(translator(source_protocol, target_protocol, dict(payload)))
    except Exception as exc:
        message = f"translation from {source_protocol} to {target_protocol} failed"
        raise TranslationError(message) from exc

    if structured:
        if structured_output_mapper is None:
            raise TranslationError(
                f"translation from {source_protocol} to {target_protocol} requires a "
                "structured_output_mapper"
            )
        try:
            expected = dict(structured_output_mapper(source_protocol, target_protocol, structured))
        except Exception as exc:
            message = (
                f"structured-output translation from {source_protocol} to {target_protocol} failed"
            )
            raise TranslationError(message) from exc
        for key, value in expected.items():
            if translated.get(key) != value:
                raise TranslationError(
                    f"translation from {source_protocol} to {target_protocol} did not "
                    f"preserve structured-output setting {key!r}"
                )
    return translated
