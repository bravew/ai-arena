from __future__ import annotations

import json

import pytest

from arena.server.votes import Choice, HumanVoteService, JsonlVoteStore, Pair


def _service(tmp_path):
    pairs = [
        Pair(
            "pair-1",
            "task-1",
            "trial-a",
            "contestant-a",
            "Answer alpha",
            "trial-b",
            "contestant-b",
            "Answer beta",
        )
    ]
    return HumanVoteService(pairs, JsonlVoteStore(tmp_path / "votes.jsonl"))


def test_blind_pair_hides_identity_until_vote_then_reveals(tmp_path) -> None:
    service = _service(tmp_path)
    blind = service.next_pair("voter-1", seed=1)
    assert blind is not None
    public = json.dumps(blind.public_payload())
    assert "contestant-a" not in public
    assert "contestant-b" not in public
    assert "trial-a" not in public
    assert "trial-b" not in public

    result = service.cast_vote("voter-1", blind.pair_id, Choice.A)
    assert set(result.revealed.values()) == {"contestant-a", "contestant-b"}
    assert set(result.revealed.values()) == {"contestant-a", "contestant-b"}


def test_votes_feed_human_leaderboard_and_gold_set(tmp_path) -> None:
    service = _service(tmp_path)
    first = service.next_pair("voter-1", seed=1)
    assert first is not None
    service.cast_vote("voter-1", first.pair_id, Choice.A)
    second = service.next_pair("voter-2", seed=1)
    assert second is not None
    service.cast_vote("voter-2", second.pair_id, Choice.TIE)

    leaderboard = service.leaderboard(samples=20)
    assert leaderboard.judge == "human"
    assert len(leaderboard.entries) == 2
    assert service.gold_set() == service.votes
    assert service.gold_set()[0].choice in (Choice.A, Choice.B, Choice.TIE, Choice.BOTH_BAD)


def test_vote_store_survives_restart_and_rejects_duplicate_voter_pair(tmp_path) -> None:
    store_path = tmp_path / "votes.jsonl"
    service = HumanVoteService(
        [Pair("pair-1", "task-1", "ta", "ca", "A", "tb", "cb", "B")],
        JsonlVoteStore(store_path),
    )
    pair = service.next_pair("voter-1", seed=1)
    assert pair is not None
    service.cast_vote("voter-1", pair.pair_id, Choice.BOTH_BAD)

    restarted = HumanVoteService(service.pairs, JsonlVoteStore(store_path))
    assert len(restarted.votes) == 1
    with pytest.raises(ValueError, match="already voted"):
        restarted.cast_vote("voter-1", pair.pair_id, Choice.A)


def test_http_adapter_returns_blind_payload_then_vote_result(tmp_path) -> None:
    service = _service(tmp_path)
    response = service.handle_get_pair("voter-1", seed=1)
    assert response.status == 200
    assert "contestant-a" not in json.dumps(response.body)
    vote = service.handle_post_vote("voter-1", {"pair_id": response.body["pair_id"], "choice": "A"})
    assert vote.status == 201
    assert "revealed" in vote.body
