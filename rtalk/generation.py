"""PageIndex-style generation pipeline.

Reference: https://docs.pageindex.ai/cookbook/vectorless-rag-pageindex

Phase 2A (Navigate): TOC + Query → LLM → Selected Node IDs
Phase 2B (Extract):   Node IDs → Chunk records
Phase 2C (Answer):   Extracted text + Query → LLM → Final answer with citations
"""

from __future__ import annotations

import json
import re
from typing import Any

from rtalk.models import EvidenceSnippet, IndexRecord

MAX_TOC_CHARS = 8000


def _node_to_dict(node: Any, depth: int) -> dict:
    """Convert TOC node to compact dict for LLM."""
    nid = getattr(node, "node_id", "")
    title = getattr(node, "title", "")
    text = (getattr(node, "text", "") or "")[:80]
    summary = text or title
    children = getattr(node, "children", [])
    kind = getattr(node, "kind", "")
    entry: dict = {"node_id": nid, "title": title, "summary": summary, "kind": kind}
    if children and depth < 2:
        entry["nodes"] = [_node_to_dict(c, depth + 1) for c in children[:8]]
    return entry


def _serialize_toc_for_llm(nodes: list[Any], max_chars: int = MAX_TOC_CHARS) -> str:
    """Serialize TOC tree to compact JSON for LLM (node_id, title, summary only)."""
    result = [_node_to_dict(n, 0) for n in nodes]
    return json.dumps(result, ensure_ascii=False, indent=2)[:max_chars]


def llm_select_nodes(
    toc_root: list[Any],
    query: str,
    groq_api_key: str,
    schema_hint: str = "",
) -> list[str]:
    """Phase 2A: LLM reasons over TOC to select relevant node IDs.

    PageIndex-style: "You have a TOC tree. Identify which node IDs are MOST likely
    to contain the answer. Output comma-separated node IDs."
    """
    toc_json = _serialize_toc_for_llm(toc_root)
    schema_block = f"\n\nCodebase structure (for context):\n{schema_hint}\n\n" if schema_hint else ""

    prompt = f"""You are a codebase navigation agent.
You have a Table-of-Contents (TOC) tree of a codebase. Each node has 'node_id', 'title', and 'summary'.
Your job: read the user's query, study the TOC summaries, and identify which node IDs are MOST likely to contain the answer. Do NOT guess — reason from the summaries.
{schema_block}
RULES:
- Output ONLY a comma-separated list of node_id values. Nothing else. No explanation.
- Return 1 to 5 node IDs maximum. Prefer the most specific match.
- Example output: n0001, n0002, n0003

TOC TREE:
{toc_json}

USER QUERY: {query}

RELEVANT NODE IDS (comma-separated only):"""

    try:
        from rtalk.groq_client import groq_chat
        raw = groq_chat(groq_api_key, [{"role": "user", "content": prompt}], max_tokens=150)
        raw = raw.strip().strip(".").strip()
        node_ids = [x.strip() for x in re.split(r"[,;\s]+", raw) if x.strip()]
        return [n for n in node_ids if n.startswith("n") and n[1:].isdigit()][:5]
    except Exception:
        return []


def extract_chunks_from_node_ids(
    toc_root: list[Any],
    node_ids: list[str],
) -> list[tuple[IndexRecord, float, list[str]]]:
    """Phase 2B: Resolve node IDs to chunk records. No LLM.
    When a selected node has no chunk_record, collect chunks from its descendants."""
    node_id_set = set(node_ids)
    result: list[tuple[IndexRecord, float, list[str]]] = []
    seen: set[str] = set()

    def collect_chunks(node: Any, path: list[str], selected: bool) -> None:
        nid = getattr(node, "node_id", "")
        title = getattr(node, "title", "")
        children = getattr(node, "children", [])
        new_path = path + [title]
        is_selected = nid in node_id_set
        if getattr(node, "chunk_record", None) and (is_selected or selected):
            rec = node.chunk_record
            key = f"{rec.file_path}:{rec.chunk.start_line}"
            if key not in seen:
                seen.add(key)
                result.append((rec, 1.0, new_path))
        for c in children:
            collect_chunks(c, new_path, selected or is_selected)

    for node in toc_root:
        collect_chunks(node, [], False)
    return result


def build_evidence_from_records(
    records_with_path: list[tuple[IndexRecord, float, list[str]]],
    max_snippet_lines: int = 60,
) -> list[EvidenceSnippet]:
    """Convert chunk records to EvidenceSnippets with structural boost."""
    snippets: list[EvidenceSnippet] = []
    for rec, score, path in records_with_path:
        if rec.chunk:
            lines = rec.chunk.text.split("\n")
            text = "\n".join(lines[:max_snippet_lines])
            structural_boost = 0.15 * len(path)
            snippets.append(
                EvidenceSnippet(
                    file_path=rec.file_path,
                    start_line=rec.chunk.start_line,
                    end_line=rec.chunk.end_line,
                    text=text,
                    score=score + structural_boost,
                    method="llm_nav",
                )
            )
    return snippets


def synthesize_answer_prompt(
    query: str,
    evidence: list[EvidenceSnippet],
    schema_understanding: str = "",
) -> str:
    """Phase 2C: PageIndex-style answer synthesis prompt.

    'You have been given a RELEVANT EXCERPT (already pre-selected by a reasoning agent).
    Answer using ONLY this excerpt. At the end, cite the section(s) you drew from.'
    """
    evidence_text = "\n".join(
        f"[{i}] {ev.citation()}:\n{ev.text[:400]}" for i, ev in enumerate(evidence, 1)
    )
    schema_block = ""
    if schema_understanding:
        schema_block = f"\n\nCodebase structure (for context):\n{schema_understanding}\n\n"

    return f"""You are a precise question-answering assistant.
You have been given a RELEVANT EXCERPT from a codebase (already pre-selected by a reasoning agent).
Answer the user's question using ONLY the information in this excerpt.
At the end, cite the file(s) and line(s) you drew from (e.g. "Source: rtalk/retrieval.py:120-145").
{schema_block}
RELEVANT EXCERPT:
{evidence_text}

USER QUESTION: {query}

ANSWER:"""
