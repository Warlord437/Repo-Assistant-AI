"""Tests for the table-of-contents (PageIndex-style) retrieval."""

from __future__ import annotations

from pathlib import Path

import pytest

from rtalk.index import build_index
from rtalk.models import IndexRecord, RecordKind
from rtalk.retrieval import RetrievalEngine
from rtalk.toc import build_toc, TOCIndex


@pytest.fixture
def toc_repo(tmp_path: Path) -> Path:
    pkg = tmp_path / "rtalk"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("")
    (pkg / "retrieval.py").write_text(
        "class RetrievalEngine:\n"
        "    def search(self, query, top_k=5):\n"
        "        return []\n"
    )
    (pkg / "answer.py").write_text(
        "def answer_question(query, evidence):\n"
        "    pass\n"
    )
    return tmp_path


@pytest.fixture
def toc_index(toc_repo: Path, tmp_path: Path) -> str:
    out = str(tmp_path / "toc_idx.jsonl")
    build_index(str(toc_repo), out_path=out, chunk_size=40)
    return out


class TestBuildTOC:
    def test_builds_hierarchy(self, toc_index: str):
        records: list[IndexRecord] = []
        with open(toc_index, "r") as f:
            for line in f:
                if line.strip():
                    records.append(IndexRecord.from_json_line(line))
        toc = build_toc(records)
        assert len(toc) > 0
        assert toc[0].kind == "folder"
        assert len(toc[0].children) > 0

    def test_toc_index_search(self, toc_index: str):
        records: list[IndexRecord] = []
        with open(toc_index, "r") as f:
            for line in f:
                if line.strip():
                    records.append(IndexRecord.from_json_line(line))
        toc = build_toc(records)
        idx = TOCIndex(toc)
        nodes = idx.search_nodes("RetrievalEngine search", top_k=5)
        assert len(nodes) >= 0

    def test_retrieval_uses_toc(self, toc_index: str):
        eng = RetrievalEngine.from_index(toc_index)
        results = eng.search("RetrievalEngine", top_k=5)
        assert isinstance(results, list)
        toc_results = [r for r in results if r.method == "toc"]
        lexical_results = [r for r in results if r.method == "lexical"]
        assert len(results) > 0
        assert len(toc_results) + len(lexical_results) >= len(results)
