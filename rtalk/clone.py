"""GitHub URL detection and shallow clone with local caching."""

from __future__ import annotations

import hashlib
import os
import re
import shutil
import subprocess
from pathlib import Path

CLONE_BASE = os.path.join(".rtalk", "repos")

_HTTPS_RE = re.compile(
    r"^https?://github\.com/(?P<owner>[^/]+)/(?P<repo>[^/]+?)(?:\.git)?/?$"
)
_SSH_RE = re.compile(
    r"^git@github\.com:(?P<owner>[^/]+)/(?P<repo>[^/]+?)(?:\.git)?$"
)


def is_github_url(value: str) -> bool:
    """Return True if value looks like a GitHub HTTPS or SSH URL."""
    return bool(_HTTPS_RE.match(value) or _SSH_RE.match(value))


def parse_github_url(url: str) -> tuple[str, str, str]:
    """Extract (owner, repo_name, clone_url) from a GitHub URL.

    Normalises SSH URLs to HTTPS for cloning.
    Raises ValueError if the URL is not recognised.
    """
    m = _HTTPS_RE.match(url)
    if m:
        owner, repo = m.group("owner"), m.group("repo")
        clone_url = f"https://github.com/{owner}/{repo}.git"
        return owner, repo, clone_url

    m = _SSH_RE.match(url)
    if m:
        owner, repo = m.group("owner"), m.group("repo")
        clone_url = f"https://github.com/{owner}/{repo}.git"
        return owner, repo, clone_url

    raise ValueError(f"Not a recognised GitHub URL: {url}")


def _cache_dir(owner: str, repo: str, clone_url: str, base: str = CLONE_BASE) -> str:
    """Deterministic cache directory: <base>/<owner>__<repo>__<short_hash>/"""
    short = hashlib.sha256(clone_url.encode()).hexdigest()[:8]
    return os.path.join(base, f"{owner}__{repo}__{short}")


def resolve_repo(
    repo_input: str,
    refresh: bool = False,
    clone_base: str = CLONE_BASE,
) -> str:
    """Resolve a repo input (local path or GitHub URL) to a local directory.

    If repo_input is a local directory, return its absolute path unchanged.
    If repo_input is a GitHub URL, clone (or reuse cache) and return the local path.
    """
    if os.path.isdir(repo_input):
        return os.path.abspath(repo_input)

    if not is_github_url(repo_input):
        raise ValueError(
            f"'{repo_input}' is not a local directory and not a recognised GitHub URL. "
            f"Expected a directory path or https://github.com/owner/repo"
        )

    owner, repo, clone_url = parse_github_url(repo_input)
    dest = _cache_dir(owner, repo, clone_url, base=clone_base)

    if os.path.isdir(dest) and not refresh:
        return os.path.abspath(dest)

    if os.path.isdir(dest) and refresh:
        shutil.rmtree(dest)

    git = shutil.which("git")
    if not git:
        raise RuntimeError("git is not installed or not on PATH. Cannot clone.")

    Path(dest).parent.mkdir(parents=True, exist_ok=True)

    result = subprocess.run(
        [git, "clone", "--depth", "1", clone_url, dest],
        capture_output=True,
        text=True,
        timeout=120,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"git clone failed (exit {result.returncode}): {result.stderr.strip()}"
        )

    return os.path.abspath(dest)


def clone_preview_path(repo_input: str, clone_base: str = CLONE_BASE) -> str | None:
    """Return the cache path that would be used for a GitHub URL, without cloning.

    Returns None if repo_input is not a GitHub URL.
    """
    if not is_github_url(repo_input):
        return None
    owner, repo, clone_url = parse_github_url(repo_input)
    return _cache_dir(owner, repo, clone_url, base=clone_base)
