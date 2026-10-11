"""`arena votes`: export human blind-vote labels as gold-set rows."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Annotated

import typer

from arena.cli import app
from arena.server.vote_pairs import build_pairs
from arena.server.votes import HumanVoteService, JsonlVoteStore

votes_app = typer.Typer(name="votes", help="Human blind-vote commands.", no_args_is_help=True)


@votes_app.command("export-gold")
def export_gold(
    home: Annotated[Path, typer.Option("--home", help="Arena data directory.")],
    out: Annotated[Path | None, typer.Option("--out", help="Write JSONL here, not stdout.")] = None,
) -> None:
    """Write one majority-verdict row per voted pair, from `votes.jsonl` in the data home."""
    try:
        service = HumanVoteService(build_pairs(home), JsonlVoteStore(home / "votes.jsonl"))
    except (RuntimeError, ValueError) as error:
        typer.echo(str(error), err=True)
        raise typer.Exit(code=2) from error
    text = "".join(json.dumps(row, sort_keys=True) + "\n" for row in service.gold_labels())
    if out is None:
        sys.stdout.write(text)
        return
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(text, encoding="utf-8")
    typer.echo(f"wrote {len(text.splitlines())} labels to {out}", err=True)


app.add_typer(votes_app)
