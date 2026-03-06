"""Tests for the impact analysis module."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from rtalk.index import build_index
from rtalk.graph import build_graph
from rtalk.impact import analyze_impact, ImpactReport, _compute_risk


@pytest.fixture
def impact_repo(tmp_path: Path) -> Path:
    """Small repo for impact testing."""
    pkg = tmp_path / "mypkg"
    pkg.mkdir()

    (pkg / "__init__.py").write_text("__version__ = '1.0'\n")

    (pkg / "core.py").write_text(
        "from mypkg.utils import helper\n\n\n"
        "def process():\n"
        "    return helper()\n"
    )

    (pkg / "utils.py").write_text(
        "def helper():\n"
        "    return 42\n\n"
        "def other():\n"
        "    return 0\n"
    )

    (pkg / "api.py").write_text(
        "from mypkg.core import process\n\n"
        "def handle():\n"
        "    return process()\n\n\n"
        'if __name__ == "__main__":\n'
        "    handle()\n"
    )

    tests_dir = tmp_path / "tests"
    tests_dir.mkdir()
    (tests_dir / "__init__.py").write_text("")
    (tests_dir / "test_utils.py").write_text(
        "from mypkg.utils import helper\n\n\n"
        "def test_helper():\n"
        "    assert helper() == 42\n"
    )
    (tests_dir / "test_core.py").write_text(
        "from mypkg.core import process\n\n\n"
        "def test_process():\n"
        "    assert process() == 42\n"
    )

    (tmp_path / "README.md").write_text("# Impact Test\n")
    return tmp_path


@pytest.fixture
def impact_data(impact_repo: Path, tmp_path: Path) -> tuple[str, str]:
    idx = str(tmp_path / "impact_idx.jsonl")
    build_index(str(impact_repo), out_path=idx, chunk_size=40)
    graph_path = str(tmp_path / "impact_graph.json")
    build_graph(idx, out_path=graph_path)
    return idx, graph_path


class TestComputeRisk:
    def test_zero_dependents(self):
        score, breakdown = _compute_risk(0, 0, 3, 0, 50)
        assert score >= 0

    def test_high_dependents(self):
        score, _ = _compute_risk(5, 2, 0, 10, 200)
        assert score > 30

    def test_capped_at_100(self):
        score, _ = _compute_risk(10, 5, 0, 50, 1000)
        assert score <= 100

    def test_tests_reduce_risk(self):
        score_no_tests, _ = _compute_risk(3, 1, 0, 5, 100)
        score_with_tests, _ = _compute_risk(3, 1, 3, 5, 100)
        assert score_with_tests <= score_no_tests


class TestAnalyzeImpact:
    def test_returns_report(self, impact_data):
        idx, graph_path = impact_data
        report = analyze_impact("mypkg/utils.py", idx, graph_path)
        assert isinstance(report, ImpactReport)
        assert report.target_file == "mypkg/utils.py"

    def test_finds_dependents(self, impact_data):
        idx, graph_path = impact_data
        report = analyze_impact("mypkg/utils.py", idx, graph_path)
        assert len(report.dependents) > 0
        assert any("core" in d for d in report.dependents)

    def test_finds_related_tests(self, impact_data):
        idx, graph_path = impact_data
        report = analyze_impact("mypkg/utils.py", idx, graph_path)
        assert len(report.related_tests) > 0
        assert any("test_utils" in t for t in report.related_tests)

    def test_risk_score_range(self, impact_data):
        idx, graph_path = impact_data
        report = analyze_impact("mypkg/utils.py", idx, graph_path)
        assert 0 <= report.risk_score <= 100

    def test_risk_breakdown_keys(self, impact_data):
        idx, graph_path = impact_data
        report = analyze_impact("mypkg/utils.py", idx, graph_path)
        assert "dependents_impact" in report.risk_breakdown
        assert "test_coverage_gap" in report.risk_breakdown

    def test_render_text(self, impact_data):
        idx, graph_path = impact_data
        report = analyze_impact("mypkg/utils.py", idx, graph_path)
        text = report.render_text()
        assert "IMPACT ANALYSIS" in text
        assert "Risk Score" in text

    def test_to_dict(self, impact_data):
        idx, graph_path = impact_data
        report = analyze_impact("mypkg/utils.py", idx, graph_path)
        d = report.to_dict()
        assert "target_file" in d
        assert "dependents" in d
        assert "risk_score" in d

    def test_leaf_file_low_impact(self, impact_data):
        """A leaf file with no dependents should have lower risk."""
        idx, graph_path = impact_data
        report = analyze_impact("mypkg/api.py", idx, graph_path)
        assert report.risk_score <= 60
