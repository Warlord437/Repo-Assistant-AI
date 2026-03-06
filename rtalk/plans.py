"""Proactive Guided QnA -- multi-step investigation plans.

Each plan is a sequence of steps that selects files by graph centrality,
filename patterns, and symbol names, runs retrieval, and assembles a
narrative report with citations. No LLM required.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from typing import Any

from rtalk.models import EvidenceSnippet, IndexRecord, RecordKind, SymbolKind
from rtalk.retrieval import RetrievalEngine


@dataclass
class PlanStep:
    """A single step in an investigation plan."""

    title: str
    description: str
    search_queries: list[str]
    file_patterns: list[str] = field(default_factory=list)
    symbol_patterns: list[str] = field(default_factory=list)


@dataclass
class StepResult:
    """Result of executing a single plan step."""

    title: str
    description: str
    evidence: list[EvidenceSnippet] = field(default_factory=list)
    narrative: str = ""


@dataclass
class GuideReport:
    """Full report from executing an investigation plan."""

    plan_name: str
    plan_description: str
    steps: list[StepResult] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "plan_name": self.plan_name,
            "plan_description": self.plan_description,
            "steps": [
                {
                    "title": s.title,
                    "description": s.description,
                    "evidence": [
                        {
                            "file_path": ev.file_path,
                            "start_line": ev.start_line,
                            "end_line": ev.end_line,
                            "text": ev.text[:500],
                            "score": round(ev.score, 4),
                            "method": ev.method,
                            "citation": ev.citation(),
                        }
                        for ev in s.evidence
                    ],
                    "narrative": s.narrative,
                }
                for s in self.steps
            ],
        }

    def render_text(self) -> str:
        out: list[str] = []
        out.append("=" * 60)
        out.append(f"  GUIDE: {self.plan_name}")
        out.append("=" * 60)
        out.append(self.plan_description)
        out.append("")

        for i, step in enumerate(self.steps, 1):
            out.append(f"--- Step {i}: {step.title} ---")
            out.append(step.description)
            out.append("")
            if step.evidence:
                for j, ev in enumerate(step.evidence, 1):
                    first_line = ev.text.split("\n")[0].strip()[:100]
                    out.append(f"  [{j}] {ev.citation()}: \"{first_line}\"")
                out.append("")
            if step.narrative:
                out.append(step.narrative)
                out.append("")

        return "\n".join(out)


PLANS: dict[str, tuple[str, list[PlanStep]]] = {
    "explain_indexing_pipeline": (
        "Traces how the indexer scans, chunks, and stores repository data.",
        [
            PlanStep(
                title="Find the indexer entry point",
                description="Locate the main indexing function and CLI entry.",
                search_queries=["build_index", "index_file"],
                file_patterns=["index"],
                symbol_patterns=["build_index"],
            ),
            PlanStep(
                title="Understand file collection",
                description="How does the indexer discover files to process?",
                search_queries=["collect_files", "git_tracked", "walk_files", "ALLOWED_EXTENSIONS"],
                file_patterns=["index"],
            ),
            PlanStep(
                title="Understand chunking",
                description="How are files split into chunks for the index?",
                search_queries=["chunk_lines", "chunk_size", "Chunk"],
                file_patterns=["index", "models"],
            ),
            PlanStep(
                title="Symbol extraction",
                description="How are Python symbols (functions, classes, imports) extracted?",
                search_queries=["extract_python_symbols", "ast.parse", "SymbolRecord"],
                file_patterns=["index"],
                symbol_patterns=["_extract_python_symbols"],
            ),
            PlanStep(
                title="Output format",
                description="What format is the index stored in?",
                search_queries=["to_json_line", "JSONL", "IndexRecord"],
                file_patterns=["models", "index"],
            ),
        ],
    ),
    "explain_search_pipeline": (
        "Traces how queries are processed, from input to ranked evidence.",
        [
            PlanStep(
                title="Query entry point",
                description="Where does a search query enter the system?",
                search_queries=["RetrievalEngine", "search", "api_search", "api_answer"],
                file_patterns=["retrieval", "server"],
            ),
            PlanStep(
                title="TF-IDF scoring",
                description="How does the lexical search rank chunks?",
                search_queries=["TFIDFIndex", "term_freq", "idf", "tokenize"],
                file_patterns=["retrieval"],
                symbol_patterns=["TFIDFIndex"],
            ),
            PlanStep(
                title="Exact search",
                description="How does exact/substring search work?",
                search_queries=["ripgrep_search", "python_exact_search", "exact"],
                file_patterns=["retrieval"],
            ),
            PlanStep(
                title="Boosting and dedup",
                description="How are results boosted by filename/symbol and deduplicated?",
                search_queries=["filename_boost", "symbol_boost", "deduplicate", "overlaps"],
                file_patterns=["retrieval"],
            ),
            PlanStep(
                title="Answer assembly",
                description="How is the final answer built from evidence?",
                search_queries=["answer_question", "build_summary", "build_explanation", "StructuredAnswer"],
                file_patterns=["answer"],
            ),
        ],
    ),
    "where_to_modify_ranking": (
        "Shows exactly where to change how search results are ranked.",
        [
            PlanStep(
                title="Scoring function",
                description="Find the main scoring computation in TF-IDF search.",
                search_queries=["term_freq", "idf", "score +=", "TFIDFIndex.search"],
                file_patterns=["retrieval"],
            ),
            PlanStep(
                title="Boost weights",
                description="Find the boost score functions and their weights.",
                search_queries=["_filename_boost_score", "_symbol_boost_score", "boost"],
                file_patterns=["retrieval"],
            ),
            PlanStep(
                title="Relevance scoring",
                description="Find the relevance computation in the answering engine.",
                search_queries=["_compute_relevance", "keyword_ratio"],
                file_patterns=["answer"],
            ),
            PlanStep(
                title="Deduplication threshold",
                description="Find the dedup threshold that controls result filtering.",
                search_queries=["_deduplicate", "score_threshold", "_overlaps"],
                file_patterns=["retrieval"],
            ),
        ],
    ),
    "how_storage_works": (
        "Explains how data is stored: index JSONL, graph JSON, clone cache.",
        [
            PlanStep(
                title="Index storage",
                description="How is the JSONL index written and read?",
                search_queries=["index.jsonl", "to_json_line", "from_json_line", "build_index"],
                file_patterns=["index", "models"],
            ),
            PlanStep(
                title="Graph storage",
                description="How is the graph JSON produced and structured?",
                search_queries=["graph.json", "build_graph", "graph"],
                file_patterns=["graph"],
            ),
            PlanStep(
                title="Clone cache",
                description="How are cloned repos cached locally?",
                search_queries=["cache_dir", "resolve_repo", "CLONE_BASE", ".rtalk/repos"],
                file_patterns=["clone"],
            ),
        ],
    ),
}


def list_plans() -> list[dict[str, str]]:
    """Return available plan names and descriptions."""
    return [
        {"name": name, "description": desc}
        for name, (desc, _) in PLANS.items()
    ]


def _match_file(file_path: str, patterns: list[str]) -> bool:
    """Check if a file path matches any of the given patterns."""
    base = os.path.basename(file_path).lower().replace(".py", "")
    path_lower = file_path.lower()
    for p in patterns:
        if p in base or p in path_lower:
            return True
    return False


def _run_step(
    step: PlanStep,
    engine: RetrievalEngine,
    records: list[IndexRecord],
) -> StepResult:
    """Execute a single plan step: search, filter, and build narrative."""
    all_evidence: list[EvidenceSnippet] = []
    seen: set[str] = set()

    for query in step.search_queries:
        snippets = engine.search(query, top_k=3)
        for sn in snippets:
            key = f"{sn.file_path}:{sn.start_line}"
            if key not in seen:
                seen.add(key)
                all_evidence.append(sn)

    if step.file_patterns:
        boosted: list[EvidenceSnippet] = []
        other: list[EvidenceSnippet] = []
        for ev in all_evidence:
            if _match_file(ev.file_path, step.file_patterns):
                ev.score *= 1.5
                boosted.append(ev)
            else:
                other.append(ev)
        all_evidence = boosted + other

    if step.symbol_patterns:
        for r in records:
            if r.record_kind == RecordKind.SYMBOL and r.symbol:
                for sp in step.symbol_patterns:
                    if sp.lower() in r.symbol.name.lower():
                        key = f"{r.file_path}:{r.symbol.start_line}"
                        if key not in seen:
                            seen.add(key)
                            all_evidence.append(EvidenceSnippet(
                                file_path=r.file_path,
                                start_line=r.symbol.start_line,
                                end_line=r.symbol.end_line,
                                text=f"[symbol] {r.symbol.kind.value} {r.symbol.name}",
                                score=0.5,
                                method="symbol",
                            ))

    all_evidence.sort(key=lambda e: e.score, reverse=True)
    top_evidence = all_evidence[:5]

    narrative_parts: list[str] = []
    if top_evidence:
        files_mentioned = list(dict.fromkeys(ev.file_path for ev in top_evidence))
        narrative_parts.append(
            f"Relevant code found in: {', '.join(files_mentioned)}."
        )
        for i, ev in enumerate(top_evidence, 1):
            first_line = ev.text.split("\n")[0].strip()[:80]
            narrative_parts.append(f"  [{i}] {ev.citation()}: \"{first_line}\"")
    else:
        narrative_parts.append("No direct evidence found for this step.")

    return StepResult(
        title=step.title,
        description=step.description,
        evidence=top_evidence,
        narrative="\n".join(narrative_parts),
    )


def run_plan(plan_name: str, index_path: str) -> GuideReport:
    """Execute a named investigation plan and return the report."""
    if plan_name not in PLANS:
        available = ", ".join(PLANS.keys())
        raise ValueError(f"Unknown plan: {plan_name!r}. Available: {available}")

    description, steps = PLANS[plan_name]
    engine = RetrievalEngine.from_index(index_path)
    records = engine.records

    step_results: list[StepResult] = []
    for step in steps:
        result = _run_step(step, engine, records)
        step_results.append(result)

    return GuideReport(
        plan_name=plan_name,
        plan_description=description,
        steps=step_results,
    )
