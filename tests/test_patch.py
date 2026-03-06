"""Tests for the patch generator."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from rtalk.patch import (
    add_todo_comment,
    generate_patch,
    insert_logging,
    verify_patch,
    _find_function_body_line,
)


@pytest.fixture
def patch_repo(tmp_path: Path) -> Path:
    (tmp_path / "calculator.py").write_text(
        "import math\n\n\n"
        "def add(a, b):\n"
        "    return a + b\n\n\n"
        "def divide(a, b):\n"
        "    if b == 0:\n"
        "        raise ValueError('Cannot divide by zero')\n"
        "    return a / b\n\n\n"
        "class Calculator:\n"
        "    def multiply(self, a, b):\n"
        "        return a * b\n"
    )
    return tmp_path


def test_find_function_body_line():
    source = "def foo():\n    return 1\n"
    assert _find_function_body_line(source, "foo") == 2


def test_find_function_not_found():
    source = "def foo():\n    pass\n"
    assert _find_function_body_line(source, "bar") is None


def test_find_function_syntax_error():
    assert _find_function_body_line("def broken(:\n", "broken") is None


def test_insert_logging(patch_repo: Path):
    diff = insert_logging(str(patch_repo), "calculator.py", "add")
    assert "--- a/calculator.py" in diff
    assert "+++ b/calculator.py" in diff
    assert "logging" in diff
    assert "Entering add" in diff


def test_insert_logging_class_method(patch_repo: Path):
    diff = insert_logging(str(patch_repo), "calculator.py", "multiply")
    assert "logging" in diff
    assert "Entering multiply" in diff


def test_insert_logging_unknown_function(patch_repo: Path):
    with pytest.raises(ValueError, match="not found"):
        insert_logging(str(patch_repo), "calculator.py", "nonexistent")


def test_add_todo_comment(patch_repo: Path):
    diff = add_todo_comment(str(patch_repo), "calculator.py", 5, "TODO: Add type hints")
    assert "--- a/calculator.py" in diff
    assert "+++ b/calculator.py" in diff
    assert "TODO: Add type hints" in diff


def test_add_todo_invalid_line(patch_repo: Path):
    with pytest.raises(ValueError, match="out of range"):
        add_todo_comment(str(patch_repo), "calculator.py", 999)


def test_generate_patch_logging(patch_repo: Path):
    diff = generate_patch(
        str(patch_repo), "calculator.py", "add logging to function divide"
    )
    assert "logging" in diff
    assert "Entering divide" in diff


def test_generate_patch_todo(patch_repo: Path):
    diff = generate_patch(
        str(patch_repo), "calculator.py", "add todo at line 3: Refactor this"
    )
    assert "Refactor this" in diff


def test_generate_patch_todo_default_message(patch_repo: Path):
    diff = generate_patch(str(patch_repo), "calculator.py", "add todo at line 1")
    assert "TODO: Review this section" in diff


def test_generate_patch_unsupported(patch_repo: Path):
    with pytest.raises(ValueError, match="Unsupported instruction"):
        generate_patch(str(patch_repo), "calculator.py", "rewrite everything")


def test_verify_patch_pure_python(patch_repo: Path):
    diff = insert_logging(str(patch_repo), "calculator.py", "add")
    ok, msg = verify_patch(diff, str(patch_repo))
    assert ok is True
    assert "valid" in msg.lower() or "cleanly" in msg.lower()


def test_verify_empty_patch(patch_repo: Path):
    ok, msg = verify_patch("", str(patch_repo))
    assert ok is False
    assert "Empty" in msg


def test_diff_contains_correct_format(patch_repo: Path):
    diff = insert_logging(str(patch_repo), "calculator.py", "add")
    lines = diff.split("\n")
    has_minus = any(l.startswith("---") for l in lines)
    has_plus = any(l.startswith("+++") for l in lines)
    has_hunk = any(l.startswith("@@") for l in lines)
    assert has_minus and has_plus and has_hunk
