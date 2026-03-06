"""Tests for the answering engine."""

from __future__ import annotations

import pytest

from rtalk.answer import answer_question, LocalModelStub, _extract_keywords
from rtalk.models import EvidenceSnippet, StructuredAnswer


def _make_snippet(
    file_path: str = "example.py",
    start: int = 1,
    end: int = 10,
    text: str = "def example():\n    pass",
    score: float = 0.8,
    method: str = "lexical",
) -> EvidenceSnippet:
    return EvidenceSnippet(
        file_path=file_path,
        start_line=start,
        end_line=end,
        text=text,
        score=score,
        method=method,
    )


def test_refuse_on_empty_evidence():
    result = answer_question("What does foo do?", [])
    assert result.refused is True
    assert result.summary == ""
    assert "No evidence" in result.refusal_reason


def test_refuse_on_irrelevant_evidence():
    ev = _make_snippet(text="completely unrelated content xyz abc", score=0.0)
    result = answer_question("How does the database migration work?", [ev])
    assert result.refused is True
    reason = result.refusal_reason.lower()
    assert "relevant" in reason or "insufficient" in reason


def test_answer_with_evidence():
    snippets = [
        _make_snippet(
            file_path="retrieval.py",
            start=10,
            end=30,
            text="def search(self, query):\n    results = self._tfidf.search(query)\n    return results",
            score=0.9,
        ),
        _make_snippet(
            file_path="models.py",
            start=5,
            end=15,
            text="class EvidenceSnippet:\n    file_path: str\n    score: float",
            score=0.5,
        ),
    ]
    result = answer_question("How does search work?", snippets)
    assert result.refused is False
    assert result.summary != ""
    assert len(result.evidence) == 2
    assert "retrieval.py" in result.evidence[0].citation()


def test_answer_includes_citations():
    snippets = [
        _make_snippet(
            file_path="foo.py",
            start=42,
            end=50,
            text="def foo():\n    return 42",
            score=0.7,
        ),
    ]
    result = answer_question("What does foo return?", snippets)
    assert not result.refused
    rendered = result.render_text()
    assert "foo.py:42-50" in rendered


def test_answer_render_refused():
    result = answer_question("Unknown question", [])
    text = result.render_text()
    assert "REFUSED" in text
    assert "Insufficient evidence" in text


def test_answer_render_with_evidence():
    snippets = [_make_snippet(text="def add(a, b):\n    return a + b", score=0.8)]
    result = answer_question("How does add work?", snippets)
    text = result.render_text()
    assert "Summary:" in text
    assert "Evidence:" in text


def test_extract_keywords():
    kws = _extract_keywords("How does the retrieval engine work?")
    assert "retrieval" in kws
    assert "engine" in kws
    assert "work" in kws
    assert "how" not in kws
    assert "the" not in kws


def test_local_model_stub():
    stub = LocalModelStub()
    assert stub.is_available() is False
    assert stub.generate("test") == "[LLM not configured]"


def test_answer_with_llm_stub():
    snippets = [_make_snippet(text="def hello(): pass", score=0.6)]
    stub = LocalModelStub()
    result = answer_question("What is hello?", snippets, llm=stub)
    assert not result.refused
    assert "LLM not configured" not in result.explanation
