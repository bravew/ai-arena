"""Run the vendor's own login in the user's terminal."""

from __future__ import annotations

import shlex
import subprocess
from collections.abc import Mapping

from arena.providers.runtimes import probe_environment, spec_for_vendor
from arena.providers.runtimes.detect import find_binary


class LoginError(Exception):
    pass


def run_vendor_login(vendor: str, environ: Mapping[str, str]) -> None:
    """Hand the terminal to `claude auth login` or `codex login`. No key variable is forwarded."""
    spec = spec_for_vendor(vendor)
    if spec is None:
        raise LoginError(f"no local CLI for vendor {vendor!r}")
    binary = find_binary(spec, environ)
    if binary is None:
        raise LoginError(f"{spec.runtime} is not installed; run `{spec.install_hint}`")
    argv = [binary, *shlex.split(spec.login_hint)[1:]]
    try:
        completed = subprocess.run(argv, env=probe_environment(environ), check=False)
    except OSError as error:
        raise LoginError(f"could not run {spec.runtime}") from error
    if completed.returncode != 0:
        raise LoginError(f"`{spec.login_hint}` exited with status {completed.returncode}")
