"""CLI commands for the gateway, provider checks, and local diagnostics."""

from __future__ import annotations

import ipaddress
import os
import socket
from collections.abc import Callable
from pathlib import Path
from typing import Annotated, Any, cast
from urllib.parse import urljoin

import httpx
import typer
import uvicorn

from arena.catalog.config import load_catalog
from arena.gateway.bench import benchmark_mock
from arena.gateway.endpoints import EndpointResolver
from arena.gateway.litellm_adapter import create_production_app
from arena.providers.config import Provider, load_providers
from arena.providers.subscriptions import (
    SubscriptionError,
    SubscriptionStore,
    Vendor,
    import_subscription,
)

app = typer.Typer(name="gateway", no_args_is_help=True)
providers_app = typer.Typer(name="providers", no_args_is_help=True)


def build_gateway_app(
    *,
    serve: Callable[..., None] = uvicorn.run,
    config: Path = Path("providers.yaml"),
    catalog: Path = Path("catalog/models.yaml"),
) -> typer.Typer:
    """Build an injectable command group for the standalone gateway process."""
    command_app = typer.Typer(name="gateway", no_args_is_help=True)

    @command_app.callback(invoke_without_command=True)
    def gateway(
        ctx: typer.Context,
        host: Annotated[
            str, typer.Option(help="Loopback or Docker bridge bind address.")
        ] = "127.0.0.1",
        port: Annotated[int, typer.Option(min=1, max=65535)] = 8765,
        providers_file: Annotated[Path, typer.Option("--config")] = config,
        catalog_file: Annotated[Path, typer.Option("--catalog")] = catalog,
        home: Annotated[Path | None, typer.Option("--home")] = None,
    ) -> None:
        """Start the local arena gateway."""
        if ctx.invoked_subcommand is not None:
            return
        if not _allowed_bind_host(host):
            typer.echo("gateway bind host must be loopback or a Docker bridge address", err=True)
            raise typer.Exit(code=2)
        try:
            provider_config = load_providers(providers_file)
            model_catalog = load_catalog(catalog_file)
        except (OSError, ValueError) as error:
            typer.echo(f"error: cannot load gateway configuration: {error}", err=True)
            raise typer.Exit(code=1) from error
        application = create_production_app(
            provider_config,
            model_catalog,
            endpoint_resolver=EndpointResolver(subscriptions=_subscription_store(home)),
        )
        serve(app=application, host=host, port=port)

    @command_app.command("bench")
    def bench(
        iterations: Annotated[int, typer.Option("--iterations", min=1)] = 1000,
    ) -> None:
        """Measure mock gateway overhead against a local fake upstream."""
        result = benchmark_mock(iterations)
        typer.echo(
            f"mock: {result.iterations} iterations, p50 {result.p50_ms:.3f} ms, "
            f"p95 {result.p95_ms:.3f} ms, added p50 {result.added_p50_ms:.3f} ms "
            "(in-process mock vs. fake-upstream baseline)"
        )

    return command_app


@providers_app.command("detect")
def detect_provider(
    url: Annotated[str, typer.Argument(help="Provider base URL to probe.")],
    timeout: Annotated[float, typer.Option("--timeout", min=0.1)] = 3.0,
) -> None:
    """Probe a base URL for supported OpenAI-compatible or Anthropic APIs."""
    results = _probe_protocols(url, timeout)
    detected = [protocol for protocol, ok in results.items() if ok]
    if detected:
        typer.echo("Detected protocols: " + ", ".join(detected))
    else:
        typer.echo("No supported protocols detected")
        raise typer.Exit(code=1)


@providers_app.command("test")
def test_providers(
    config: Annotated[Path, typer.Option("--config", exists=True, dir_okay=False)] = Path(
        "providers.yaml"
    ),
    timeout: Annotated[float, typer.Option("--timeout", min=0.1)] = 10.0,
) -> None:
    """Send one-token requests for every configured API key/account."""
    try:
        providers = load_providers(config)
    except (OSError, ValueError) as error:
        typer.echo(f"error: {error}", err=True)
        raise typer.Exit(code=1) from error
    failures = 0
    for provider in providers.providers:
        if not provider.enabled or provider.kind is not None:
            continue
        if provider.subscription is not None:
            typer.echo(
                f"{provider.id}/{provider.subscription.vendor}: skipped "
                "(subscription adapter unavailable)"
            )
            failures += 1
            continue
        apis = _provider_apis(provider)
        if not apis:
            typer.echo(f"{provider.id}: failed (no API protocols configured)")
            failures += 1
            continue
        for key in provider.keys:
            secret = os.environ.get(_environment_name(key.env))
            if not secret:
                typer.echo(f"{provider.id}/{key.id}: failed (environment variable is unset)")
                failures += 1
                continue
            protocol, endpoint = next(iter(apis.items()))
            try:
                status = _test_provider_endpoint(protocol, endpoint, secret, timeout)
            except (httpx.HTTPError, ValueError) as error:
                typer.echo(f"{provider.id}/{key.id}: failed ({type(error).__name__})")
                failures += 1
            else:
                if status < 400:
                    typer.echo(f"{provider.id}/{key.id}: ok (HTTP {status})")
                else:
                    typer.echo(f"{provider.id}/{key.id}: failed (HTTP {status})")
                    failures += 1
    if failures:
        raise typer.Exit(code=1)


_VENDORS: tuple[Vendor, ...] = ("claude", "chatgpt", "copilot")


def _subscription_store(home: Path | None) -> SubscriptionStore:
    return SubscriptionStore((home or Path.home() / ".arena") / "subscriptions")


@app.command("import")
def import_subscription_command(
    vendor: Annotated[str, typer.Argument()],
    sign_in: Annotated[
        Path, typer.Option("--from", exists=True, dir_okay=False, help="Native CLI sign-in file.")
    ],
    home: Annotated[Path | None, typer.Option("--home")] = None,
) -> None:
    """Copy a vendor CLI sign-in into the daemon's private subscription store."""
    if vendor not in _VENDORS:
        typer.echo(f"error: unknown vendor {vendor!r}; use one of {', '.join(_VENDORS)}", err=True)
        raise typer.Exit(code=2)
    try:
        account = import_subscription(vendor, sign_in, _subscription_store(home))
    except SubscriptionError as error:
        typer.echo(f"error: {error}", err=True)
        raise typer.Exit(code=1) from error
    typer.echo(f"imported {account.display_name}; the source sign-in was not modified")


@providers_app.command("import")
def providers_import(
    vendor: Annotated[str, typer.Argument()],
    sign_in: Annotated[
        Path, typer.Option("--from", exists=True, dir_okay=False, help="Native CLI sign-in file.")
    ],
    home: Annotated[Path | None, typer.Option("--home")] = None,
) -> None:
    """Import a subscription account (alias of `arena import`)."""
    import_subscription_command(vendor, sign_in, home)


def create_doctor_command() -> Callable[..., None]:
    """Create the doctor command function for mounting on the root Typer app."""

    def doctor(
        home: Annotated[Path | None, typer.Option("--home")] = None,
        gateway: Annotated[str, typer.Option("--gateway")] = "http://127.0.0.1:8765",
        timeout: Annotated[float, typer.Option("--timeout", min=0.1)] = 2.0,
    ) -> None:
        """Check Docker, gateway health, providers, and local arena storage."""
        failures = 0
        if not _docker_available():
            typer.echo("Docker: unavailable")
            failures += 1
        else:
            typer.echo("Docker: available")
        healthy = gateway_health(gateway, timeout)
        typer.echo(f"Gateway: {'available' if healthy else 'unavailable'} ({gateway})")
        failures += not healthy
        directory = home or (Path.home() / ".arena")
        if directory.exists() and not os.access(directory, os.W_OK):
            typer.echo(f"Arena home: not writable ({directory})")
            failures += 1
        else:
            typer.echo(
                f"Arena home: {'writable' if directory.exists() else 'not created'} ({directory})"
            )
        if failures:
            raise typer.Exit(code=1)

    return doctor


def gateway_health(url: str, timeout: float) -> bool:
    """Return whether the local gateway health endpoint responds successfully."""
    try:
        response = httpx.get(
            urljoin(url.rstrip("/") + "/", "arena/health"),
            headers={"Authorization": "Bearer arena-ops"},
            timeout=timeout,
        )
    except httpx.HTTPError:
        return False
    return response.is_success


def _docker_available() -> bool:
    import shutil

    return shutil.which("docker") is not None and socket.gethostname() != ""


def _allowed_bind_host(host: str) -> bool:
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return host in {"host.docker.internal", "gateway.docker.internal"}
    return address.is_loopback or (address.is_private and not address.is_unspecified)


def _probe_protocols(url: str, timeout: float) -> dict[str, bool]:
    base = url.rstrip("/") + "/"
    probes = {
        "chat": ("GET", "v1/models"),
        "responses": ("GET", "v1/models"),
        "anthropic": ("POST", "v1/messages"),
        "gemini": ("GET", "v1beta/models"),
    }
    found: dict[str, bool] = {}
    for protocol, (method, path) in probes.items():
        try:
            response = httpx.request(method, urljoin(base, path), timeout=timeout)
        except httpx.HTTPError:
            found[protocol] = False
        else:
            found[protocol] = response.status_code not in {404, 405, 501}
    return found


def _provider_apis(provider: Provider) -> dict[str, str]:
    extra: dict[str, Any] = provider.model_extra or {}
    apis = extra.get("apis", {})
    if not isinstance(apis, dict):
        return {}
    raw_apis = cast(dict[str, Any], apis)
    return {key: value for key, value in raw_apis.items() if isinstance(value, str)}


def _environment_name(reference: str) -> str:
    return reference[2:-1] if reference.startswith("${") and reference.endswith("}") else reference


def _test_provider_endpoint(protocol: str, endpoint: str, secret: str, timeout: float) -> int:
    base = endpoint.rstrip("/")
    headers = {"Authorization": f"Bearer {secret}"}
    if protocol == "anthropic":
        headers = {"x-api-key": secret, "anthropic-version": "2023-06-01"}
        response = httpx.post(
            urljoin(base + "/", "v1/messages"),
            headers=headers,
            json={
                "model": "claude-3-haiku-20240307",
                "max_tokens": 1,
                "messages": [{"role": "user", "content": "ping"}],
            },
            timeout=timeout,
        )
    elif protocol == "chat":
        response = httpx.post(
            urljoin(base + "/", "chat/completions"),
            headers=headers,
            json={
                "model": "gpt-4o-mini",
                "max_tokens": 1,
                "messages": [{"role": "user", "content": "ping"}],
            },
            timeout=timeout,
        )
    elif protocol == "responses":
        response = httpx.post(
            urljoin(base + "/", "responses"),
            headers=headers,
            json={"model": "gpt-4o-mini", "max_output_tokens": 1, "input": "ping"},
            timeout=timeout,
        )
    else:
        raise ValueError(f"provider test does not support protocol {protocol!r}")
    return response.status_code


app.add_typer(build_gateway_app(), name="gateway")
app.add_typer(providers_app, name="providers")
