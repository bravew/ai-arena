"""Git skill source resolution behind a replaceable resolver interface."""

from __future__ import annotations

import re
import subprocess
from pathlib import Path
from typing import Protocol

from arena.core.models import SkillSource

_COMMIT = re.compile(r"^[0-9a-fA-F]{40,64}$")


class GitResolver(Protocol):
    def resolve(self, repository: str, ref: str) -> str:
        """Resolve a branch, tag, or commit-ish to its immutable commit hash."""
        ...


class SubprocessGitResolver:
    """Resolve refs and check out a pinned Git tree without a working directory."""

    def resolve(self, repository: str, ref: str) -> str:
        if not repository or not ref or ref.startswith("-"):
            raise ValueError("Git repository and ref must be non-empty; ref cannot start with '-'")
        tag_ref = f"refs/tags/{ref.removeprefix('refs/tags/')}"
        branch_ref = f"refs/heads/{ref.removeprefix('refs/heads/')}"
        result = subprocess.run(
            ["git", "ls-remote", repository, tag_ref, f"{tag_ref}^{{}}", branch_ref],
            check=False,
            capture_output=True,
            text=True,
            timeout=60,
        )
        if result.returncode:
            raise RuntimeError(
                f"cannot resolve Git ref {ref!r} in {repository!r}: {result.stderr.strip()}"
            )
        matches: dict[str, str] = {}
        for line in result.stdout.splitlines():
            sha, _separator, name = line.partition("\t")
            if _COMMIT.fullmatch(sha):
                matches[name.removesuffix("^{}") if name.endswith("^{}") else name] = sha.lower()
        resolved_name = next(
            (candidate for candidate in (tag_ref, branch_ref) if candidate in matches),
            None,
        )
        if resolved_name is None:
            if _COMMIT.fullmatch(ref):
                return ref.lower()
            raise ValueError(f"Git ref {ref!r} not found in {repository!r}")
        return matches.get(resolved_name, "")


def pin_skill_source(source: SkillSource, resolver: GitResolver) -> SkillSource:
    """Return a Git skill source pinned to its resolved commit."""
    if not source.git:
        return source
    if not source.ref:
        raise ValueError(f"Git skill source {source.git!r} is missing a ref")
    commit = (
        source.ref.lower()
        if _COMMIT.fullmatch(source.ref)
        else resolver.resolve(source.git, source.ref)
    )
    return source.model_copy(update={"ref": commit})


def local_git_resolver(repository: Path) -> GitResolver:
    """Build a resolver scoped to a local repository path (useful for offline tests)."""
    repository_path = repository

    class LocalResolver:
        def resolve(self, repository: str, ref: str) -> str:
            del repository
            result = subprocess.run(
                ["git", "--git-dir", str(repository_path), "rev-parse", f"{ref}^{{commit}}"],
                check=False,
                capture_output=True,
                text=True,
                timeout=10,
            )
            if result.returncode or not _COMMIT.fullmatch(result.stdout.strip()):
                raise ValueError(f"cannot resolve local Git ref {ref!r}: {result.stderr.strip()}")
            return result.stdout.strip().lower()

    return LocalResolver()
