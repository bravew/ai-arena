"""HTTP routes for blind human voting, mounted on the viewer server's route registry.

A voter is a random id the server sets in an `arena_voter` cookie when the first pair is served.
Pair responses never carry identities; a successful vote returns the reveal. The server's own
Origin check already refuses cross-origin writes before a route runs.
"""

from __future__ import annotations

import json
import re
import secrets
from typing import Any, cast

from arena.server.app import ArenaHTTPServer
from arena.server.votes import HumanVoteService

_VOTER = re.compile(r"[A-Za-z0-9_-]{16,64}")
_MAX_BODY = 4096


def register_vote_routes(server: ArenaHTTPServer, service: HumanVoteService) -> None:
    """Add `GET /api/votes/pair`, `POST /api/votes` and `GET /api/votes/leaderboard`."""

    def voter_of(handler: Any) -> str | None:
        for part in str(handler.headers.get("Cookie", "")).split(";"):
            name, _, value = part.strip().partition("=")
            if name == "arena_voter" and _VOTER.fullmatch(value):
                return value
        return None

    def get_pair(handler: Any, _match: str) -> None:
        voter = voter_of(handler)
        headers: dict[str, str] = {}
        if voter is None:
            voter = secrets.token_urlsafe(16)
            cookie = f"arena_voter={voter}; HttpOnly; SameSite=Strict; Path=/"
            if handler.headers.get("X-Forwarded-Proto", "").lower() == "https":
                cookie += "; Secure"
            headers["Set-Cookie"] = cookie
        response = service.handle_get_pair(voter)
        if response.status == 204:
            handler.send_bytes(204, b"", headers=headers)
            return
        handler.send_json(response.status, response.body, headers)

    def post_vote(handler: Any, _match: str) -> None:
        voter = voter_of(handler)
        if voter is None:
            handler.send_json(400, {"error": "no voter session; load a comparison first"})
            return
        try:
            length = int(handler.headers.get("Content-Length", "0"))
            if length <= 0 or length > _MAX_BODY:
                raise ValueError("invalid body size")
            payload = cast(object, json.loads(handler.rfile.read(length)))
        except ValueError:
            handler.send_json(400, {"error": "invalid vote request"})
            return
        try:
            response = service.handle_post_vote(voter, payload)
        except RuntimeError as error:
            handler.send_json(500, {"error": str(error)})
            return
        handler.send_json(response.status, response.body)

    def leaderboard(handler: Any, _match: str) -> None:
        # Ratings from disconnected comparison groups are not comparable, so every entry is listed
        # by strength rather than forcing one global ranking.
        try:
            ratings = service.leaderboard()
        except ValueError as error:
            handler.send_json(500, {"error": f"cannot rate votes: {error}"})
            return
        compared: dict[str, int] = {}
        for vote in service.votes:
            for contestant in (vote.left_contestant_id, vote.right_contestant_id):
                compared[contestant] = compared.get(contestant, 0) + 1
        rows = sorted(ratings.entries, key=lambda row: (-row.strength, row.contestant_id))
        handler.send_json(
            200,
            [
                {
                    "contestant_id": row.contestant_id,
                    "rating": row.strength,
                    "low": row.interval.low,
                    "high": row.interval.high,
                    "comparisons": compared.get(row.contestant_id, 0),
                }
                for row in rows
            ],
        )

    server.register_route("GET", r"/api/votes/pair", get_pair)
    server.register_route("POST", r"/api/votes", post_vote)
    server.register_route("GET", r"/api/votes/leaderboard", leaderboard)
