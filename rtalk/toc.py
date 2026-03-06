"""Table of Contents for vectorless RAG.

PageIndex-style hierarchical tree: folder -> file -> symbol -> chunk.
Enables two-phase retrieval: (1) navigate TOC to find relevant nodes, (2) fetch chunk content.
Reference: https://docs.pageindex.ai/cookbook/vectorless-rag-pageindex
"""

from __future__ import annotations

import os
import re
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any

from rtalk.models import IndexRecord, RecordKind, SymbolKind


def _tokenize(text: str) -> list[str]:
    return re.findall(r"[a-z0-9_]+", text.lower())


@dataclass
class TOCNode:
    """A node in the table-of-contents tree."""

    node_id: str
    title: str
    file_path: str = ""
    start_line: int = 0
    end_line: int = 0
    text: str = ""
    kind: str = ""
    children: list["TOCNode"] = field(default_factory=list)
    chunk_record: IndexRecord | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "node_id": self.node_id,
            "title": self.title,
            "file_path": self.file_path,
            "start_line": self.start_line,
            "end_line": self.end_line,
            "text": self.text[:200] if self.text else "",
            "kind": self.kind,
            "children": [c.to_dict() for c in self.children],
        }


def _folder_from_path(fp: str, depth: int = 2) -> str:
    parts = fp.split("/")
    if len(parts) <= 1:
        return "."
    return "/".join(parts[:-1][:depth])


def build_toc(records: list[IndexRecord]) -> list[TOCNode]:
    """Build hierarchical TOC from index records.

    Structure: folder -> file -> symbol (class/function) -> chunk.
    Mirrors PageIndex: table of contents before retrieval.
    """
    folders: dict[str, dict[str, list[TOCNode]]] = defaultdict(lambda: defaultdict(list))
    file_symbols: dict[str, list[tuple[int, int, str, str]]] = defaultdict(list)
    file_chunks: dict[str, list[tuple[IndexRecord, str]]] = defaultdict(list)

    for rec in records:
        if rec.record_kind == RecordKind.SYMBOL and rec.symbol:
            sym = rec.symbol
            if sym.kind in (SymbolKind.CLASS, SymbolKind.FUNCTION):
                file_symbols[rec.file_path].append(
                    (sym.start_line, sym.end_line, sym.name, sym.kind.value)
                )
        elif rec.record_kind == RecordKind.FILE_CHUNK and rec.chunk:
            first_line = rec.chunk.text.split("\n")[0].strip()[:80]
            file_chunks[rec.file_path].append((rec, first_line))

    node_counter = [0]

    def next_id() -> str:
        node_counter[0] += 1
        return f"n{node_counter[0]:04d}"

    def make_symbol_nodes(file_path: str) -> list[TOCNode]:
        symbols = sorted(file_symbols.get(file_path, []), key=lambda x: x[0])
        chunks = sorted(file_chunks.get(file_path, []), key=lambda x: x[0].chunk.start_line)
        nodes: list[TOCNode] = []

        for start, end, name, kind in symbols:
            nid = next_id()
            child_chunks: list[TOCNode] = []
            for rec, first_line in chunks:
                if rec.chunk and rec.chunk.start_line >= start and rec.chunk.end_line <= end:
                    child_chunks.append(
                        TOCNode(
                            node_id=next_id(),
                            title=first_line or f"lines {rec.chunk.start_line}-{rec.chunk.end_line}",
                            file_path=file_path,
                            start_line=rec.chunk.start_line,
                            end_line=rec.chunk.end_line,
                            text=rec.chunk.text[:300],
                            kind="chunk",
                            chunk_record=rec,
                        )
                    )
            if not child_chunks:
                for rec, first_line in chunks:
                    if rec.chunk and rec.chunk.start_line <= end and rec.chunk.end_line >= start:
                        child_chunks.append(
                            TOCNode(
                                node_id=next_id(),
                                title=first_line or f"lines {rec.chunk.start_line}-{rec.chunk.end_line}",
                                file_path=file_path,
                                start_line=rec.chunk.start_line,
                                end_line=rec.chunk.end_line,
                                text=rec.chunk.text[:300],
                                kind="chunk",
                                chunk_record=rec,
                            )
                        )
                        break
            nodes.append(
                TOCNode(
                    node_id=nid,
                    title=name,
                    file_path=file_path,
                    start_line=start,
                    end_line=end,
                    text=f"{kind} {name}",
                    kind=kind,
                    children=child_chunks[:3],
                )
            )
        return nodes

    for rec in records:
        if rec.record_kind != RecordKind.FILE_CHUNK or not rec.chunk:
            continue
        fp = rec.file_path
        folder = _folder_from_path(fp)
        base = os.path.basename(fp)

        if fp not in folders[folder]:
            sym_nodes = make_symbol_nodes(fp)
            if sym_nodes:
                file_node = TOCNode(
                    node_id=next_id(),
                    title=base,
                    file_path=fp,
                    start_line=0,
                    end_line=0,
                    text=base,
                    kind="file",
                    children=sym_nodes,
                )
            else:
                chunk_nodes = []
                for r, first in file_chunks.get(fp, []):
                    if r.chunk:
                        chunk_nodes.append(
                            TOCNode(
                                node_id=next_id(),
                                title=first or f"lines {r.chunk.start_line}-{r.chunk.end_line}",
                                file_path=fp,
                                start_line=r.chunk.start_line,
                                end_line=r.chunk.end_line,
                                text=r.chunk.text[:300],
                                kind="chunk",
                                chunk_record=r,
                            )
                        )
                file_node = TOCNode(
                    node_id=next_id(),
                    title=base,
                    file_path=fp,
                    kind="file",
                    children=chunk_nodes[:5],
                )
            folders[folder][fp] = [file_node]

    root: list[TOCNode] = []
    for folder in sorted(folders.keys()):
        file_nodes = []
        for fp in sorted(folders[folder].keys()):
            file_nodes.extend(folders[folder][fp])
        if file_nodes:
            root.append(
                TOCNode(
                    node_id=next_id(),
                    title=folder or ".",
                    file_path="",
                    text=folder or "root",
                    kind="folder",
                    children=file_nodes,
                )
            )
    return root


class TOCIndex:
    """Inverted index over TOC node titles and text for fast navigation."""

    def __init__(self, toc: list[TOCNode]) -> None:
        self.toc = toc
        self._node_tokens: dict[str, list[str]] = {}
        self._df: dict[str, int] = defaultdict(int)
        self._all_chunk_records: list[IndexRecord] = []
        self._build_index()

    def _add_node(self, node: TOCNode) -> None:
        text = f"{node.title} {node.text} {node.file_path}"
        tokens = _tokenize(text)
        self._node_tokens[node.node_id] = tokens
        for t in set(tokens):
            self._df[t] += 1
        if node.chunk_record:
            self._all_chunk_records.append(node.chunk_record)
        for c in node.children:
            self._add_node(c)

    def _build_index(self) -> None:
        for node in self.toc:
            self._add_node(node)

    def _score_node(self, node_id: str, query_tokens: list[str]) -> float:
        tokens = self._node_tokens.get(node_id, [])
        if not tokens or not query_tokens:
            return 0.0
        hits = sum(1 for qt in query_tokens if qt in tokens)
        return hits / len(query_tokens)

    def search_nodes(
        self, query: str, top_k: int = 10
    ) -> list[tuple[TOCNode, float, list[str]]]:
        """Phase 1: Find relevant TOC nodes by lexical match on titles/summaries."""
        query_tokens = _tokenize(query)
        if not query_tokens:
            return []

        scored: list[tuple[TOCNode, float, list[str]]] = []

        def visit(node: TOCNode, path: list[str]) -> None:
            path = path + [node.title]
            score = self._score_node(node.node_id, query_tokens)
            if node.chunk_record and score > 0:
                scored.append((node, score, path))
            for c in node.children:
                visit(c, path)

        for root_node in self.toc:
            visit(root_node, [])

        scored.sort(key=lambda x: x[1], reverse=True)
        return scored[:top_k]

    def get_chunk_records_from_nodes(
        self, nodes: list[tuple[TOCNode, float, list[str]]]
    ) -> list[tuple[IndexRecord, float, list[str]]]:
        """Phase 2: Extract chunk records from matched TOC nodes."""
        result: list[tuple[IndexRecord, float, list[str]]] = []
        seen: set[str] = set()
        for node, score, path in nodes:
            if node.chunk_record:
                key = f"{node.file_path}:{node.start_line}"
                if key not in seen:
                    seen.add(key)
                    result.append((node.chunk_record, score, path))
        return result
