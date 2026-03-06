"""CLI Indexer -- scans a repository and builds a JSONL index with chunks and symbols."""

from __future__ import annotations

import ast
import hashlib
import os
import subprocess
import sys
from pathlib import Path

from rtalk.models import (
    Chunk,
    IndexRecord,
    RecordKind,
    SymbolKind,
    SymbolRecord,
)

ALLOWED_EXTENSIONS: set[str] = {".py", ".md", ".txt", ".yml", ".yaml", ".toml", ".json"}

DEFAULT_CHUNK_SIZE = 80


def _sha256(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def _git_tracked_files(repo_path: str) -> list[str] | None:
    """Return git-tracked files, or None if not a git repo."""
    try:
        result = subprocess.run(
            ["git", "ls-files", "--cached", "--others", "--exclude-standard"],
            cwd=repo_path,
            capture_output=True,
            text=True,
            check=True,
        )
        return [f for f in result.stdout.strip().split("\n") if f]
    except (subprocess.CalledProcessError, FileNotFoundError):
        return None


def _walk_files(repo_path: str) -> list[str]:
    """Walk directory, skipping hidden dirs and common noise."""
    skip_dirs = {".git", "__pycache__", "node_modules", ".venv", "venv", ".tox", ".eggs"}
    files: list[str] = []
    for dirpath, dirnames, filenames in os.walk(repo_path):
        dirnames[:] = [d for d in dirnames if d not in skip_dirs and not d.startswith(".")]
        for fn in filenames:
            full = os.path.join(dirpath, fn)
            rel = os.path.relpath(full, repo_path)
            files.append(rel)
    return files


def _collect_files(repo_path: str) -> list[str]:
    """Collect files to index, respecting .gitignore when possible."""
    tracked = _git_tracked_files(repo_path)
    candidates = tracked if tracked is not None else _walk_files(repo_path)
    return [
        f
        for f in candidates
        if any(f.endswith(ext) for ext in ALLOWED_EXTENSIONS)
    ]


def _chunk_lines(lines: list[str], chunk_size: int) -> list[Chunk]:
    """Split lines into fixed-size chunks."""
    chunks: list[Chunk] = []
    for start in range(0, len(lines), chunk_size):
        end = min(start + chunk_size, len(lines))
        text = "\n".join(lines[start:end])
        chunks.append(Chunk(start_line=start + 1, end_line=end, text=text))
    return chunks


def _extract_python_symbols(source: str, file_path: str) -> list[SymbolRecord]:
    """Use the ast module to extract functions, classes, and imports with line ranges."""
    symbols: list[SymbolRecord] = []
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return symbols

    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            end = node.end_lineno or node.lineno
            symbols.append(
                SymbolRecord(
                    name=node.name,
                    kind=SymbolKind.FUNCTION,
                    start_line=node.lineno,
                    end_line=end,
                    file_path=file_path,
                )
            )
        elif isinstance(node, ast.ClassDef):
            end = node.end_lineno or node.lineno
            symbols.append(
                SymbolRecord(
                    name=node.name,
                    kind=SymbolKind.CLASS,
                    start_line=node.lineno,
                    end_line=end,
                    file_path=file_path,
                )
            )
        elif isinstance(node, ast.Import):
            for alias in node.names:
                symbols.append(
                    SymbolRecord(
                        name=alias.name,
                        kind=SymbolKind.IMPORT,
                        start_line=node.lineno,
                        end_line=node.end_lineno or node.lineno,
                        file_path=file_path,
                    )
                )
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            for alias in node.names:
                symbols.append(
                    SymbolRecord(
                        name=f"{module}.{alias.name}",
                        kind=SymbolKind.IMPORT,
                        start_line=node.lineno,
                        end_line=node.end_lineno or node.lineno,
                        file_path=file_path,
                    )
                )

    return symbols


def index_file(
    repo_path: str,
    rel_path: str,
    chunk_size: int = DEFAULT_CHUNK_SIZE,
) -> list[IndexRecord]:
    """Index a single file, returning chunk and symbol records."""
    full_path = os.path.join(repo_path, rel_path)
    try:
        with open(full_path, "r", encoding="utf-8", errors="replace") as f:
            content = f.read()
    except OSError:
        return []

    lines = content.split("\n")
    sha = _sha256(content)
    total = len(lines)
    records: list[IndexRecord] = []

    for chunk in _chunk_lines(lines, chunk_size):
        records.append(
            IndexRecord(
                record_kind=RecordKind.FILE_CHUNK,
                file_path=rel_path,
                sha256=sha,
                total_lines=total,
                chunk=chunk,
            )
        )

    if rel_path.endswith(".py"):
        for sym in _extract_python_symbols(content, rel_path):
            records.append(
                IndexRecord(
                    record_kind=RecordKind.SYMBOL,
                    file_path=rel_path,
                    sha256=sha,
                    total_lines=total,
                    symbol=sym,
                )
            )

    return records


def build_index(
    repo_path: str,
    out_path: str = ".rtalk/index.jsonl",
    chunk_size: int = DEFAULT_CHUNK_SIZE,
) -> int:
    """Build the full index for a repository. Returns number of records written."""
    repo_path = os.path.abspath(repo_path)
    if not os.path.isdir(repo_path):
        print(f"Error: {repo_path} is not a directory", file=sys.stderr)
        sys.exit(1)

    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)

    files = _collect_files(repo_path)
    if not files:
        print("Warning: no indexable files found.", file=sys.stderr)

    count = 0
    with open(out, "w", encoding="utf-8") as fout:
        for rel in sorted(files):
            for record in index_file(repo_path, rel, chunk_size):
                fout.write(record.to_json_line() + "\n")
                count += 1

    print(f"Indexed {len(files)} files -> {count} records written to {out_path}")
    return count
