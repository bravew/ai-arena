"""Normalized JSON cassette recording and strict replay primitives.

The gateway server does not yet select provider handlers. Callers can inject
``CassetteHandler`` as their dispatcher once provider selection is wired.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, cast

from arena.gateway.redact import scrub_headers, scrub_json


class CassetteError(Exception):
    """A cassette could not be read, parsed, or written."""


class CassetteMiss(CassetteError):
    """No recorded entry matches the requested protocol and normalized request."""


@dataclass(frozen=True, slots=True)
class CassetteResponse:
    """Serializable response data retained independently of a server response object."""

    status_code: int
    headers: Mapping[str, str]
    body: Mapping[str, Any]


ResponseHandler = Callable[[str, Mapping[str, Any]], Awaitable[CassetteResponse]]
CassetteMode = Literal["record", "replay"]


def normalize_request(request: Mapping[str, Any]) -> str:
    """Return stable JSON for matching requests regardless of object key order."""
    try:
        encoded = json.dumps(
            request,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
        return scrub_json(encoded).decode("utf-8")
    except (TypeError, ValueError) as exc:
        raise CassetteError(f"request cannot be normalized: {exc}") from exc


class CassetteRecorder:
    """Append normalized request/response pairs as JSON Lines."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._lock = asyncio.Lock()

    async def record(
        self, protocol: str, request: Mapping[str, Any], response: CassetteResponse
    ) -> None:
        entry = {
            "protocol": protocol,
            "request": json.loads(scrub_json(normalize_request(request).encode("utf-8"))),
            "response": {
                "status_code": response.status_code,
                "headers": dict(sorted(scrub_headers(dict(response.headers)).items())),
                "body": json.loads(
                    scrub_json(
                        json.dumps(response.body, ensure_ascii=False, separators=(",", ":")).encode(
                            "utf-8"
                        )
                    )
                ),
            },
        }
        try:
            encoded = json.dumps(
                entry, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
            )
            async with self._lock:
                await asyncio.to_thread(self._append, encoded)
        except (OSError, TypeError, ValueError) as exc:
            raise CassetteError(f"could not record cassette at {self.path}: {exc}") from exc

    def _append(self, encoded: str) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as cassette:
            cassette.write(encoded + "\n")


class CassetteReplayer:
    """Serve only recorded responses; misses never invoke an upstream handler."""

    def __init__(self, path: Path) -> None:
        self.path = path

    async def replay(self, protocol: str, request: Mapping[str, Any]) -> CassetteResponse:
        expected_request = normalize_request(request)
        try:
            with self.path.open(encoding="utf-8") as cassette:
                for line_number, line in enumerate(cassette, start=1):
                    if not line.strip():
                        continue
                    try:
                        parsed: Any = json.loads(line)
                        if not isinstance(parsed, dict):
                            raise ValueError("entry must be an object")
                        entry = cast(dict[str, Any], parsed)
                        candidate: Any = entry.get("request")
                        if (
                            entry.get("protocol") == protocol
                            and isinstance(candidate, dict)
                            and normalize_request(cast(dict[str, Any], candidate))
                            == expected_request
                        ):
                            raw_response: Any = entry["response"]
                            if not isinstance(raw_response, dict):
                                raise ValueError("response must be an object")
                            response = cast(dict[str, Any], raw_response)
                            raw_headers: Any = response["headers"]
                            raw_body: Any = response["body"]
                            if not isinstance(raw_headers, dict) or not isinstance(raw_body, dict):
                                raise ValueError("response headers and body must be objects")
                            parsed_headers = cast(dict[Any, Any], raw_headers)
                            parsed_body = cast(dict[str, Any], raw_body)
                            if not all(
                                isinstance(key, str) and isinstance(value, str)
                                for key, value in parsed_headers.items()
                            ):
                                raise ValueError("response headers must contain only strings")
                            status_code: Any = response["status_code"]
                            if not isinstance(status_code, int) or isinstance(status_code, bool):
                                raise ValueError("response status_code must be an integer")
                            headers: dict[str, str] = cast(dict[str, str], raw_headers)
                            body: dict[str, Any] = parsed_body
                            return CassetteResponse(status_code, headers, body)
                    except (KeyError, TypeError, ValueError, CassetteError) as exc:
                        raise CassetteError(
                            f"invalid cassette entry at {self.path}:{line_number}: {exc}"
                        ) from exc
        except OSError as exc:
            if exc.errno == 2:
                raise CassetteMiss(f"no recorded cassette entry for protocol {protocol!r}") from exc
            raise CassetteError(f"could not read cassette at {self.path}: {exc}") from exc
        raise CassetteMiss(f"no recorded cassette entry for protocol {protocol!r}")

    async def dispatch(
        self,
        protocol: str,
        request: Mapping[str, Any],
        live_handler: ResponseHandler | None = None,
    ) -> CassetteResponse:
        """Replay a match and deliberately ignore the optional live handler."""
        del live_handler
        return await self.replay(protocol, request)


class CassetteHandler:
    """Injectable record/replay wrapper around an async provider response handler."""

    def __init__(
        self,
        mode: CassetteMode,
        path: Path,
        handler: ResponseHandler | None = None,
    ) -> None:
        self.mode = mode
        self.recorder = CassetteRecorder(path) if mode == "record" else None
        self.replayer = CassetteReplayer(path) if mode == "replay" else None
        self.handler = handler
        if mode == "record" and handler is None:
            raise ValueError("record mode requires a response handler")

    async def __call__(self, protocol: str, request: Mapping[str, Any]) -> CassetteResponse:
        if self.mode == "replay":
            assert self.replayer is not None
            return await self.replayer.replay(protocol, request)
        assert self.handler is not None and self.recorder is not None
        response = await self.handler(protocol, request)
        await self.recorder.record(protocol, request, response)
        return response
