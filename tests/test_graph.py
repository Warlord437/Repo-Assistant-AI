"""Tests for the graph module on a tiny fixture repo."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from rtalk.index import build_index
from rtalk.graph import (
    build_graph,
    build_internal_import_graph,
    build_call_graph,
    compute_centrality,
    compute_clusters,
    _file_to_module,
    _is_internal,
    _build_module_map,
    _load_records,
)


@pytest.fixture
def graph_repo(tmp_path: Path) -> Path:
    """A small repo with internal imports for graph testing."""
    pkg = tmp_path / "mypkg"
    pkg.mkdir()

    (pkg / "__init__.py").write_text("__version__ = '1.0'\n")

    (pkg / "core.py").write_text(
        "from mypkg.utils import helper\n"
        "from mypkg.models import Item\n\n\n"
        "def process(items):\n"
        "    for item in items:\n"
        "        helper(item)\n"
        "    return True\n\n\n"
        "def validate(item):\n"
        "    return item is not None\n"
    )

    (pkg / "utils.py").write_text(
        "import os\n\n\n"
        "def helper(item):\n"
        "    return str(item)\n\n\n"
        "def format_output(data):\n"
        "    return helper(data)\n"
    )

    (pkg / "models.py").write_text(
        "from dataclasses import dataclass\n\n\n"
        "@dataclass\n"
        "class Item:\n"
        "    name: str\n"
        "    value: int = 0\n"
    )

    (pkg / "api.py").write_text(
        "from mypkg.core import process\n"
        "from mypkg.models import Item\n\n\n"
        "def handle_request(data):\n"
        "    items = [Item(name=d) for d in data]\n"
        "    return process(items)\n"
    )

    tests_dir = tmp_path / "tests"
    tests_dir.mkdir()
    (tests_dir / "__init__.py").write_text("")
    (tests_dir / "test_core.py").write_text(
        "from mypkg.core import process, validate\n\n\n"
        "def test_process():\n"
        "    assert process([]) is True\n\n\n"
        "def test_validate():\n"
        "    assert validate('x') is True\n"
    )

    (tmp_path / "README.md").write_text("# Test Repo\n")

    return tmp_path


@pytest.fixture
def graph_index(graph_repo: Path, tmp_path: Path) -> str:
    out = str(tmp_path / "graph_idx.jsonl")
    build_index(str(graph_repo), out_path=out, chunk_size=40)
    return out


# ---------------------------------------------------------------------------
# Unit tests
# ---------------------------------------------------------------------------

class TestFileToModule:
    def test_simple(self):
        assert _file_to_module("mypkg/core.py") == "mypkg.core"

    def test_init(self):
        assert _file_to_module("mypkg/__init__.py") == "mypkg"

    def test_nested(self):
        assert _file_to_module("a/b/c.py") == "a.b.c"


class TestIsInternal:
    def test_stdlib(self):
        mm = {"mypkg": "mypkg/__init__.py", "mypkg.core": "mypkg/core.py"}
        assert _is_internal("os", mm) is False
        assert _is_internal("sys", mm) is False

    def test_third_party(self):
        mm = {"mypkg": "mypkg/__init__.py"}
        assert _is_internal("fastapi", mm) is False
        assert _is_internal("pytest", mm) is False

    def test_internal(self):
        mm = {"mypkg": "mypkg/__init__.py", "mypkg.core": "mypkg/core.py"}
        assert _is_internal("mypkg.core", mm) is True
        assert _is_internal("mypkg", mm) is True


# ---------------------------------------------------------------------------
# Integration tests
# ---------------------------------------------------------------------------

class TestBuildInternalImportGraph:
    def test_has_edges(self, graph_index: str):
        records = _load_records(graph_index)
        edges, fwd, rev = build_internal_import_graph(records)
        assert len(edges) > 0

    def test_core_imports_utils(self, graph_index: str):
        records = _load_records(graph_index)
        edges, _, _ = build_internal_import_graph(records)
        pairs = {(e["source"], e["target"]) for e in edges}
        assert any(
            "core" in s and "utils" in t for s, t in pairs
        ), f"Expected core->utils edge, got: {pairs}"

    def test_no_stdlib_edges(self, graph_index: str):
        records = _load_records(graph_index)
        edges, _, _ = build_internal_import_graph(records)
        for e in edges:
            assert "os.py" not in e["target"]
            assert e["target"] != "os"

    def test_reverse_deps(self, graph_index: str):
        records = _load_records(graph_index)
        _, _, rev = build_internal_import_graph(records)
        assert len(rev) > 0
        all_targets = set()
        for deps in rev.values():
            all_targets.update(deps)
        assert len(all_targets) > 0


class TestBuildCallGraph:
    def test_has_call_edges(self, graph_index: str):
        records = _load_records(graph_index)
        call_edges = build_call_graph(records)
        assert len(call_edges) > 0

    def test_format_calls_helper(self, graph_index: str):
        records = _load_records(graph_index)
        call_edges = build_call_graph(records)
        has_helper_call = any(
            "format_output" in e["source"] and "helper" in e["target"]
            for e in call_edges
        )
        assert has_helper_call, f"Expected format_output->helper, got: {call_edges}"


class TestCentrality:
    def test_returns_ranked(self, graph_index: str):
        records = _load_records(graph_index)
        edges, _, _ = build_internal_import_graph(records)
        all_py = {r.file_path for r in records if r.file_path.endswith(".py")}
        result = compute_centrality(edges, all_py)
        assert len(result) > 0

    def test_scores_are_positive(self, graph_index: str):
        records = _load_records(graph_index)
        edges, _, _ = build_internal_import_graph(records)
        all_py = {r.file_path for r in records if r.file_path.endswith(".py")}
        result = compute_centrality(edges, all_py)
        for _, score in result:
            assert score > 0

    def test_empty_graph(self):
        assert compute_centrality([], set()) == []


class TestClusters:
    def test_groups_by_folder(self, graph_index: str):
        records = _load_records(graph_index)
        all_files = {r.file_path for r in records}
        clusters = compute_clusters(all_files)
        assert "mypkg" in clusters
        assert len(clusters["mypkg"]) > 0


class TestBuildGraph:
    def test_produces_json(self, graph_index: str, tmp_path: Path):
        out = str(tmp_path / "graph.json")
        result = build_graph(graph_index, out_path=out)
        assert os.path.isfile(out)
        assert "nodes" in result
        assert "import_edges" in result
        assert "call_edges" in result
        assert "centrality" in result
        assert "clusters" in result
        assert "stats" in result

    def test_nodes_have_required_fields(self, graph_index: str, tmp_path: Path):
        out = str(tmp_path / "graph.json")
        result = build_graph(graph_index, out_path=out)
        for node in result["nodes"]:
            assert "id" in node
            assert "label" in node
            assert "folder" in node
            assert "symbols" in node
            assert "dependencies" in node
            assert "dependents" in node

    def test_stats(self, graph_index: str, tmp_path: Path):
        out = str(tmp_path / "graph.json")
        result = build_graph(graph_index, out_path=out)
        assert result["stats"]["total_files"] > 0
        assert result["stats"]["python_files"] > 0
