"""Retrieval engine with lexical (TF-IDF) and exact search over the index.

PageIndex-style: builds a table-of-contents (TOC) tree first, then two-phase retrieval:
(1) Navigate TOC to find relevant nodes, (2) fetch chunk content. Falls back to direct
TF-IDF when TOC yields few results.
Reference: https://docs.pageindex.ai/cookbook/vectorless-rag-pageindex
"""

from __future__ import annotations

import math
import os
import re
import shutil
import subprocess
from collections import Counter
from pathlib import Path

from rtalk.models import EvidenceSnippet, IndexRecord, RecordKind

MAX_SNIPPET_LINES = 60

_EXPLAIN_PATTERNS = re.compile(
    r"\b(explain|overview|architecture|how to run|entrypoint|entry point|"
    r"folders|structure|what does this repo|what is this|summarize|summary)\b",
    re.IGNORECASE,
)

_FILENAME_HINTS: dict[str, list[str]] = {
    "search": ["retrieval", "search", "index", "query"],
    "retrieval": ["retrieval", "search", "index"],
    "index": ["index", "indexer"],
    "test": ["test_", "tests"],
    "api": ["server", "api", "app", "routes"],
    "config": ["config", "settings", "pyproject", "setup"],
    "model": ["model", "schema", "types"],
    "database": ["db", "database", "migration", "models"],
    "auth": ["auth", "login", "session", "token"],
}


def _tokenize(text: str) -> list[str]:
    """Lowercase split on non-alphanumeric boundaries."""
    return re.findall(r"[a-z0-9_]+", text.lower())


def detect_explain_intent(query: str) -> bool:
    """Return True if the query is asking for a repo-level explanation."""
    return bool(_EXPLAIN_PATTERNS.search(query))


def _filename_boost_score(file_path: str, query_tokens: list[str]) -> float:
    """Boost score for files whose names match query intent."""
    base = os.path.basename(file_path).lower().replace(".py", "").replace(".md", "")
    boost = 0.0

    for token in query_tokens:
        if token in base:
            boost += 0.3
        hints = _FILENAME_HINTS.get(token, [])
        for h in hints:
            if h in base or h in file_path.lower():
                boost += 0.15

    if base in ("readme", "readme.md"):
        if any(t in ("run", "install", "setup", "start", "overview") for t in query_tokens):
            boost += 0.25

    return min(boost, 0.8)


def _symbol_boost_score(record: IndexRecord, query_tokens: list[str]) -> float:
    """Boost score when a query matches a symbol name."""
    if not record.symbol:
        return 0.0
    sym_lower = record.symbol.name.lower()
    for token in query_tokens:
        if token == sym_lower or token in sym_lower:
            return 0.4
    return 0.0


def _overlaps(a: EvidenceSnippet, b: EvidenceSnippet) -> bool:
    """Return True if two snippets from the same file have overlapping line ranges."""
    if a.file_path != b.file_path:
        return False
    return a.start_line <= b.end_line and b.start_line <= a.end_line


def _deduplicate(snippets: list[EvidenceSnippet], score_threshold: float = 0.3) -> list[EvidenceSnippet]:
    """Remove overlapping snippets from the same file unless scores differ significantly."""
    result: list[EvidenceSnippet] = []
    for sn in snippets:
        dominated = False
        for existing in result:
            if _overlaps(sn, existing):
                if abs(sn.score - existing.score) < score_threshold:
                    dominated = True
                    break
        if not dominated:
            result.append(sn)
    return result


class TFIDFIndex:
    """Lightweight in-memory TF-IDF index over text chunks."""

    def __init__(self) -> None:
        self.docs: list[IndexRecord] = []
        self.doc_tokens: list[list[str]] = []
        self.df: Counter[str] = Counter()
        self.n_docs: int = 0

    def add(self, record: IndexRecord, text: str) -> None:
        tokens = _tokenize(text)
        self.docs.append(record)
        self.doc_tokens.append(tokens)
        unique = set(tokens)
        for tok in unique:
            self.df[tok] += 1
        self.n_docs += 1

    def search(self, query: str, top_k: int = 5) -> list[tuple[IndexRecord, float]]:
        query_tokens = _tokenize(query)
        if not query_tokens:
            return []

        scores: list[float] = []
        for idx, tokens in enumerate(self.doc_tokens):
            if not tokens:
                scores.append(0.0)
                continue
            tf = Counter(tokens)
            score = 0.0
            for qt in query_tokens:
                if qt in tf:
                    term_freq = tf[qt] / len(tokens)
                    idf = math.log((self.n_docs + 1) / (self.df.get(qt, 0) + 1)) + 1
                    score += term_freq * idf
            rec = self.docs[idx]
            score += _filename_boost_score(rec.file_path, query_tokens)
            score += _symbol_boost_score(rec, query_tokens)
            scores.append(score)

        ranked = sorted(enumerate(scores), key=lambda x: x[1], reverse=True)
        results: list[tuple[IndexRecord, float]] = []
        for idx, sc in ranked[:top_k]:
            if sc > 0:
                results.append((self.docs[idx], sc))
        return results


def _ripgrep_search(
    query: str, repo_path: str, max_results: int = 10
) -> list[EvidenceSnippet]:
    """Use ripgrep for exact substring search if available."""
    rg = shutil.which("rg")
    if not rg:
        return []

    try:
        result = subprocess.run(
            [
                rg,
                "--line-number",
                "--no-heading",
                "--max-count",
                str(max_results * 2),
                "--context",
                "3",
                query,
                repo_path,
            ],
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (subprocess.TimeoutExpired, OSError):
        return []

    if result.returncode not in (0, 1):
        return []

    snippets: list[EvidenceSnippet] = []
    current_file: str = ""
    current_lines: list[str] = []
    current_start: int = 0
    current_end: int = 0

    def flush() -> None:
        nonlocal current_file, current_lines, current_start, current_end
        if current_file and current_lines:
            rel = os.path.relpath(current_file, repo_path)
            text = "\n".join(current_lines[:MAX_SNIPPET_LINES])
            snippets.append(
                EvidenceSnippet(
                    file_path=rel,
                    start_line=current_start,
                    end_line=current_end,
                    text=text,
                    score=1.0,
                    method="exact",
                )
            )
        current_lines = []

    for line in result.stdout.split("\n"):
        if line == "--":
            flush()
            continue
        match = re.match(r"^(.+?)[:\-](\d+)[:\-](.*)", line)
        if not match:
            continue
        fpath, lineno_str, content = match.groups()
        lineno = int(lineno_str)
        if fpath != current_file:
            flush()
            current_file = fpath
            current_start = lineno
        if not current_lines:
            current_start = lineno
        current_end = lineno
        current_lines.append(content)

    flush()
    return snippets[:max_results]


def _python_exact_search(
    query: str, records: list[IndexRecord], max_results: int = 10
) -> list[EvidenceSnippet]:
    """Pure-Python fallback for exact substring search in indexed chunks."""
    results: list[EvidenceSnippet] = []
    query_lower = query.lower()

    for rec in records:
        if rec.record_kind != RecordKind.FILE_CHUNK or not rec.chunk:
            continue
        if query_lower in rec.chunk.text.lower():
            lines = rec.chunk.text.split("\n")
            results.append(
                EvidenceSnippet(
                    file_path=rec.file_path,
                    start_line=rec.chunk.start_line,
                    end_line=rec.chunk.end_line,
                    text="\n".join(lines[:MAX_SNIPPET_LINES]),
                    score=1.0,
                    method="exact",
                )
            )
            if len(results) >= max_results:
                break
    return results


class RetrievalEngine:
    """Combined retrieval: TOC navigation + lexical (TF-IDF) + exact search."""

    def __init__(self, records: list[IndexRecord], repo_path: str | None = None) -> None:
        self.records = records
        self.repo_path = repo_path
        self._tfidf = TFIDFIndex()
        for rec in records:
            text = ""
            if rec.chunk:
                text = rec.chunk.text
            elif rec.symbol:
                text = f"{rec.symbol.kind.value} {rec.symbol.name} {rec.file_path}"
            if text:
                self._tfidf.add(rec, text)
        from rtalk.toc import build_toc, TOCIndex
        self._toc = build_toc(records)
        self._toc_index = TOCIndex(self._toc)

    @classmethod
    def from_index(cls, index_path: str, repo_path: str | None = None) -> "RetrievalEngine":
        """Load engine from a JSONL index file."""
        records: list[IndexRecord] = []
        with open(index_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    records.append(IndexRecord.from_json_line(line))
        return cls(records, repo_path)

    def _search_via_toc(self, query: str, fetch_k: int) -> list[EvidenceSnippet]:
        """Phase 1+2: Search TOC nodes, then convert to evidence snippets."""
        nodes = self._toc_index.search_nodes(query, top_k=fetch_k)
        if not nodes:
            return []
        chunk_records = self._toc_index.get_chunk_records_from_nodes(nodes)
        snippets: list[EvidenceSnippet] = []
        for rec, score, path in chunk_records:
            if rec.chunk:
                lines = rec.chunk.text.split("\n")
                text = "\n".join(lines[:MAX_SNIPPET_LINES])
                structural_boost = 0.1 * len(path)
                snippets.append(
                    EvidenceSnippet(
                        file_path=rec.file_path,
                        start_line=rec.chunk.start_line,
                        end_line=rec.chunk.end_line,
                        text=text,
                        score=score + structural_boost,
                        method="toc",
                    )
                )
        return snippets

    def _search_with_query(self, query: str, fetch_k: int) -> list[EvidenceSnippet]:
        """Internal: run search with given query. Returns raw snippets.
        Two-phase: TOC navigation first, then TF-IDF + exact as fallback/supplement."""
        snippets: list[EvidenceSnippet] = []
        seen: set[str] = set()

        toc_results = self._search_via_toc(query, fetch_k)
        for sn in toc_results:
            key = f"{sn.file_path}:{sn.start_line}"
            if key not in seen:
                seen.add(key)
                snippets.append(sn)

        if self.repo_path:
            for sn in _ripgrep_search(query, self.repo_path, max_results=fetch_k):
                key = f"{sn.file_path}:{sn.start_line}"
                if key not in seen:
                    seen.add(key)
                    snippets.append(sn)
        else:
            for sn in _python_exact_search(query, self.records, max_results=fetch_k):
                key = f"{sn.file_path}:{sn.start_line}"
                if key not in seen:
                    seen.add(key)
                    snippets.append(sn)

        for rec, score in self._tfidf.search(query, top_k=fetch_k):
            if rec.chunk:
                key = f"{rec.file_path}:{rec.chunk.start_line}"
                if key not in seen:
                    seen.add(key)
                    lines = rec.chunk.text.split("\n")
                    snippets.append(
                        EvidenceSnippet(
                            file_path=rec.file_path,
                            start_line=rec.chunk.start_line,
                            end_line=rec.chunk.end_line,
                            text="\n".join(lines[:MAX_SNIPPET_LINES]),
                            score=score,
                            method="lexical",
                        )
                    )
        return snippets

    def _search_via_llm_nav(
        self,
        query: str,
        ai_api_key: str,
        schema_understanding: str = "",
        fetch_k: int = 10,
    ) -> list[EvidenceSnippet]:
        """PageIndex Phase 2A+2B: LLM selects TOC nodes, then extract chunks."""
        try:
            from rtalk.generation import (
                llm_select_nodes,
                extract_chunks_from_node_ids,
                build_evidence_from_records,
            )
            node_ids = llm_select_nodes(
                self._toc, query, ai_api_key, schema_hint=schema_understanding
            )
            if not node_ids:
                return []
            records_with_path = extract_chunks_from_node_ids(self._toc, node_ids)
            return build_evidence_from_records(records_with_path, MAX_SNIPPET_LINES)
        except Exception:
            return []

    def search(
        self,
        query: str,
        top_k: int = 5,
        ai_api_key: str | None = None,
        schema_understanding: str = "",
    ) -> list[EvidenceSnippet]:
        """Run combined search and return deduplicated, ranked evidence.
        When ai_api_key is provided, runs PageIndex-style LLM tree search first."""
        fetch_k = top_k * 3
        snippets: list[EvidenceSnippet] = []
        seen: set[str] = set()

        if ai_api_key and ai_api_key.strip():
            llm_nav = self._search_via_llm_nav(
                query, ai_api_key, schema_understanding, fetch_k
            )
            for sn in llm_nav:
                key = f"{sn.file_path}:{sn.start_line}"
                if key not in seen:
                    seen.add(key)
                    snippets.append(sn)

        base = self._search_with_query(query, fetch_k)
        for sn in base:
            key = f"{sn.file_path}:{sn.start_line}"
            if key not in seen:
                seen.add(key)
                snippets.append(sn)

        if not snippets and self._tfidf.n_docs > 0:
            tokens = _tokenize(query)
            stop = {"how", "does", "the", "a", "an", "is", "are", "what", "where", "when", "do", "work"}
            keywords = [t for t in tokens if t not in stop and len(t) > 1]
            for kw in keywords[:3]:
                expanded = " ".join(_FILENAME_HINTS.get(kw, [kw]))
                fallback = self._search_with_query(expanded, fetch_k)
                if fallback:
                    for sn in fallback:
                        key = f"{sn.file_path}:{sn.start_line}"
                        if key not in seen:
                            seen.add(key)
                            snippets.append(sn)
                    break

        snippets.sort(key=lambda s: s.score, reverse=True)
        snippets = _deduplicate(snippets)
        return snippets[:top_k]

    def search_lexical(self, query: str, top_k: int = 5) -> list[EvidenceSnippet]:
        """Lexical search only (TF-IDF)."""
        results: list[EvidenceSnippet] = []
        for rec, score in self._tfidf.search(query, top_k=top_k):
            if rec.chunk:
                results.append(
                    EvidenceSnippet(
                        file_path=rec.file_path,
                        start_line=rec.chunk.start_line,
                        end_line=rec.chunk.end_line,
                        text="\n".join(rec.chunk.text.split("\n")[:MAX_SNIPPET_LINES]),
                        score=score,
                        method="lexical",
                    )
                )
        return results

    def search_exact(self, query: str, top_k: int = 5) -> list[EvidenceSnippet]:
        """Exact search only (ripgrep with Python fallback)."""
        if self.repo_path:
            results = _ripgrep_search(query, self.repo_path, max_results=top_k)
            if results:
                return results
        return _python_exact_search(query, self.records, max_results=top_k)
