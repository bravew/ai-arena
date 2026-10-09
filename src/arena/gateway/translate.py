"""Protocol request translation boundary for gateway relays.

Translation is injected at this boundary so the gateway can use LiteLLM's protocol
translator without coupling request admission to a provider call. The adapter must
return a request in the target protocol and preserve structured-output constraints.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any

ProtocolTranslator = Callable[[str, str, Mapping[str, Any]], Mapping[str, Any]]


class TranslationError(ValueError):
    """The requested protocol conversion could not be completed."""


def translate_request(
    payload: Mapping[str, Any],
    *,
    source_protocol: str,
    target_protocol: str,
    translator: ProtocolTranslator,
) -> dict[str, Any]:
    """Translate a decoded JSON request while retaining structured-output settings.

    The source payload is copied before handing it to the translator. Structured output
    fields are restored if an adapter omits them, preventing protocol adapters from
    silently weakening a schema-constrained request.
    """
    if not source_protocol or not target_protocol:
        raise TranslationError("source and target protocols are required")
    if source_protocol == target_protocol:
        return dict(payload)

    structured_fields = ("response_format", "json_schema", "structured_outputs")
    preserved = {key: payload[key] for key in structured_fields if key in payload}
    try:
        translated = dict(translator(source_protocol, target_protocol, dict(payload)))
    except Exception as exc:
        message = f"translation from {source_protocol} to {target_protocol} failed"
        raise TranslationError(message) from exc
    # Adapters may drop or rewrite these fields. Preserve their exact decoded values so
    # translation cannot silently relax a caller's output constraint.
    translated.update(preserved)
    return translated
