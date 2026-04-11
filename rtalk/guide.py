"""Guided Investigation -- query-driven RAG + AI. Uses same vectorless retrieval as Ask."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from rtalk.models import EvidenceSnippet
from rtalk.retrieval import RetrievalEngine


@dataclass
class GuideStep:
    """A single step in the generated guide."""

    title: str
    description: str
    evidence: list[EvidenceSnippet] = field(default_factory=list)
    narrative: str = ""


@dataclass
class GuideReport:
    """Report from running a guided investigation."""

    query: str
    steps: list[GuideStep] = field(default_factory=list)
    summary: str = ""
    is_ai_generated: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "query": self.query,
            "summary": self.summary,
            "is_ai_generated": self.is_ai_generated,
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
        out.append(f"  GUIDE: {self.query}")
        out.append("=" * 60)
        if self.summary:
            out.append(self.summary)
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


def run_guide(
    query: str,
    index_path: str,
    repo_path: str | None = None,
    graph_path: str | None = None,
    ai_api_key: str | None = None,
    schema_understanding: str = "",
    top_k: int = 8,
) -> GuideReport:
    """Run a guided investigation using vectorless RAG + optional AI.

    Uses RetrievalEngine (same as Ask) to retrieve evidence, then either
    AI or a simple template to synthesize a step-by-step guide.
    """
    engine = RetrievalEngine.from_index(index_path, repo_path=repo_path)
    evidence = engine.search(
        query,
        top_k=top_k,
        ai_api_key=ai_api_key,
        schema_understanding=schema_understanding,
    )

    if not evidence:
        return GuideReport(
            query=query,
            summary="No relevant code found for this topic. Try rephrasing or indexing the repo.",
            steps=[],
        )

    if ai_api_key and ai_api_key.strip():
        from rtalk.answer import GroqAdapter
        from rtalk.groq_client import groq_chat, RateLimitError, APIError

        llm = GroqAdapter(ai_api_key)
        if llm.is_available():
            evidence_text = "\n".join(
                f"[{i}] {ev.citation()}:\n{ev.text[:350]}" for i, ev in enumerate(evidence, 1)
            )
            schema_block = f"\n\nCodebase structure:\n{schema_understanding}\n\n" if schema_understanding else ""
            prompt = (
                f"The user wants a guided investigation on: {query}\n"
                f"{schema_block}"
                f"Relevant code evidence:\n{evidence_text}\n\n"
                f"Write a step-by-step guide (3-6 steps) to understand or work with this topic. "
                f"For each step: (1) a short title, (2) what to look at or do, (3) cite evidence by [1], [2], etc. "
                f"Format as:\n"
                f"STEP 1: Title\nDescription...\n"
                f"STEP 2: Title\nDescription...\n"
                f"etc."
            )
            try:
                ai_text = groq_chat(ai_api_key, [{"role": "user", "content": prompt}], max_tokens=800)
                if ai_text:
                    steps = _parse_ai_steps(ai_text, evidence)
                    summary = ai_text.split("\n")[0][:200] if ai_text else ""
                    return GuideReport(query=query, steps=steps, summary=summary, is_ai_generated=True)
            except RateLimitError as e:
                import sys
                print(f"⚠️  Rate limit reached: {str(e)}", file=sys.stderr)
                print("   Continuing with lexical search results only.", file=sys.stderr)
            except (ValueError, APIError) as e:
                import sys
                print(f"❌ API Error: {str(e)}", file=sys.stderr)
                print("   Continuing with lexical search results only.", file=sys.stderr)
            except Exception as e:
                import sys
                print(f"❌ Unexpected error: {type(e).__name__}: {str(e)}", file=sys.stderr)
                print("   Continuing with lexical search results only.", file=sys.stderr)

    # Fallback: single step with evidence
    files = list(dict.fromkeys(ev.file_path for ev in evidence))
    narrative = f"Relevant code in: {', '.join(files[:5])}.\n"
    for i, ev in enumerate(evidence[:5], 1):
        narrative += f"  [{i}] {ev.citation()}: \"{ev.text.split(chr(10))[0][:80]}...\"\n"
    return GuideReport(
        query=query,
        steps=[
            GuideStep(
                title="Relevant code",
                description=query,
                evidence=evidence[:5],
                narrative=narrative.strip(),
            )
        ],
    )


def _parse_ai_steps(ai_text: str, evidence: list[EvidenceSnippet]) -> list[GuideStep]:
    """Parse AI output into GuideStep list."""
    steps: list[GuideStep] = []
    current_title = ""
    current_desc: list[str] = []
    lines = ai_text.split("\n")

    def flush():
        nonlocal current_title, current_desc
        if current_title:
            desc = "\n".join(current_desc).strip()
            steps.append(
                GuideStep(
                    title=current_title,
                    description=desc,
                    evidence=evidence[:3],
                    narrative=desc,
                )
            )
        current_title = ""
        current_desc = []

    for line in lines:
        line_stripped = line.strip()
        if line_stripped.upper().startswith("STEP ") and ":" in line_stripped:
            flush()
            parts = line_stripped.split(":", 1)
            current_title = parts[1].strip() if len(parts) > 1 else line_stripped
            current_desc = []
        elif current_title:
            current_desc.append(line)

    flush()
    if not steps:
        steps.append(
            GuideStep(
                title="Overview",
                description=ai_text[:500],
                evidence=evidence[:3],
                narrative=ai_text[:500],
            )
        )
    return steps
