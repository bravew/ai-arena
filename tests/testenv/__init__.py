"""Test isolation: every test runs under a temporary HOME and may not touch the real one.

`plugin.py` installs this for every test (pyproject `addopts`). See docs/code-standards.md.
"""

from tests.testenv.guard import RealHomeAccess, RealHomeGuard

__all__ = ["RealHomeAccess", "RealHomeGuard"]
