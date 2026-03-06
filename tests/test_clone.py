"""Tests for the clone module (URL detection, path computation, resolve logic)."""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

from rtalk.clone import (
    is_github_url,
    parse_github_url,
    clone_preview_path,
    resolve_repo,
    _cache_dir,
)


# ---------------------------------------------------------------------------
# URL detection
# ---------------------------------------------------------------------------

class TestIsGitHubUrl:
    def test_https_basic(self):
        assert is_github_url("https://github.com/owner/repo") is True

    def test_https_with_git_suffix(self):
        assert is_github_url("https://github.com/owner/repo.git") is True

    def test_https_with_trailing_slash(self):
        assert is_github_url("https://github.com/owner/repo/") is True

    def test_ssh(self):
        assert is_github_url("git@github.com:owner/repo.git") is True

    def test_ssh_without_git(self):
        assert is_github_url("git@github.com:owner/repo") is True

    def test_local_path_rejected(self):
        assert is_github_url("/some/local/path") is False

    def test_random_url_rejected(self):
        assert is_github_url("https://example.com/foo/bar") is False

    def test_empty_string(self):
        assert is_github_url("") is False

    def test_gitlab_rejected(self):
        assert is_github_url("https://gitlab.com/owner/repo") is False


# ---------------------------------------------------------------------------
# URL parsing
# ---------------------------------------------------------------------------

class TestParseGitHubUrl:
    def test_https(self):
        owner, repo, url = parse_github_url("https://github.com/fastapi/fastapi")
        assert owner == "fastapi"
        assert repo == "fastapi"
        assert url == "https://github.com/fastapi/fastapi.git"

    def test_https_with_git_suffix(self):
        owner, repo, url = parse_github_url("https://github.com/owner/my-repo.git")
        assert owner == "owner"
        assert repo == "my-repo"

    def test_ssh(self):
        owner, repo, url = parse_github_url("git@github.com:owner/repo.git")
        assert owner == "owner"
        assert repo == "repo"
        assert "https://github.com" in url

    def test_invalid_url_raises(self):
        with pytest.raises(ValueError, match="Not a recognised"):
            parse_github_url("https://example.com/foo")


# ---------------------------------------------------------------------------
# Cache directory logic
# ---------------------------------------------------------------------------

class TestCacheDir:
    def test_deterministic(self):
        d1 = _cache_dir("owner", "repo", "https://github.com/owner/repo.git")
        d2 = _cache_dir("owner", "repo", "https://github.com/owner/repo.git")
        assert d1 == d2

    def test_different_urls_different_dirs(self):
        d1 = _cache_dir("a", "b", "https://github.com/a/b.git")
        d2 = _cache_dir("a", "c", "https://github.com/a/c.git")
        assert d1 != d2

    def test_contains_owner_and_repo(self):
        d = _cache_dir("myorg", "myrepo", "https://github.com/myorg/myrepo.git")
        assert "myorg__myrepo" in d


class TestClonePreviewPath:
    def test_github_url(self):
        result = clone_preview_path("https://github.com/owner/repo")
        assert result is not None
        assert "owner__repo" in result

    def test_local_path(self):
        assert clone_preview_path("/some/path") is None


# ---------------------------------------------------------------------------
# resolve_repo (with mocked git)
# ---------------------------------------------------------------------------

class TestResolveRepo:
    def test_local_directory(self, tmp_path: Path):
        result = resolve_repo(str(tmp_path))
        assert result == str(tmp_path)

    def test_nonexistent_local_raises(self):
        with pytest.raises(ValueError, match="not a local directory"):
            resolve_repo("/definitely/not/a/real/path/zzzz")

    @patch("rtalk.clone.shutil.which", return_value="/usr/bin/git")
    @patch("rtalk.clone.subprocess.run")
    def test_github_url_clones(self, mock_run: MagicMock, mock_which: MagicMock, tmp_path: Path):
        clone_base = str(tmp_path / "repos")
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")

        os.makedirs(
            _cache_dir("owner", "repo", "https://github.com/owner/repo.git", base=clone_base),
            exist_ok=True,
        )

        result = resolve_repo(
            "https://github.com/owner/repo",
            clone_base=clone_base,
        )
        assert "owner__repo" in result

    @patch("rtalk.clone.shutil.which", return_value=None)
    def test_no_git_raises(self, mock_which: MagicMock):
        with pytest.raises(RuntimeError, match="git is not installed"):
            resolve_repo("https://github.com/owner/repo", clone_base="/tmp/test_repos")

    def test_reuse_cache(self, tmp_path: Path):
        """If clone dir already exists, return it without re-cloning."""
        cache_dir = _cache_dir("o", "r", "https://github.com/o/r.git", base=str(tmp_path))
        os.makedirs(cache_dir, exist_ok=True)
        result = resolve_repo("https://github.com/o/r", clone_base=str(tmp_path))
        assert os.path.isdir(result)
