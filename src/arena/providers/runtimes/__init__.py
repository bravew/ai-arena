"""Detect the vendor CLIs (`claude`, `codex`) and report their sign-in state."""

from __future__ import annotations

from arena.providers.runtimes.detect import (
    AuthState,
    Probe,
    ProbeResult,
    RuntimeReport,
    RuntimeStatus,
    check_model,
    detect_all,
    detect_runtime,
    probe_environment,
    read_codex_models,
    run_probe,
)
from arena.providers.runtimes.specs import (
    CLAUDE,
    CODEX,
    SPECS,
    ProtocolFailure,
    RuntimeSpec,
    spec_for_vendor,
)

__all__ = [
    "CLAUDE",
    "CODEX",
    "SPECS",
    "AuthState",
    "Probe",
    "ProbeResult",
    "ProtocolFailure",
    "RuntimeReport",
    "RuntimeSpec",
    "RuntimeStatus",
    "check_model",
    "detect_all",
    "detect_runtime",
    "probe_environment",
    "read_codex_models",
    "run_probe",
    "spec_for_vendor",
]
