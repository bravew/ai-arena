from __future__ import annotations

import json
import threading
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
