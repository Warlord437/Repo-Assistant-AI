"""Tests for the retrieval engine."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from rtalk.index import build_index
from rtalk.models import Chunk, EvidenceSnippet, IndexRecord, RecordKind, SymbolKind, SymbolRecord
from rtalk.retrieval import (
    RetrievalEngine,
    TFIDFIndex,
    _python_exact_search,
    detect_explain_intent,
    _filename_boost_score,
    _symbol_boost_score,
    _deduplicate,
)


@pytest.fixture
def sample_repo(tmp_path: Path) -> Path:
    (tmp_path / "math_utils.py").write_text(
        "def add(a, b):\n    return a + b\n\n"
        "def multiply(a, b):\n    return a * b\n\n"
        "def fibonacci(n):\n    if n <= 1:\n        return n\n"
        "    return fibonacci(n-1) + fibonacci(n-2)\n"
    )
    (tmp_path / "README.md").write_text(
        "# Math Utils\n\nA collection of math utility functions.\n"
        "Includes add, multiply, and fibonacci.\n"
    )
    return tmp_path


@pytest.fixture
def indexed(sample_repo: Path, tmp_path: Path) -> tuple[str, str]:
    out = str(tmp_path / "idx.jsonl")
    build_index(str(sample_repo), out_path=out, chunk_size=40)
    return str(sample_repo), out


def test_tfidf_basic():
    idx = TFIDFIndex()
    rec1 = IndexRecord(
        record_kind=RecordKind.FILE_CHUNK,
        file_path="a.py",
        sha256="aaa",
        total_lines=5,
        chunk=Chunk(start_line=1, end_line=5, text="def hello world greeting"),
    )
    rec2 = IndexRecord(
        record_kind=RecordKind.FILE_CHUNK,
        file_path="b.py",
        sha256="bbb",
        total_lines=3,
        chunk=Chunk(start_line=1, end_line=3, text="database connection pool"),
    )
    idx.add(rec1, "def hello world greeting")
    idx.add(rec2, "database connection pool")

    results = idx.search("hello greeting")
    assert len(results) > 0
    assert results[0][0].file_path == "a.py"


def test_tfidf_empty_query():
    idx = TFIDFIndex()
    rec = IndexRecord(
        record_kind=RecordKind.FILE_CHUNK,
        file_path="a.py",
        sha256="aaa",
        total_lines=1,
        chunk=Chunk(start_line=1, end_line=1, text="some text"),
    )
    idx.add(rec, "some text")
    assert idx.search("") == []


def test_python_exact_search():
    records = [
        IndexRecord(
            record_kind=RecordKind.FILE_CHUNK,
            file_path="utils.py",
            sha256="x",
            total_lines=10,
            chunk=Chunk(start_line=1, end_line=10, text="def fibonacci(n):\n    pass"),
        ),
        IndexRecord(
            record_kind=RecordKind.FILE_CHUNK,
            file_path="other.py",
            sha256="y",
            total_lines=5,
            chunk=Chunk(start_line=1, end_line=5, text="no match here"),
        ),
    ]
    results = _python_exact_search("fibonacci", records)
    assert len(results) == 1
    assert results[0].file_path == "utils.py"
    assert results[0].method == "exact"


def test_python_exact_search_case_insensitive():
    records = [
        IndexRecord(
            record_kind=RecordKind.FILE_CHUNK,
            file_path="a.py",
            sha256="x",
            total_lines=1,
            chunk=Chunk(start_line=1, end_line=1, text="class MyClass:"),
        ),
    ]
    results = _python_exact_search("myclass", records)
    assert len(results) == 1


def test_engine_from_index(indexed):
    repo_path, index_path = indexed
    engine = RetrievalEngine.from_index(index_path)
    assert len(engine.records) > 0


def test_engine_search_lexical(indexed):
    _, index_path = indexed
    engine = RetrievalEngine.from_index(index_path)
    results = engine.search_lexical("fibonacci recursive")
    assert len(results) > 0
    assert all(isinstance(r, EvidenceSnippet) for r in results)
    assert all(r.method == "lexical" for r in results)


def test_engine_search_exact(indexed):
    _, index_path = indexed
    engine = RetrievalEngine.from_index(index_path)
    results = engine.search_exact("fibonacci")
    assert len(results) > 0
    assert all(r.method == "exact" for r in results)


def test_engine_combined_search(indexed):
    _, index_path = indexed
    engine = RetrievalEngine.from_index(index_path)
    results = engine.search("fibonacci", top_k=3)
    assert len(results) > 0
    for r in results:
        assert r.file_path
        assert r.start_line >= 1
        assert r.text


def test_engine_no_results(indexed):
    _, index_path = indexed
    engine = RetrievalEngine.from_index(index_path)
    results = engine.search("zzzzxyznonexistent")
    assert len(results) == 0


def test_evidence_snippet_citation():
    ev = EvidenceSnippet(
        file_path="src/main.py",
        start_line=10,
        end_line=25,
        text="some code",
        score=0.9,
        method="lexical",
    )
    assert ev.citation() == "src/main.py:10-25"


# ---------------------------------------------------------------------------
# Intent detection
# ---------------------------------------------------------------------------

class TestDetectExplainIntent:
    def test_explain_repo(self):
        assert detect_explain_intent("explain this repo") is True

    def test_overview(self):
        assert detect_explain_intent("give me an overview") is True

    def test_architecture(self):
        assert detect_explain_intent("what is the architecture?") is True

    def test_how_to_run(self):
        assert detect_explain_intent("how to run this project") is True

    def test_entrypoint(self):
        assert detect_explain_intent("where is the entrypoint?") is True

    def test_folders(self):
        assert detect_explain_intent("what are the main folders?") is True

    def test_normal_query_no_intent(self):
        assert detect_explain_intent("how does fibonacci work?") is False

    def test_specific_function_no_intent(self):
        assert detect_explain_intent("what does the add function return?") is False


# ---------------------------------------------------------------------------
# Filename and symbol boosting
# ---------------------------------------------------------------------------

class TestFilenameBoosting:
    def test_boost_for_matching_name(self):
        score = _filename_boost_score("retrieval.py", ["retrieval"])
        assert score > 0

    def test_boost_for_hint(self):
        score = _filename_boost_score("retrieval.py", ["search"])
        assert score > 0

    def test_no_boost_for_unrelated(self):
        score = _filename_boost_score("utils.py", ["database"])
        assert score == 0.0


class TestSymbolBoosting:
    def test_boost_when_name_matches(self):
        rec = IndexRecord(
            record_kind=RecordKind.SYMBOL,
            file_path="a.py",
            sha256="x",
            total_lines=10,
            symbol=SymbolRecord(
                name="fibonacci",
                kind=SymbolKind.FUNCTION,
                start_line=1,
                end_line=5,
                file_path="a.py",
            ),
        )
        score = _symbol_boost_score(rec, ["fibonacci"])
        assert score > 0

    def test_no_boost_no_symbol(self):
        rec = IndexRecord(
            record_kind=RecordKind.FILE_CHUNK,
            file_path="a.py",
            sha256="x",
            total_lines=10,
            chunk=Chunk(start_line=1, end_line=5, text="hello"),
        )
        assert _symbol_boost_score(rec, ["fibonacci"]) == 0.0


# ---------------------------------------------------------------------------
# Deduplication
# ---------------------------------------------------------------------------

class TestDeduplication:
    def test_removes_overlapping_same_file(self):
        sn1 = EvidenceSnippet("a.py", 1, 20, "text1", 0.9, "lexical")
        sn2 = EvidenceSnippet("a.py", 10, 30, "text2", 0.85, "lexical")
        result = _deduplicate([sn1, sn2])
        assert len(result) == 1

    def test_keeps_different_files(self):
        sn1 = EvidenceSnippet("a.py", 1, 20, "text1", 0.9, "lexical")
        sn2 = EvidenceSnippet("b.py", 1, 20, "text2", 0.85, "lexical")
        result = _deduplicate([sn1, sn2])
        assert len(result) == 2

    def test_keeps_significant_score_diff(self):
        sn1 = EvidenceSnippet("a.py", 1, 20, "text1", 0.9, "lexical")
        sn2 = EvidenceSnippet("a.py", 10, 30, "text2", 0.2, "lexical")
        result = _deduplicate([sn1, sn2], score_threshold=0.3)
        assert len(result) == 2
