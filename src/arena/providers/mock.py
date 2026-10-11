"""Deterministic, network-free provider responses for supported protocols."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from typing import Any

SUPPORTED_PROTOCOLS = frozenset({"chat", "responses", "anthropic", "gemini"})


class UnsupportedMockProtocol(ValueError):
    """Raised when the mock provider receives an unknown protocol."""


class MockProvider:
    """Produce stable protocol-shaped outputs without credentials or network access."""

    def __init__(self, output: str = "Mock response") -> None:
        if not output:
            raise ValueError("mock output must not be empty")
        self.output = output

    def complete(self, protocol: str, request: Mapping[str, Any]) -> dict[str, Any]:
        """Return a deterministic completion in the requested protocol's response shape."""
        if protocol not in SUPPORTED_PROTOCOLS:
            raise UnsupportedMockProtocol(f"unsupported mock protocol: {protocol}")
        model_value = request.get("model", "mock")
        model = model_value if isinstance(model_value, str) else "mock"
        stable_input = json.dumps(
            request,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            default=repr,
        )
        response_id = (
            "mock-" + hashlib.sha256(f"{protocol}:{model}:{stable_input}".encode()).hexdigest()[:16]
        )

        if protocol == "chat":
            return {
                "id": response_id,
                "object": "chat.completion",
                "model": model,
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": self.output},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {"prompt_tokens": 1, "completion_tokens": 2, "total_tokens": 3},
            }
        if protocol == "responses":
            return {
                "id": response_id,
                "object": "response",
                "model": model,
                "status": "completed",
                "output": [
                    {
                        "id": response_id + "-item",
                        "type": "message",
                        "role": "assistant",
                        "content": [{"type": "output_text", "text": self.output}],
                    }
                ],
                "usage": {"input_tokens": 1, "output_tokens": 2, "total_tokens": 3},
            }
        if protocol == "anthropic":
            return {
                "id": response_id,
                "type": "message",
                "role": "assistant",
                "model": model,
                "content": [{"type": "text", "text": self.output}],
                "stop_reason": "end_turn",
                "stop_sequence": None,
                "usage": {"input_tokens": 1, "output_tokens": 2},
            }
        return {
            "candidates": [
                {
                    "content": {"role": "model", "parts": [{"text": self.output}]},
                    "finishReason": "STOP",
                    "index": 0,
                }
            ],
            "usageMetadata": {
                "promptTokenCount": 1,
                "candidatesTokenCount": 2,
                "totalTokenCount": 3,
            },
            "modelVersion": model,
            "responseId": response_id,
        }
