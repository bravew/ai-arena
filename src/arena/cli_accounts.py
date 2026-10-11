"""`arena providers add | list | forget`: onboarding for `local-cli` and API providers.

No command here prints a token. Accounts are shown by a redacted id, and an API key is only ever
named by its environment variable.
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Annotated

import typer

from arena.agents.local.signin import SignInError
from arena.providers.accounts import (
    AccountError,
    AccountStore,
    LoginError,
    discover,
    discovery_enabled,
    read_existing,
    run_vendor_login,
)
from arena.providers.config import load_providers

_VENDOR_NAMES = {"claude": "claude", "codex": "chatgpt", "chatgpt": "chatgpt"}
_ENV_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

SUBSCRIPTION_WARNING = (
    "warning: this runs the vendor's own CLI on your subscription. Check the vendor's terms for "
    "automated use, and expect trials to count against your plan's usage limits."
)


def account_store(home: Path | None) -> AccountStore:
    return AccountStore((home or Path.home() / ".arena") / "accounts")


def _fail(message: str, code: int = 1) -> typer.Exit:
    typer.echo(f"error: {message}", err=True)
    return typer.Exit(code=code)


def add_command(
    target: Annotated[
        str, typer.Argument(metavar="TARGET", help="claude, codex, or an API provider id.")
    ],
    env: Annotated[
        str | None, typer.Option("--env", help="API provider: the variable that holds the key.")
    ] = None,
    no_login: Annotated[
        bool,
        typer.Option("--no-login", help="Register an existing sign-in; skip the vendor login."),
    ] = False,
    config: Annotated[Path, typer.Option("--config", dir_okay=False)] = Path("providers.yaml"),
    home: Annotated[Path | None, typer.Option("--home")] = None,
) -> None:
    """Add a subscription account (the vendor's own login) or check an API key variable."""
    vendor = _VENDOR_NAMES.get(target)
    if vendor is None:
        _add_api_provider(target, env, config)
        return
    environ = dict(os.environ)
    typer.echo(SUBSCRIPTION_WARNING)
    if not no_login:
        try:
            run_vendor_login(vendor, environ)
        except LoginError as error:
            raise _fail(str(error)) from error
    try:
        sign_in = read_existing(vendor, environ)
    except SignInError as error:
        hint = (
            "; on macOS run `arena providers import claude --keychain`"
            if vendor == "claude"
            else ""
        )
        raise _fail(f"{error}{hint}") from error
    try:
        account = account_store(home).save(vendor, sign_in, source="cli-sign-in-file")
    except (AccountError, OSError) as error:
        raise _fail(str(error)) from error
    typer.echo(
        f"added {target} account {account.account_id}; the CLI's own sign-in was not modified"
    )


def _add_api_provider(provider_id: str, env: str | None, config: Path) -> None:
    try:
        providers = load_providers(config)
    except (OSError, ValueError) as error:
        raise _fail(str(error)) from error
    provider = next((item for item in providers.providers if item.id == provider_id), None)
    if provider is None:
        raise _fail(f"{provider_id!r} is not claude, codex, or a provider in {config}", 2)
    if provider.subscription is not None:
        raise _fail(
            f"{provider_id} is a subscription provider; use `arena providers add <vendor>`", 2
        )
    if env is None or not _ENV_NAME.match(env):
        raise _fail("give the variable that holds the key: --env NAME", 2)
    state = "set" if os.environ.get(env) else "not set"
    typer.echo(f"{provider_id}: {env} is {state} in this shell (the value is never read or shown)")
    typer.echo(f"keys line for {config}:  keys: [{{id: main, env: {env}}}]")


def list_command(home: Annotated[Path | None, typer.Option("--home")] = None) -> None:
    """List stored accounts and, unless discovery is off, sign-ins found on this machine."""
    store = account_store(home)
    environ = dict(os.environ)
    stored = store.list()
    for account in stored:
        typer.echo(
            f"{account.vendor}: {account.account_id} stored ({account.source}, {account.saved_at})"
        )
    stored_vendors = {account.vendor for account in stored}
    for found in discover(environ):
        if found.vendor not in stored_vendors:
            typer.echo(f"{found.vendor}: {found.account_id} found at {found.location}, not added")
    if not discovery_enabled(environ):
        typer.echo("sign-in discovery is off (ARENA_DISCOVER_SIGNINS)")
    elif not stored:
        typer.echo("run `arena providers add claude` or `arena providers add codex` to add one")


def forget_command(
    vendor: Annotated[str, typer.Argument(help="claude or codex.")],
    home: Annotated[Path | None, typer.Option("--home")] = None,
) -> None:
    """Delete the arena's stored copy of a sign-in. The CLI's own sign-in is not touched."""
    name = _VENDOR_NAMES.get(vendor)
    if name is None:
        raise _fail(f"unknown vendor {vendor!r}; use claude or codex", 2)
    try:
        removed = account_store(home).forget(name)
    except (AccountError, OSError) as error:
        raise _fail(str(error)) from error
    typer.echo(f"forgot {vendor}" if removed else f"no stored {vendor} account")
