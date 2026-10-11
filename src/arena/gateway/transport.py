"""HTTP transport adapter that preserves upstream response body chunks."""

from __future__ import annotations

from collections.abc import AsyncIterator, Mapping
from dataclasses import dataclass

import httpx


@dataclass(frozen=True, slots=True)
class TransportResponse:
    status_code: int
    headers: Mapping[str, str]
    body: AsyncIterator[bytes]


class HttpTransport:
    """Send byte-exact requests and stream the upstream response body unchanged."""

    def __init__(self, *, timeout: float = 600.0) -> None:
        if timeout <= 0:
            raise ValueError("HTTP transport timeout must be positive")
        self.timeout = timeout

    async def request(
        self,
        url: str,
        *,
        body: bytes,
        headers: Mapping[str, str],
    ) -> TransportResponse:
        client = httpx.AsyncClient(timeout=self.timeout)
        try:
            request = client.build_request(
                "POST",
                url,
                content=body,
                headers={**headers, "content-type": "application/json"},
            )
            response = await client.send(request, stream=True)
        except BaseException:
            await client.aclose()
            raise

        async def chunks() -> AsyncIterator[bytes]:
            try:
                async for chunk in response.aiter_raw():
                    yield chunk
            finally:
                await response.aclose()
                await client.aclose()

        safe_headers = {
            key: value
            for key, value in response.headers.items()
            if key.lower() in {"content-type", "retry-after", "x-request-id"}
        }
        return TransportResponse(response.status_code, safe_headers, chunks())

    async def from_response(
        self,
        status_code: int,
        headers: Mapping[str, str],
        body: AsyncIterator[bytes],
    ) -> TransportResponse:
        """Build the transport response shape for injected transports and tests."""
        return TransportResponse(status_code, dict(headers), body)
