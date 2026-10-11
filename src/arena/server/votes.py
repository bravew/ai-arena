"""Framework-independent blind human voting service and JSONL persistence."""

from __future__ import annotations

import hashlib
import json
import random
from dataclasses import asdict, dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any, Protocol, cast

from arena.stats.pairwise import Outcome, PairwiseJudgment
from arena.stats.ratings import Ratings, bradley_terry


class Choice(StrEnum):
    A = "A"
    B = "B"
    TIE = "tie"
    BOTH_BAD = "both_bad"


@dataclass(frozen=True)
class Submission:
    content: str


@dataclass(frozen=True)
class Pair:
    pair_id: str
    task_id: str
    trial_a_id: str
    contestant_a_id: str
    content_a: str
    trial_b_id: str
    contestant_b_id: str
    content_b: str

    def __post_init__(self) -> None:
        if not all((self.pair_id, self.task_id, self.trial_a_id, self.trial_b_id)):
            raise ValueError("pair and trial ids must be non-empty")
        if self.trial_a_id == self.trial_b_id or self.contestant_a_id == self.contestant_b_id:
            raise ValueError("pair trials and contestants must differ")


@dataclass(frozen=True)
class BlindPair:
    pair_id: str
    task_id: str
    left: Submission
    right: Submission
    left_trial_id: str
    right_trial_id: str

    def public_payload(self) -> dict[str, Any]:
        return {
            "pair_id": self.pair_id,
            "task_id": self.task_id,
            "left": asdict(self.left),
            "right": asdict(self.right),
        }

    def identity_order(self) -> tuple[str, str]:
        return self.left_trial_id, self.right_trial_id


@dataclass(frozen=True)
class Vote:
    pair_id: str
    task_id: str
    voter_id: str
    left_trial_id: str
    right_trial_id: str
    left_contestant_id: str
    right_contestant_id: str
    choice: Choice


@dataclass(frozen=True)
class VoteResult:
    vote: Vote
    revealed: dict[str, str]


@dataclass(frozen=True)
class HttpResponse:
    status: int
    body: dict[str, Any]


class VoteStore(Protocol):
    def read(self) -> tuple[Vote, ...]: ...

    def append(self, vote: Vote) -> None: ...


class JsonlVoteStore:
    """Append-only store; malformed records are errors, never an empty vote set."""

    def __init__(self, path: Path) -> None:
        self.path = path

    def read(self) -> tuple[Vote, ...]:
        if not self.path.exists():
            return ()
        try:
            lines = self.path.read_text(encoding="utf-8").splitlines()
            votes = tuple(_vote_from_json(json.loads(line)) for line in lines if line)
        except (OSError, json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
            raise RuntimeError(f"cannot read vote store {self.path}: {exc}") from exc
        unique = {(vote.voter_id, vote.pair_id) for vote in votes}
        if len(unique) != len(votes):
            raise RuntimeError(f"duplicate voter/pair entry in vote store {self.path}")
        return votes

    def append(self, vote: Vote) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        encoded = json.dumps(asdict(vote), sort_keys=True, separators=(",", ":"))
        try:
            with self.path.open("a", encoding="utf-8") as stream:
                stream.write(encoded + "\n")
                stream.flush()
        except OSError as exc:
            raise RuntimeError(f"cannot write vote store {self.path}: {exc}") from exc


def _vote_from_json(value: Any) -> Vote:
    if not isinstance(value, dict):
        raise ValueError("vote record must be an object")
    record = cast(dict[str, Any], value)
    fields = (
        "pair_id",
        "task_id",
        "voter_id",
        "left_trial_id",
        "right_trial_id",
        "left_contestant_id",
        "right_contestant_id",
        "choice",
    )
    if any(not isinstance(record.get(field), str) for field in fields):
        raise ValueError("vote record fields must be strings")
    return Vote(
        pair_id=record["pair_id"],
        task_id=record["task_id"],
        voter_id=record["voter_id"],
        left_trial_id=record["left_trial_id"],
        right_trial_id=record["right_trial_id"],
        left_contestant_id=record["left_contestant_id"],
        right_contestant_id=record["right_contestant_id"],
        choice=Choice(record["choice"]),
    )


class HumanVoteService:
    """Blind pairwise service; contestant identity is revealed only on vote result."""

    def __init__(self, pairs: list[Pair] | tuple[Pair, ...], store: VoteStore) -> None:
        self.pairs = tuple(pairs)
        if len({pair.pair_id for pair in self.pairs}) != len(self.pairs):
            raise ValueError("pair ids must be unique")
        self._pairs = {pair.pair_id: pair for pair in self.pairs}
        self.store = store
        self._votes = list(store.read())
        unknown = {vote.pair_id for vote in self._votes} - self._pairs.keys()
        if unknown:
            raise ValueError(f"votes refer to unknown pairs: {sorted(unknown)}")

    @property
    def votes(self) -> tuple[Vote, ...]:
        return tuple(self._votes)

    def next_pair(self, voter_id: str, *, seed: int | None = None) -> BlindPair | None:
        if not voter_id:
            raise ValueError("voter id must be non-empty")
        completed = {vote.pair_id for vote in self._votes if vote.voter_id == voter_id}
        available = [pair for pair in self.pairs if pair.pair_id not in completed]
        if not available:
            return None
        pair = random.Random(seed).choice(available)
        if _orientation(voter_id, pair.pair_id, seed):
            left = Submission(pair.content_a)
            right = Submission(pair.content_b)
            left_id, right_id = pair.trial_a_id, pair.trial_b_id
        else:
            left = Submission(pair.content_b)
            right = Submission(pair.content_a)
            left_id, right_id = pair.trial_b_id, pair.trial_a_id
        return BlindPair(pair.pair_id, pair.task_id, left, right, left_id, right_id)

    def cast_vote(self, voter_id: str, pair_id: str, choice: Choice | str) -> VoteResult:
        if not voter_id:
            raise ValueError("voter id must be non-empty")
        try:
            selected = Choice(choice)
            pair = self._pairs[pair_id]
        except (ValueError, KeyError) as exc:
            raise ValueError("unknown pair or choice") from exc
        if any(vote.voter_id == voter_id and vote.pair_id == pair_id for vote in self._votes):
            raise ValueError("voter already voted on this pair")
        blind = self._oriented_pair(pair, voter_id)
        left_id, right_id = blind.identity_order()
        contestants = {pair.trial_a_id: pair.contestant_a_id, pair.trial_b_id: pair.contestant_b_id}
        vote = Vote(
            pair_id,
            pair.task_id,
            voter_id,
            left_id,
            right_id,
            contestants[left_id],
            contestants[right_id],
            selected,
        )
        self.store.append(vote)
        self._votes.append(vote)
        return VoteResult(vote, {left_id: contestants[left_id], right_id: contestants[right_id]})

    def _oriented_pair(self, pair: Pair, voter_id: str) -> BlindPair:
        if _orientation(voter_id, pair.pair_id, None):
            left = Submission(pair.content_a)
            right = Submission(pair.content_b)
            left_id, right_id = pair.trial_a_id, pair.trial_b_id
        else:
            left = Submission(pair.content_b)
            right = Submission(pair.content_a)
            left_id, right_id = pair.trial_b_id, pair.trial_a_id
        return BlindPair(pair.pair_id, pair.task_id, left, right, left_id, right_id)

    def gold_set(self) -> tuple[Vote, ...]:
        """Human-labeled pair preferences, retaining both-bad labels explicitly."""
        return self.votes

    def gold_labels(self) -> list[dict[str, Any]]:
        """One row per voted pair: the majority human verdict, in the pair's own A/B order.

        `human_verdict` is 'a', 'b', 'tie' or 'both_bad'. A split with no single leader is 'tie'.
        These rows carry no judge verdict, so they join a judge's output by `item_id`; they are
        not a `PairJudgment` file.
        """
        rows: list[dict[str, Any]] = []
        for pair in self.pairs:
            counts = {"a": 0, "b": 0, "tie": 0, "both_bad": 0}
            for vote in self._votes:
                if vote.pair_id != pair.pair_id:
                    continue
                if vote.choice == Choice.TIE:
                    counts["tie"] += 1
                elif vote.choice == Choice.BOTH_BAD:
                    counts["both_bad"] += 1
                else:
                    left_wins = vote.choice == Choice.A
                    winner = vote.left_trial_id if left_wins else vote.right_trial_id
                    counts["a" if winner == pair.trial_a_id else "b"] += 1
            if not any(counts.values()):
                continue
            top = max(counts.values())
            leaders = [name for name, number in counts.items() if number == top]
            rows.append(
                {
                    "item_id": pair.pair_id,
                    "task_id": pair.task_id,
                    "trial_a_id": pair.trial_a_id,
                    "trial_b_id": pair.trial_b_id,
                    "contestant_a_id": pair.contestant_a_id,
                    "contestant_b_id": pair.contestant_b_id,
                    "length_a": len(pair.content_a),
                    "length_b": len(pair.content_b),
                    "human_verdict": leaders[0] if len(leaders) == 1 else "tie",
                    "votes": counts,
                }
            )
        return rows

    def leaderboard(self, *, samples: int = 2_000) -> Ratings:
        judgments = [
            PairwiseJudgment(
                vote.left_contestant_id,
                vote.right_contestant_id,
                cast(Outcome, _rating_outcome(vote.choice)),
                "human",
                vote.task_id,
            )
            for vote in self._votes
        ]
        return bradley_terry(judgments, judge="human", samples=samples)

    def handle_get_pair(self, voter_id: str, *, seed: int | None = None) -> HttpResponse:
        pair = self.next_pair(voter_id, seed=seed)
        if pair is None:
            return HttpResponse(204, {})
        return HttpResponse(200, pair.public_payload())

    def handle_post_vote(self, voter_id: str, payload: Any) -> HttpResponse:
        if not isinstance(payload, dict):
            return HttpResponse(400, {"error": "pair_id and choice are required"})
        body = cast(dict[str, Any], payload)
        pair_id, choice = body.get("pair_id"), body.get("choice")
        if not isinstance(pair_id, str) or not isinstance(choice, str):
            return HttpResponse(400, {"error": "pair_id and choice are required"})
        try:
            result = self.cast_vote(voter_id, pair_id, choice)
        except ValueError as exc:
            message = str(exc)
            return HttpResponse(409 if "already voted" in message else 400, {"error": message})
        return HttpResponse(201, {"vote": asdict(result.vote), "revealed": result.revealed})


def _rating_outcome(choice: Choice) -> str:
    return "left" if choice == Choice.A else "right" if choice == Choice.B else "tie"


def _orientation(voter_id: str, pair_id: str, seed: int | None) -> bool:
    del seed  # The test seed selects a pair; orientation must survive the separate POST request.
    digest = hashlib.sha256(f"{voter_id}\0{pair_id}".encode()).digest()
    return bool(digest[0] & 1)
