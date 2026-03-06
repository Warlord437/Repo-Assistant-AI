"""Tests for the guided investigation (query-driven RAG)."""

from __future__ import annotations

from pathlib import Path

import pytest

from rtalk.guide import run_guide, GuideReport
from rtalk.index import build_index


@pytest.fixture
def guide_repo(tmp_path: Path) -> Path:
    """Small repo for guide testing."""
    pkg = tmp_path / "rtalk"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("__version__ = '0.1.0'\n")

    (pkg / "index.py").write_text(
        "import os\n"
        "from rtalk.models import IndexRecord, Chunk\n\n"
        "ALLOWED_EXTENSIONS = {'.py', '.md'}\n\n"
        "def build_index(repo_path, out_path='.rtalk/index.jsonl'):\n    pass\n"
    )

    (pkg / "retrieval.py").write_text(
        "class RetrievalEngine:\n"
        "    def search(self, query, top_k=5):\n"
        "        return []\n"
    )

    (tmp_path / "README.md").write_text("# rtalk\nTest project.\n")

    return tmp_path


@pytest.fixture
def guide_index(guide_repo: Path, tmp_path: Path) -> str:
    out = str(tmp_path / "guide_idx.jsonl")
    build_index(str(guide_repo), out_path=out, chunk_size=40)
    return out


class TestRunGuide:
    def test_returns_report(self, guide_index: str):
        report = run_guide("How does indexing work?", guide_index)
        assert isinstance(report, GuideReport)
        assert report.query == "How does indexing work?"

    def test_no_evidence_fallback(self, guide_index: str):
        report = run_guide("xyznonexistent123", guide_index)
        assert report.query == "xyznonexistent123"
        assert "No relevant" in report.summary or len(report.steps) <= 1

    def test_with_evidence(self, guide_index: str):
        report = run_guide("build_index index", guide_index)
        assert report.query == "build_index index"
        assert isinstance(report.steps, list)

    def test_render_text(self, guide_index: str):
        report = run_guide("index", guide_index)
        text = report.render_text()
        assert "GUIDE" in text
        assert report.query in text

    def test_to_dict(self, guide_index: str):
        report = run_guide("retrieval search", guide_index)
        d = report.to_dict()
        assert "query" in d
        assert "steps" in d
        assert "summary" in d
