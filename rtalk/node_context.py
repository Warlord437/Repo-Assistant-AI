"""Node context -- file-specific RAG for map graph side panel.

Retrieves chunks and symbols for a clicked file from the index, sends to LLM
for a precise explanation. Avoids generic repo overview.
"""

from __future__ import annotations

import os
from rtalk.models import IndexRecord, RecordKind, SymbolKind


def _load_records(index_path: str) -> list[IndexRecord]:
    records: list[IndexRecord] = []
    with open(index_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                records.append(IndexRecord.from_json_line(line))
    return records


def _get_file_chunks(records: list[IndexRecord], file_path: str) -> list[tuple[int, int, str]]:
    """Get all chunks for a file, sorted by start_line. Returns [(start, end, text), ...]."""
    out: list[tuple[int, int, str]] = []
    for rec in records:
        if rec.record_kind != RecordKind.FILE_CHUNK or rec.file_path != file_path or not rec.chunk:
            continue
        out.append((rec.chunk.start_line, rec.chunk.end_line, rec.chunk.text))
    out.sort(key=lambda x: x[0])
    return out


def _get_file_symbols(records: list[IndexRecord], file_path: str) -> list[tuple[str, str, int]]:
    """Get classes and functions for a file. Returns [(name, kind, line), ...]."""
    out: list[tuple[str, str, int]] = []
    for rec in records:
        if rec.record_kind != RecordKind.SYMBOL or rec.file_path != file_path or not rec.symbol:
            continue
        if rec.symbol.kind in (SymbolKind.CLASS, SymbolKind.FUNCTION):
            out.append((rec.symbol.name, rec.symbol.kind.value, rec.symbol.start_line))
    out.sort(key=lambda x: x[2])
    return out


def _build_code_context(chunks: list[tuple[int, int, str]], max_chars: int = 8000) -> str:
    """Build code block from chunks, truncating if needed."""
    parts: list[str] = []
    total = 0
    for start, end, text in chunks:
        block = f"[Lines {start}-{end}]\n{text}"
        if total + len(block) > max_chars:
            remaining = max_chars - total - 80
            if remaining > 200:
                parts.append(block[:remaining] + "\n... [truncated]")
            break
        parts.append(block)
        total += len(block)
    return "\n\n".join(parts)


def explain_file(
    index_path: str,
    file_path: str,
    ai_api_key: str,
    repo_path: str | None = None,
) -> tuple[str, str | None]:
    """Generate AI explanation for a specific file using its indexed chunks.

    Uses RAG: retrieves actual code from the index, sends to LLM.
    Returns (summary, error_message). error_message is None on success.
    """
    if not os.path.isfile(index_path):
        return "", f"Index not found: {index_path}"

    records = _load_records(index_path)
    chunks = _get_file_chunks(records, file_path)
    symbols = _get_file_symbols(records, file_path)

    if not chunks:
        return "", (
            f"File '{file_path}' not found in index. "
            "The graph may be from a different repo. Try re-indexing (Refresh + Index Repo)."
        )

    code_context = _build_code_context(chunks)
    symbol_list = ", ".join(f"{k} {n} (L{s})" for n, k, s in symbols[:20]) if symbols else "none"

    prompt = (
        f"You are explaining a specific file from a codebase. Use ONLY the code below.\n\n"
        f"File: {file_path}\n"
        f"Symbols (classes/functions): {symbol_list}\n\n"
        f"Code:\n{code_context}\n\n"
        f"Write 2-4 sentences: What does this file do? What are its key responsibilities? "
        f"Name the main functions or classes if relevant. Be specific to this file's code."
    )

    try:
        from rtalk.groq_client import groq_chat
        result = groq_chat(
            ai_api_key,
            [{"role": "user", "content": prompt}],
            max_tokens=250,
        )
        return (result or "", None)
    except Exception as e:
        return "", str(e)


def explain_folder(
    index_path: str,
    folder: str,
    ai_api_key: str,
    centrality_list: list[dict] | None = None,
) -> tuple[str, str | None]:
    """Generate AI explanation for a folder using top files' chunks.

    If centrality_list provided (from graph centralty), uses it to pick top files.
    Otherwise picks first N files in folder.
    Returns (summary, error_message). error_message is None on success.
    """
    if not os.path.isfile(index_path):
        return "", f"Index not found: {index_path}"

    records = _load_records(index_path)
    prefix = folder.rstrip("/") + "/" if folder != "." else ""
    in_folder = [r for r in records if r.file_path.startswith(prefix) or (folder == "." and "/" not in r.file_path)]

    file_chunks: dict[str, list[tuple[int, int, str]]] = {}
    for rec in in_folder:
        if rec.record_kind == RecordKind.FILE_CHUNK and rec.chunk:
            fp = rec.file_path
            if fp not in file_chunks:
                file_chunks[fp] = []
            file_chunks[fp].append((rec.chunk.start_line, rec.chunk.end_line, rec.chunk.text))

    for fp in file_chunks:
        file_chunks[fp].sort(key=lambda x: x[0])

    files = list(file_chunks.keys())
    if centrality_list:
        centrality = {c["file"]: c.get("score", 0) for c in centrality_list if isinstance(c, dict)}
        files.sort(key=lambda f: (-centrality.get(f, 0), f))
    files = files[:6]

    parts: list[str] = []
    total = 0
    max_total = 6000
    for fp in files:
        chunks = file_chunks.get(fp, [])
        ctx = _build_code_context(chunks, max_chars=max_total - total - 100)
        if not ctx:
            continue
        parts.append(f"=== {fp} ===\n{ctx}")
        total += len(ctx)
        if total >= max_total:
            break

    if not parts:
        return "", f"No indexed files found in folder '{folder}/'."

    prompt = (
        f"You are explaining a folder from a codebase. Use ONLY the code below.\n\n"
        f"Folder: {folder}/\n\n"
        f"Code from top files:\n\n" + "\n\n".join(parts) + "\n\n"
        f"Write 2-4 sentences: What does this folder do? What are its main components? "
        f"Be specific to the code shown."
    )

    try:
        from rtalk.groq_client import groq_chat
        result = groq_chat(
            ai_api_key,
            [{"role": "user", "content": prompt}],
            max_tokens=250,
        )
        return (result or "", None)
    except Exception as e:
        return "", str(e)
