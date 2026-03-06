"""Tests for the indexer module."""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

import pytest

from rtalk.index import build_index, index_file, _chunk_lines, _extract_python_symbols


@pytest.fixture
def sample_repo(tmp_path: Path) -> Path:
    """Create a small fake repository for testing."""
    (tmp_path / "hello.py").write_text(
        'def greet(name: str) -> str:\n    return f"Hello, {name}!"\n\n\n'
        "class Greeter:\n    def __init__(self):\n        self.count = 0\n"
        "    def say_hi(self):\n        self.count += 1\n"
    )
    (tmp_path / "README.md").write_text("# Sample\n\nThis is a test repo.\n")
    (tmp_path / "config.toml").write_text('[project]\nname = "test"\n')
    (tmp_path / "binary.bin").write_bytes(b"\x00\x01\x02")
    return tmp_path


def test_chunk_lines_basic():
    lines = [f"line {i}" for i in range(200)]
    chunks = _chunk_lines(lines, chunk_size=80)
    assert len(chunks) == 3
    assert chunks[0].start_line == 1
    assert chunks[0].end_line == 80
    assert chunks[1].start_line == 81
    assert chunks[1].end_line == 160
    assert chunks[2].start_line == 161
    assert chunks[2].end_line == 200


def test_chunk_lines_small_file():
    lines = ["a", "b", "c"]
    chunks = _chunk_lines(lines, chunk_size=80)
    assert len(chunks) == 1
    assert chunks[0].start_line == 1
    assert chunks[0].end_line == 3


def test_extract_python_symbols():
    source = (
        "import os\n"
        "from pathlib import Path\n\n"
        "def foo(x):\n    return x + 1\n\n"
        "class Bar:\n    def baz(self):\n        pass\n"
    )
    symbols = _extract_python_symbols(source, "test.py")
    names = {s.name for s in symbols}
    assert "foo" in names
    assert "Bar" in names
    assert "baz" in names
    assert "os" in names
    assert "pathlib.Path" in names


def test_extract_symbols_invalid_syntax():
    symbols = _extract_python_symbols("def broken(:\n", "bad.py")
    assert symbols == []


def test_index_file(sample_repo: Path):
    records = index_file(str(sample_repo), "hello.py", chunk_size=80)
    assert len(records) > 0
    kinds = {r.record_kind.value for r in records}
    assert "file_chunk" in kinds
    assert "symbol" in kinds

    func_names = {r.symbol.name for r in records if r.symbol}
    assert "greet" in func_names
    assert "Greeter" in func_names


def test_build_index_skips_binary(sample_repo: Path, tmp_path: Path):
    """The build_index pipeline filters by extension, so .bin files are excluded."""
    out = str(tmp_path / "skip_test.jsonl")
    build_index(str(sample_repo), out_path=out)
    with open(out) as f:
        for line in f:
            data = json.loads(line)
            assert not data["file_path"].endswith(".bin")


def test_build_index(sample_repo: Path, tmp_path: Path):
    out = str(tmp_path / "test_index.jsonl")
    count = build_index(str(sample_repo), out_path=out)
    assert count > 0
    assert os.path.isfile(out)

    with open(out) as f:
        lines = f.readlines()
    assert len(lines) == count

    for line in lines:
        data = json.loads(line)
        assert "record_kind" in data
        assert "file_path" in data
        assert "sha256" in data


def test_index_roundtrip(sample_repo: Path, tmp_path: Path):
    """Records serialize and deserialize correctly."""
    from rtalk.models import IndexRecord

    records = index_file(str(sample_repo), "hello.py", chunk_size=80)
    for rec in records:
        line = rec.to_json_line()
        restored = IndexRecord.from_json_line(line)
        assert restored.record_kind == rec.record_kind
        assert restored.file_path == rec.file_path
        assert restored.sha256 == rec.sha256
