from __future__ import annotations

import json
import threading
import urllib.error
import urllib.request
from pathlib import Path

from arena.server.app import ArenaHTTPServer, create_server
from arena.server.vote_routes import register_vote_routes
from arena.server.votes import HumanVoteService, JsonlVoteStore, Pair


def _pairs() -> list[Pair]:
    return [
        Pair(
            pair_id=f"p{index}",
            task_id="t1",
            trial_a_id=f"ta{index}",
            contestant_a_id="alpha",
            content_a=f"alpha answer {index}",
            trial_b_id=f"tb{index}",
            contestant_b_id="beta",
            content_b=f"beta answer {index}",
        )
        for index in range(3)
    ]


def _call(
    url: str,
    *,
    method: str = "GET",
    cookie: str | None = None,
    body: bytes | None = None,
    extra: dict[str, str] | None = None,
) -> tuple[int, bytes, dict[str, str]]:
    headers = dict(extra or {})
    if cookie:
        headers["Cookie"] = cookie
    if body is not None:
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(url, data=body, method=method, headers=headers)
    try:
        response = urllib.request.urlopen(request, timeout=3)
    except urllib.error.HTTPError as error:
        return error.code, error.read(), dict(error.headers.items())
    with response:
        return response.status, response.read(), dict(response.headers.items())


class _Running:
    def __init__(self, home: Path, pairs: list[Pair]) -> None:
        self.service = HumanVoteService(pairs, JsonlVoteStore(home / "votes.jsonl"))
        self.server: ArenaHTTPServer = create_server("127.0.0.1", 0, home)
        register_vote_routes(self.server, self.service)
        self.base = f"http://127.0.0.1:{self.server.server_address[1]}"
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    def __enter__(self) -> _Running:
        self.thread.start()
        return self

    def __exit__(self, *_exc: object) -> None:
        self.server.shutdown()
        self.server.server_close()


def _cookie(headers: dict[str, str]) -> str:
    return headers["Set-Cookie"].split(";", 1)[0]


def test_pair_response_hides_identities_and_sets_voter_cookie(tmp_path: Path) -> None:
    with _Running(tmp_path, _pairs()) as running:
        status, body, headers = _call(f"{running.base}/api/votes/pair")

    assert status == 200
    assert "HttpOnly" in headers["Set-Cookie"] and "SameSite=Strict" in headers["Set-Cookie"]
    text = body.decode()
    for secret in ("alpha", "beta", "ta0", "tb0", "contestant"):
        assert secret not in text.replace("alpha answer", "").replace("beta answer", "")
    assert set(json.loads(body)) == {"pair_id", "task_id", "left", "right"}


def test_vote_reveals_identities_only_after_vote_and_feeds_gold_set(tmp_path: Path) -> None:
    with _Running(tmp_path, _pairs()) as running:
        _, body, headers = _call(f"{running.base}/api/votes/pair")
        cookie = _cookie(headers)
        pair = json.loads(body)
        status, voted, _ = _call(
            f"{running.base}/api/votes",
            method="POST",
            cookie=cookie,
            body=json.dumps({"pair_id": pair["pair_id"], "choice": "A"}).encode(),
        )

        assert status == 201
        revealed = json.loads(voted)["revealed"]
        assert revealed == {
            f"ta{pair['pair_id'][1:]}": "alpha",
            f"tb{pair['pair_id'][1:]}": "beta",
        }
        assert len(running.service.gold_set()) == 1
        # The vote is persisted so a restarted service sees it.
        reloaded = HumanVoteService(_pairs(), JsonlVoteStore(tmp_path / "votes.jsonl"))
        assert len(reloaded.votes) == 1


def test_vote_without_session_and_malformed_bodies_are_rejected(tmp_path: Path) -> None:
    with _Running(tmp_path, _pairs()) as running:
        no_session, _, _ = _call(
            f"{running.base}/api/votes", method="POST", body=b'{"pair_id":"p0","choice":"A"}'
        )
        _, _, headers = _call(f"{running.base}/api/votes/pair")
        cookie = _cookie(headers)
        bad_json, _, _ = _call(f"{running.base}/api/votes", method="POST", cookie=cookie, body=b"{")
        empty, _, _ = _call(f"{running.base}/api/votes", method="POST", cookie=cookie, body=b"")
        oversized, _, _ = _call(
            f"{running.base}/api/votes", method="POST", cookie=cookie, body=b" " * 5000
        )
        bad_choice, _, _ = _call(
            f"{running.base}/api/votes",
            method="POST",
            cookie=cookie,
            body=b'{"pair_id":"p0","choice":"nope"}',
        )

    assert (no_session, bad_json, empty, oversized, bad_choice) == (400, 400, 400, 400, 400)


def test_cross_origin_vote_is_refused_and_second_vote_conflicts(tmp_path: Path) -> None:
    with _Running(tmp_path, _pairs()) as running:
        _, body, headers = _call(f"{running.base}/api/votes/pair")
        cookie = _cookie(headers)
        payload = json.dumps({"pair_id": json.loads(body)["pair_id"], "choice": "tie"}).encode()
        cross, _, _ = _call(
            f"{running.base}/api/votes",
            method="POST",
            cookie=cookie,
            body=payload,
            extra={"Origin": "http://evil.example"},
        )
        first, _, _ = _call(f"{running.base}/api/votes", method="POST", cookie=cookie, body=payload)
        second, _, _ = _call(
            f"{running.base}/api/votes", method="POST", cookie=cookie, body=payload
        )

    assert (cross, first, second) == (403, 201, 409)


def test_twenty_votes_update_leaderboard_and_exhausted_voter_gets_204(tmp_path: Path) -> None:
    pairs = [
        Pair(
            pair_id=f"p{index}",
            task_id="t1",
            trial_a_id=f"ta{index}",
            contestant_a_id="alpha",
            content_a="a",
            trial_b_id=f"tb{index}",
            contestant_b_id="beta",
            content_b="b",
        )
        for index in range(20)
    ]
    with _Running(tmp_path, pairs) as running:
        cookie = ""
        for _ in range(20):
            status, body, headers = _call(f"{running.base}/api/votes/pair", cookie=cookie or None)
            assert status == 200
            cookie = cookie or _cookie(headers)
            pair = json.loads(body)
            # Pick the side that shows alpha's text so alpha wins every comparison.
            choice = "A" if pair["left"]["content"] == "a" else "B"
            vote_status, _, _ = _call(
                f"{running.base}/api/votes",
                method="POST",
                cookie=cookie,
                body=json.dumps({"pair_id": pair["pair_id"], "choice": choice}).encode(),
            )
            assert vote_status == 201
        done, _, _ = _call(f"{running.base}/api/votes/pair", cookie=cookie)
        _, board, _ = _call(f"{running.base}/api/votes/leaderboard")

    rows = json.loads(board)
    assert done == 204
    assert [row["contestant_id"] for row in rows] == ["alpha", "beta"]
    assert rows[0]["rating"] > rows[1]["rating"]
    assert rows[0]["comparisons"] == 20
