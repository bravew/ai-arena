"""Ops-only gateway health, lane snapshots and process metrics endpoints."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import asdict, dataclass
from typing import Any, cast

from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route

from arena.gateway.auth import Caller, Purpose
from arena.gateway.lanes import KeyLane
from arena.obs.metrics import Metrics
from arena.obs.otel import OtlpExporter


@dataclass(frozen=True, slots=True)
class LaneSnapshot:
    in_flight: int
    queued: int
    concurrency: int
    resting_until: float | None = None
    rest_class: str | None = None


def snapshot_lane(
    lane: KeyLane, *, resting_until: float | None = None, rest_class: str | None = None
) -> LaneSnapshot:
    """Copy current lane values into a transport-safe immutable snapshot."""
    return LaneSnapshot(lane.in_flight, lane.queued, lane.concurrency, resting_until, rest_class)


def status_routes(
    *,
    lanes: Callable[[], dict[str, LaneSnapshot]],
    metrics: Metrics,
    exporter: OtlpExporter | None = None,
) -> list[Route]:
    """Build status routes for inclusion in the authenticated gateway ASGI app."""

    async def health(request: Request) -> Response:
        if not _ops(request):
            return _denied(request)
        return JSONResponse({"status": "ok"})

    async def lane_status(request: Request) -> Response:
        if not _ops(request):
            return _denied(request)
        try:
            snapshots = lanes()
            return JSONResponse({"lanes": {key: asdict(value) for key, value in snapshots.items()}})
        except Exception:
            return JSONResponse(
                {"error": {"type": "lanes_unavailable", "message": "lane state is unavailable"}},
                status_code=500,
            )

    async def stats(request: Request) -> Response:
        if not _ops(request):
            return _denied(request)
        otel: dict[str, Any]
        if exporter is None:
            otel = {"enabled": False}
        else:
            current = exporter.stats()
            otel = {
                "enabled": True,
                "queued": current.queued,
                "queued_bytes": current.queued_bytes,
                "otel_dropped": current.dropped,
                "exported": current.exported,
                "failed": current.failed,
                "errors": current.errors,
            }
        return JSONResponse({"metrics": metrics.snapshot(), "otel": otel})

    return [
        Route("/arena/health", health, methods=["GET"]),
        Route("/arena/lanes", lane_status, methods=["GET"]),
        Route("/arena/stats", stats, methods=["GET"]),
    ]


def _ops(request: Request) -> bool:
    caller = cast(Caller | None, request.scope.get("state", {}).get("arena.caller"))
    return caller is not None and caller.purpose is Purpose.OPS


def _denied(request: Request) -> Response:
    caller = cast(Caller | None, request.scope.get("state", {}).get("arena.caller"))
    if caller is None:
        return JSONResponse(
            {"error": {"type": "authentication_error", "message": "missing arena token"}},
            status_code=401,
            headers={"WWW-Authenticate": "Bearer"},
        )
    return JSONResponse(
        {"error": {"type": "forbidden", "message": "arena ops token required"}},
        status_code=403,
    )
