"""Answering engine -- produces citation-grounded answers from evidence snippets.

MVP uses deterministic templates and heuristics. No LLM required.
An optional LLMAdapter plugin interface is provided for future use.
"""

from __future__ import annotations

import re
from abc import ABC, abstractmethod
from collections import Counter

from rtalk.models import EvidenceSnippet, StructuredAnswer


class LLMAdapter(ABC):
    """Plugin interface for optional LLM integration.

    Implement this to swap in a local model (e.g., llama.cpp, Ollama) later.
    The MVP does not require any adapter -- the deterministic engine is used.
    """

    @abstractmethod
    def generate(self, prompt: str, max_tokens: int = 512) -> str:
        ...

    @abstractmethod
    def is_available(self) -> bool:
        ...


class LocalModelStub(LLMAdapter):
    """Stub adapter that always reports unavailable. Placeholder for future work."""

    def generate(self, prompt: str, max_tokens: int = 512) -> str:
        return "[LLM not configured]"

    def is_available(self) -> bool:
        return False


class GroqAdapter(LLMAdapter):
    """Groq API adapter for answer synthesis."""

    def __init__(self, api_key: str | None) -> None:
        self.api_key = (api_key or "").strip()

    def generate(self, prompt: str, max_tokens: int = 512) -> str:
        if not self.api_key:
            return ""
        try:
            from rtalk.groq_client import groq_chat, RateLimitError, APIError
            return groq_chat(
                self.api_key,
                [{"role": "user", "content": prompt}],
                max_tokens=max_tokens,
            )
        except RateLimitError as e:
            import sys
            print(f"⚠️  Rate limit reached: {str(e)}", file=sys.stderr)
            return ""
        except (ValueError, APIError) as e:
            import sys
            print(f"❌ API Error: {str(e)}", file=sys.stderr)
            return ""
        except Exception as e:
            import sys
            print(f"❌ Unexpected error: {type(e).__name__}: {str(e)}", file=sys.stderr)
            return ""

    def is_available(self) -> bool:
        return bool(self.api_key)


def _extract_keywords(query: str) -> list[str]:
    """Pull meaningful words from the query for matching."""
    stop = {
        "the", "a", "an", "is", "are", "was", "were", "what", "where", "when",
        "how", "why", "who", "which", "do", "does", "did", "can", "could",
        "will", "would", "should", "in", "on", "at", "to", "for", "of",
        "and", "or", "not", "it", "its", "this", "that", "with", "from",
        "be", "been", "being", "have", "has", "had", "i", "me", "my",
    }
    words = re.findall(r"[a-z0-9_]+", query.lower())
    return [w for w in words if w not in stop and len(w) > 1]


def _compute_relevance(snippet: EvidenceSnippet, keywords: list[str]) -> float:
    """Score how well a snippet's text matches the query keywords."""
    text_lower = snippet.text.lower()
    hits = sum(1 for kw in keywords if kw in text_lower)
    if not keywords:
        return snippet.score
    keyword_ratio = hits / len(keywords)
    return snippet.score * 0.6 + keyword_ratio * 0.4


def _build_summary(query: str, evidence: list[EvidenceSnippet]) -> str:
    """Build a deterministic summary from evidence."""
    if not evidence:
        return ""

    files = list(dict.fromkeys(ev.file_path for ev in evidence))
    keywords = _extract_keywords(query)

    if len(files) == 1:
        location = f"in `{files[0]}`"
    elif len(files) <= 3:
        location = "across " + ", ".join(f"`{f}`" for f in files)
    else:
        location = f"across {len(files)} files"

    keyword_str = ", ".join(keywords[:4]) if keywords else "the query terms"

    return (
        f"Found {len(evidence)} relevant snippet(s) {location} "
        f"matching {keyword_str}."
    )


def _build_explanation(
    query: str, evidence: list[EvidenceSnippet], keywords: list[str]
) -> str:
    """Build a grounded explanation referencing evidence by citation index."""
    if not evidence:
        return ""

    parts: list[str] = []
    for i, ev in enumerate(evidence, 1):
        matching = [kw for kw in keywords if kw in ev.text.lower()]
        first_line = ev.text.split("\n")[0].strip()[:100]
        if matching:
            parts.append(
                f"[{i}] {ev.citation()} contains {', '.join(matching)}: "
                f'"{first_line}"'
            )
        else:
            parts.append(
                f"[{i}] {ev.citation()} is contextually related: "
                f'"{first_line}"'
            )

    return "Based on the indexed code:\n" + "\n".join(parts)


def answer_question(
    query: str,
    evidence: list[EvidenceSnippet],
    llm: LLMAdapter | None = None,
    schema_understanding: str = "",
) -> StructuredAnswer:
    """Produce a structured answer from query + evidence snippets.

    If evidence is empty or insufficient, returns a refusal with guidance.
    If an LLM adapter is provided and available, it can enhance the explanation.
    schema_understanding: graph-derived context (folders, central files, imports).
    """
    if not evidence:
        if llm and llm.is_available() and schema_understanding:
            prompt = (
                f"The user asked: {query}\n\n"
                f"No code snippets were found by search, but here is the codebase structure:\n{schema_understanding}\n\n"
                f"Based on this structure, give a brief answer (2-4 sentences) about where to look or what the question likely refers to. "
                f"Mention specific files or folders from the schema if relevant."
            )
            llm_text = llm.generate(prompt, max_tokens=300)
            if llm_text:
                return StructuredAnswer(
                    query=query,
                    summary=llm_text.split("\n")[0][:200],
                    evidence=[],
                    explanation=llm_text,
                    is_ai_generated=True,
                )
        return StructuredAnswer(
            query=query,
            summary="",
            refused=True,
            refusal_reason="No evidence snippets found for this query.",
            missing_info=(
                "Index the repo first (click Index Repo or use Map to auto-build). "
                "For search/retrieval questions, try terms like 'retrieval', 'tfidf', 'search'."
            ),
        )

    keywords = _extract_keywords(query)

    scored = [(ev, _compute_relevance(ev, keywords)) for ev in evidence]
    scored.sort(key=lambda x: x[1], reverse=True)

    best_score = scored[0][1] if scored else 0.0
    if best_score < 0.01:
        return StructuredAnswer(
            query=query,
            summary="",
            refused=True,
            refusal_reason="Evidence snippets were found but none are relevant enough to the query.",
            missing_info=(
                "Files checked: "
                + ", ".join(dict.fromkeys(ev.file_path for ev, _ in scored))
                + ". Try rephrasing your question or searching for specific identifiers."
            ),
        )

    ranked_evidence = [ev for ev, _ in scored]

    is_ai = False
    if llm and llm.is_available():
        from rtalk.generation import synthesize_answer_prompt
        prompt = synthesize_answer_prompt(
            query, ranked_evidence, schema_understanding
        )
        llm_text = llm.generate(prompt, max_tokens=400)
        if llm_text:
            summary = llm_text.split("\n")[0][:300]
            explanation = llm_text
            is_ai = True
        else:
            summary = _build_summary(query, ranked_evidence)
            explanation = _build_explanation(query, ranked_evidence, keywords)
    else:
        summary = _build_summary(query, ranked_evidence)
        explanation = _build_explanation(query, ranked_evidence, keywords)

    return StructuredAnswer(
        query=query,
        summary=summary,
        evidence=ranked_evidence,
        explanation=explanation,
        is_ai_generated=is_ai,
    )
