"""Detect the vendor CLIs (`claude`, `codex`) and report their sign-in state."""

from __future__ import annotations

from arena.providers.runtimes.detect import (
    AuthState,
    ProbeResult,
    RuntimeReport,
    RuntimeStatus,
    check_model,
    detect_all,
    detect_runtime,
    probe_environment,
    run_probe,
)
from arena.providers.runtimes.specs import CLAUDE, CODEX, SPECS, RuntimeSpec, spec_for_vendor

__all__ = [
    "CLAUDE",
    "CODEX",
    "SPECS",
    "AuthState",
    "ProbeResult",
    "RuntimeReport",
    "RuntimeSpec",
    "RuntimeStatus",
    "check_model",
    "detect_all",
    "detect_runtime",
    "probe_environment",
    "run_probe",
    "spec_for_vendor",
]
