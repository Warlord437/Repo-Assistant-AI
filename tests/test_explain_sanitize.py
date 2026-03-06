"""Tests for Explain tab sanitization and citation display."""

from __future__ import annotations

from pathlib import Path

import pytest

from rtalk.text_clean import normalize_lines, strip_html, strip_markdown_images
from rtalk.index import build_index
from rtalk.explain import summarize_repo


# ---------------------------------------------------------------------------
# text_clean module
# ---------------------------------------------------------------------------

class TestStripHtml:
    def test_removes_html_tags(self):
        out = strip_html("<p>Hello <b>world</b></p>")
        assert "<" not in out and ">" not in out
        assert "Hello" in out and "world" in out

    def test_keeps_img_alt_text(self):
        out = strip_html('<img src="x.png" alt="Diagram of flow">')
        assert "Diagram of flow" in out
        assert "<" not in out and ">" not in out

    def test_collapses_whitespace(self):
        out = strip_html("  foo   \n\n   bar  ")
        assert "  " not in out or out.count("  ") < 2

    def test_decodes_entities(self):
        out = strip_html("&amp; &lt; &gt;")
        assert "&" in out and "<" in out and ">" in out


class TestStripMarkdownImages:
    def test_removes_markdown_images(self):
        text = "Some text\n![alt](url.png)\nMore text"
        out = strip_markdown_images(text)
        assert "![alt]" not in out
        assert "Some text" in out and "More text" in out

    def test_removes_img_tags(self):
        text = "Before <img src='x'> after"
        out = strip_markdown_images(text)
        assert "<img" not in out
        assert "Before" in out and "after" in out


class TestNormalizeLines:
    def test_trims_lines(self):
        out = normalize_lines("  a  \n  b  ")
        assert out == "a\nb"

    def test_dedupes_blank_lines(self):
        out = normalize_lines("a\n\n\n\nb")
        assert "\n\n\n" not in out


# ---------------------------------------------------------------------------
# Explain report: What section sanitization
# ---------------------------------------------------------------------------

@pytest.fixture
def html_readme_repo(tmp_path: Path) -> str:
    """Repo with README containing HTML and markdown images."""
    (tmp_path / "README.md").write_text(
        "# My Project\n\n"
        "<p>This is <b>HTML</b> in the README.</p>\n\n"
        "![Screenshot](screenshot.png)\n\n"
        "<img src='logo.png' alt='Logo'>\n\n"
        "More plain text here."
    )
    (tmp_path / "foo.py").write_text("x = 1\n")
    idx = str(tmp_path / "index.jsonl")
    build_index(str(tmp_path), out_path=idx, chunk_size=200)
    return idx


class TestWhatSectionSanitization:
    def test_output_has_no_html_tags(self, html_readme_repo: str):
        report = summarize_repo(html_readme_repo)
        body = report.what.body
        assert "<" not in body and ">" not in body
        assert "HTML" in body or "plain text" in body or "My Project" in body

    def test_readable_content(self, html_readme_repo: str):
        report = summarize_repo(html_readme_repo)
        body = report.what.body
        assert len(body) > 10
        assert "My Project" in body or "README" in body


# ---------------------------------------------------------------------------
# How to run: deduped, max 8 lines
# ---------------------------------------------------------------------------

@pytest.fixture
def readme_with_commands(tmp_path: Path) -> str:
    """README with code fence commands in Quick Start."""
    (tmp_path / "README.md").write_text(
        "# Repo\n\n## Quick Start\n\n"
        "```bash\n"
        "pip install -e .\n"
        "pip install -e .\n"
        "python -m foo run\n"
        "python -m foo run\n"
        "uvicorn app:main\n"
        "make test\n"
        "```\n\n"
        "## Install\n\n"
        "```\npip install -e .\n```\n"
    )
    (tmp_path / "foo.py").write_text("x = 1\n")
    idx = str(tmp_path / "index.jsonl")
    build_index(str(tmp_path), out_path=idx, chunk_size=200)
    return idx


class TestHowToRun:
    def test_deduped_commands(self, readme_with_commands: str):
        report = summarize_repo(readme_with_commands)
        body = report.how_to_run.body
        lines = [l.strip() for l in body.split("\n") if l.strip().startswith("$")]
        pip_count = sum(1 for l in lines if "pip install" in l)
        assert pip_count <= 2

    def test_max_8_command_lines(self, readme_with_commands: str):
        report = summarize_repo(readme_with_commands)
        body = report.how_to_run.body
        lines = [l for l in body.split("\n") if l.strip().startswith("$")]
        assert len(lines) <= 8

    def test_prefers_pip_python_uvicorn(self, readme_with_commands: str):
        report = summarize_repo(readme_with_commands)
        body = report.how_to_run.body
        assert "pip" in body or "python" in body or "uvicorn" in body


# ---------------------------------------------------------------------------
# Citations
# ---------------------------------------------------------------------------

@pytest.fixture
def mini_index(tmp_path: Path) -> str:
    """Minimal repo for citation tests."""
    (tmp_path / "README.md").write_text("# Foo\n\nA tool.\n")
    (tmp_path / "bar.py").write_text("x = 1\n")
    idx = str(tmp_path / "index.jsonl")
    build_index(str(tmp_path), out_path=idx, chunk_size=80)
    return idx


class TestCitationsTop:
    def test_citations_top_length_at_most_5(self, mini_index: str):
        report = summarize_repo(mini_index)
        assert len(report.what.citations_top) <= 5
        assert len(report.how_to_run.citations_top) <= 5

    def test_to_dict_includes_citations_top(self, mini_index: str):
        report = summarize_repo(mini_index)
        d = report.to_dict()
        assert "citations_top" in d["what"]
        assert len(d["what"]["citations_top"]) <= 5
        assert "citations_all" in d["what"] or "citations" in d["what"]
