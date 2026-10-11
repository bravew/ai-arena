from __future__ import annotations

import json
import threading
import urllib.error
import urllib.request
from pathlib import Path

from arena.core.cas import ArtifactStore
from arena.core.store import Store
from arena.server.app import create_server
from arena.server.vote_pairs import build_pairs


def _seed(home: Path) -> None:
    cas = ArtifactStore(home / "artifacts")
    with Store(home / "arena.db") as store:
        store.execute("INSERT INTO runs (id, config_json, status) VALUES ('r', '{}', 'succeeded')")
        rows = [
            ("t1", "alpha", "task", "text/markdown", b"alpha answer"),
            ("t2", "beta", "task", "text/plain", b"beta answer"),
            ("t3", "beta", "task", "text/plain", b"beta repeat"),
            ("t4", "gamma", "task", "text/html", b"<p>not text</p>"),
        ]
        for trial, who, task, mime, payload in rows:
            store.execute(
                "INSERT INTO trials (id, run_id, contestant_id, task_id, attempt, status) "
                "VALUES (?, 'r', ?, ?, ?, 'succeeded')",
                (trial, who, task, int(trial[1:])),
            )
            store.execute(
                "INSERT INTO trial_artifacts(trial_id, path, sha256, mime, render_hint) "
                "VALUES (?, 'out', ?, ?, 'text')",
                (trial, cas.put(payload), mime),
            )


def test_pairs_join_different_contestants_with_text_artifacts_only(tmp_path: Path) -> None:
    _seed(tmp_path)

    pairs = build_pairs(tmp_path)

    assert {(p.trial_a_id, p.trial_b_id) for p in pairs} == {("t1", "t2"), ("t1", "t3")}
    assert all(p.task_id == "r/task" for p in pairs)


def test_no_database_means_no_pairs(tmp_path: Path) -> None:
    assert build_pairs(tmp_path) == []


def test_create_server_serves_votes_from_stored_trials(tmp_path: Path) -> None:
    _seed(tmp_path)
    server = create_server("127.0.0.1", 0, tmp_path)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        base = f"http://127.0.0.1:{server.server_address[1]}"
        with urllib.request.urlopen(f"{base}/api/votes/pair", timeout=3) as response:
            pair = json.loads(response.read())
            cookie = response.headers["Set-Cookie"].split(";", 1)[0]
        request = urllib.request.Request(
            f"{base}/api/votes",
            data=json.dumps({"pair_id": pair["pair_id"], "choice": "tie"}).encode(),
            headers={"Cookie": cookie, "Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=3) as voted:
            assert voted.status == 201
    finally:
        server.shutdown()
        server.server_close()

    assert set(pair) == {"pair_id", "task_id", "left", "right"}
    assert len((tmp_path / "votes.jsonl").read_text().splitlines()) == 1


def test_gold_labels_use_the_pairs_own_order_and_the_majority_verdict(tmp_path: Path) -> None:
    from arena.server.votes import Choice, HumanVoteService, JsonlVoteStore

    _seed(tmp_path)
    service = HumanVoteService(build_pairs(tmp_path), JsonlVoteStore(tmp_path / "votes.jsonl"))
    pair = next(p for p in service.pairs if (p.trial_a_id, p.trial_b_id) == ("t1", "t2"))
    other = next(p for p in service.pairs if p.pair_id != pair.pair_id)
    # Three voters each prefer trial t1, whichever side it was shown on.
    for voter in ("v1", "v2", "v3"):
        shown_left = service.next_pair(voter, seed=0)
        assert shown_left is not None
        blind = service._oriented_pair(pair, voter)  # pyright: ignore[reportPrivateUsage]
        service.cast_vote(
            voter, pair.pair_id, Choice.A if blind.left_trial_id == "t1" else Choice.B
        )
    service.cast_vote("v1", other.pair_id, Choice.BOTH_BAD)

    rows = {row["item_id"]: row for row in service.gold_labels()}

    assert rows[pair.pair_id]["human_verdict"] == "a"
    assert rows[pair.pair_id]["votes"] == {"a": 3, "b": 0, "tie": 0, "both_bad": 0}
    assert rows[other.pair_id]["human_verdict"] == "both_bad"
    assert rows[pair.pair_id]["length_a"] == len("alpha answer")


def test_export_gold_command_writes_one_row_per_voted_pair(tmp_path: Path) -> None:
    from typer.testing import CliRunner

    from arena.cli import app
    from arena.server.votes import Choice, HumanVoteService, JsonlVoteStore

    _seed(tmp_path)
    service = HumanVoteService(build_pairs(tmp_path), JsonlVoteStore(tmp_path / "votes.jsonl"))
    service.cast_vote("v1", service.pairs[0].pair_id, Choice.TIE)
    out = tmp_path / "gold" / "human.jsonl"

    result = CliRunner().invoke(
        app, ["votes", "export-gold", "--home", str(tmp_path), "--out", str(out)]
    )

    assert result.exit_code == 0, result.output
    rows = [json.loads(line) for line in out.read_text().splitlines()]
    assert [row["human_verdict"] for row in rows] == ["tie"]


def test_viewer_routes_fall_back_to_the_app_but_unknown_api_paths_do_not(tmp_path: Path) -> None:
    dist = Path(__file__).resolve().parents[2] / "web" / "dist"
    if not (dist / "index.html").is_file():
        import pytest

        pytest.skip("web/dist is not built")
    server = create_server("127.0.0.1", 0, tmp_path)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        base = f"http://127.0.0.1:{server.server_address[1]}"
        with urllib.request.urlopen(f"{base}/vote", timeout=3) as response:
            assert b'<div id="root"' in response.read()
        try:
            urllib.request.urlopen(f"{base}/api/nothing", timeout=3)
            raise AssertionError("expected 404")
        except urllib.error.HTTPError as error:
            assert error.code == 404
        try:
            urllib.request.urlopen(f"{base}/missing.js", timeout=3)
            raise AssertionError("expected 404")
        except urllib.error.HTTPError as error:
            assert error.code == 404
    finally:
        server.shutdown()
        server.server_close()


def test_a_browsers_same_origin_vote_is_accepted_and_a_foreign_origin_is_not(
    tmp_path: Path,
) -> None:
    _seed(tmp_path)
    server = create_server("127.0.0.1", 0, tmp_path)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        base = f"http://127.0.0.1:{server.server_address[1]}"
        with urllib.request.urlopen(f"{base}/api/votes/pair", timeout=3) as response:
            pair = json.loads(response.read())
            cookie = response.headers["Set-Cookie"].split(";", 1)[0]

        def post(origin: str) -> int:
            request = urllib.request.Request(
                f"{base}/api/votes",
                data=json.dumps({"pair_id": pair["pair_id"], "choice": "tie"}).encode(),
                headers={"Cookie": cookie, "Content-Type": "application/json", "Origin": origin},
                method="POST",
            )
            try:
                with urllib.request.urlopen(request, timeout=3) as voted:
                    return voted.status
            except urllib.error.HTTPError as error:
                return error.code

        assert post("https://foreign.example") == 403
        assert post(base) == 201
    finally:
        server.shutdown()
        server.server_close()
