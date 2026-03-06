"""Tests for the explain module on a small fixture repository."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from rtalk.index import build_index
from rtalk.explain import (
    summarize_repo,
    detect_entrypoints,
    build_import_graph,
    _load_records,
)
from rtalk.models import ExplainReport


@pytest.fixture
def mini_repo(tmp_path: Path) -> Path:
    """Create a small Python project for explain tests."""
    pkg = tmp_path / "mypkg"
    pkg.mkdir()

    (tmp_path / "README.md").write_text(
        "# My Cool Tool\n\n"
        "A CLI tool for processing data files.\n\n"
        "## Quickstart\n\n"
        "```\npip install -e .\npython -m mypkg run\n```\n"
    )

    (tmp_path / "pyproject.toml").write_text(
        "[project]\nname = \"my-cool-tool\"\nversion = \"1.0\"\n\n"
        "[project.scripts]\nmytool = \"mypkg.cli:main\"\n"
    )

    (pkg / "__init__.py").write_text("__version__ = '1.0'\n")

    (pkg / "cli.py").write_text(
        "import sys\n"
        "from mypkg.core import process\n\n\n"
        "def main():\n"
        "    process(sys.argv[1:])\n\n\n"
        'if __name__ == "__main__":\n'
        "    main()\n"
    )

    (pkg / "core.py").write_text(
        "from mypkg.utils import validate\n\n\n"
        "def process(args):\n"
        "    for a in args:\n"
        "        validate(a)\n"
        "    return True\n"
    )

    (pkg / "utils.py").write_text(
        "import os\n\n\n"
        "def validate(item):\n"
        "    return os.path.exists(item)\n"
    )

    (pkg / "server.py").write_text(
        "from fastapi import FastAPI\n\n"
        "app = FastAPI()\n\n\n"
        "@app.get('/')\n"
        "def root():\n"
        "    return {'status': 'ok'}\n"
    )

    tests_dir = tmp_path / "tests"
    tests_dir.mkdir()
    (tests_dir / "test_core.py").write_text(
        "from mypkg.core import process\n\n\n"
        "def test_process():\n"
        "    assert process([]) is True\n"
    )

    return tmp_path


@pytest.fixture
def mini_index(mini_repo: Path, tmp_path: Path) -> str:
    out = str(tmp_path / "index.jsonl")
    build_index(str(mini_repo), out_path=out, chunk_size=40)
    return out


# ---------------------------------------------------------------------------
# Full report tests
# ---------------------------------------------------------------------------

class TestSummarizeRepo:
    def test_returns_explain_report(self, mini_index: str):
        report = summarize_repo(mini_index)
        assert isinstance(report, ExplainReport)

    def test_what_section_from_readme(self, mini_index: str):
        report = summarize_repo(mini_index)
        assert "My Cool Tool" in report.what.body
        assert len(report.what.citations) > 0
        assert "README" in report.what.citations[0]

    def test_how_to_run_extracts_commands(self, mini_index: str):
        report = summarize_repo(mini_index)
        body = report.how_to_run.body
        assert "pip install" in body or "python" in body or "mytool" in body.lower() or "Command" in body

    def test_directories_present(self, mini_index: str):
        report = summarize_repo(mini_index)
        assert len(report.directories) > 0
        dir_names = [d.path for d in report.directories]
        assert "mypkg" in dir_names

    def test_entrypoints_found(self, mini_index: str):
        report = summarize_repo(mini_index)
        assert len(report.entrypoints) > 0
        kinds = {e.kind for e in report.entrypoints}
        assert "main_block" in kinds or "fastapi_app" in kinds

    def test_architecture_has_central_modules(self, mini_index: str):
        report = summarize_repo(mini_index)
        modules = [m for m, _ in report.architecture.central_modules]
        assert len(modules) > 0

    def test_start_here_not_empty(self, mini_index: str):
        report = summarize_repo(mini_index)
        assert len(report.start_here) > 0
        has_readme = any("README" in s.title for s in report.start_here)
        assert has_readme

    def test_render_text(self, mini_index: str):
        report = summarize_repo(mini_index)
        text = report.render_text()
        assert "REPO OVERVIEW" in text
        assert "Entry Points" in text or "entry" in text.lower()

    def test_to_dict_structure(self, mini_index: str):
        report = summarize_repo(mini_index)
        d = report.to_dict()
        assert "what" in d
        assert "how_to_run" in d
        assert "directories" in d
        assert "entrypoints" in d
        assert "architecture" in d
        assert "start_here" in d

    def test_all_entrypoint_citations_valid(self, mini_index: str):
        report = summarize_repo(mini_index)
        for ep in report.entrypoints:
            cit = ep.citation()
            assert ":" in cit
            parts = cit.split(":")
            assert len(parts) == 2


# ---------------------------------------------------------------------------
# Entrypoint detection
# ---------------------------------------------------------------------------

class TestDetectEntrypoints:
    def test_finds_main_block(self, mini_index: str):
        records = _load_records(mini_index)
        eps = detect_entrypoints(records)
        main_eps = [e for e in eps if e.kind == "main_block"]
        assert len(main_eps) >= 1
        assert any("cli" in e.file_path for e in main_eps)

    def test_finds_fastapi_app(self, mini_index: str):
        records = _load_records(mini_index)
        eps = detect_entrypoints(records)
        api_eps = [e for e in eps if e.kind == "fastapi_app"]
        assert len(api_eps) >= 1
        assert any("server" in e.file_path for e in api_eps)


# ---------------------------------------------------------------------------
# Import graph
# ---------------------------------------------------------------------------

class TestBuildImportGraph:
    def test_has_edges(self, mini_index: str):
        records = _load_records(mini_index)
        graph = build_import_graph(records)
        assert len(graph.edges) > 0

    def test_has_central_modules(self, mini_index: str):
        records = _load_records(mini_index)
        graph = build_import_graph(records)
        assert len(graph.central_modules) > 0

    def test_graph_dict_format(self, mini_index: str):
        records = _load_records(mini_index)
        graph = build_import_graph(records)
        d = graph.to_dict()
        assert "edges" in d
        assert "central_modules" in d
        for edge in d["edges"]:
            assert "source" in edge
            assert "target" in edge
