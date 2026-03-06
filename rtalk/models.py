"""Shared data models used across rtalk components."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any
import json


class RecordKind(str, Enum):
    FILE_CHUNK = "file_chunk"
    SYMBOL = "symbol"


class SymbolKind(str, Enum):
    FUNCTION = "function"
    CLASS = "class"
    IMPORT = "import"


@dataclass
class Chunk:
    start_line: int
    end_line: int
    text: str


@dataclass
class SymbolRecord:
    name: str
    kind: SymbolKind
    start_line: int
    end_line: int
    file_path: str


@dataclass
class IndexRecord:
    """Single record written to the JSONL index."""

    record_kind: RecordKind
    file_path: str
    sha256: str
    total_lines: int
    chunk: Chunk | None = None
    symbol: SymbolRecord | None = None

    def to_json_line(self) -> str:
        d: dict[str, Any] = {
            "record_kind": self.record_kind.value,
            "file_path": self.file_path,
            "sha256": self.sha256,
            "total_lines": self.total_lines,
        }
        if self.chunk:
            d["chunk"] = {
                "start_line": self.chunk.start_line,
                "end_line": self.chunk.end_line,
                "text": self.chunk.text,
            }
        if self.symbol:
            d["symbol"] = {
                "name": self.symbol.name,
                "kind": self.symbol.kind.value,
                "file_path": self.symbol.file_path,
                "start_line": self.symbol.start_line,
                "end_line": self.symbol.end_line,
            }
        return json.dumps(d, ensure_ascii=False)

    @classmethod
    def from_json_line(cls, line: str) -> "IndexRecord":
        d = json.loads(line)
        chunk = None
        if "chunk" in d and d["chunk"]:
            chunk = Chunk(**d["chunk"])
        symbol = None
        if "symbol" in d and d["symbol"]:
            sym = d["symbol"]
            symbol = SymbolRecord(
                name=sym["name"],
                kind=SymbolKind(sym["kind"]),
                start_line=sym["start_line"],
                end_line=sym["end_line"],
                file_path=sym["file_path"],
            )
        return cls(
            record_kind=RecordKind(d["record_kind"]),
            file_path=d["file_path"],
            sha256=d["sha256"],
            total_lines=d["total_lines"],
            chunk=chunk,
            symbol=symbol,
        )


@dataclass
class EvidenceSnippet:
    """A single piece of evidence returned by retrieval."""

    file_path: str
    start_line: int
    end_line: int
    text: str
    score: float
    method: str  # "lexical" | "exact"

    def citation(self) -> str:
        return f"{self.file_path}:{self.start_line}-{self.end_line}"


@dataclass
class StructuredAnswer:
    """The final answer produced by the answering engine."""

    query: str
    summary: str
    evidence: list[EvidenceSnippet] = field(default_factory=list)
    explanation: str = ""
    refused: bool = False
    refusal_reason: str = ""
    missing_info: str = ""
    is_ai_generated: bool = False

    def render_text(self) -> str:
        lines: list[str] = []
        lines.append(f"Query: {self.query}")
        lines.append("")

        if self.refused:
            lines.append("REFUSED: Insufficient evidence to answer this question.")
            if self.refusal_reason:
                lines.append(f"Reason: {self.refusal_reason}")
            if self.missing_info:
                lines.append(f"Suggested files to check: {self.missing_info}")
            return "\n".join(lines)

        lines.append(f"Summary: {self.summary}")
        lines.append("")
        lines.append("Evidence:")
        for i, ev in enumerate(self.evidence, 1):
            lines.append(f"  [{i}] {ev.citation()} (score={ev.score:.3f}, {ev.method})")
            preview = ev.text.split("\n")[0][:120]
            lines.append(f"      {preview}")
        lines.append("")
        if self.explanation:
            lines.append(f"Explanation: {self.explanation}")
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Explain Report models
# ---------------------------------------------------------------------------


@dataclass
class Entrypoint:
    """A detected entry point in the repository."""

    file_path: str
    line: int
    kind: str  # "main_block", "fastapi_app", "flask_app", "click_cli", "typer_cli", "script"
    description: str

    def citation(self) -> str:
        return f"{self.file_path}:{self.line}"


@dataclass
class DirectoryInfo:
    """Summary of a top-level directory."""

    path: str
    file_count: int
    extensions: list[str]
    description: str


@dataclass
class ImportEdge:
    """A single import relationship: source_file imports target_module."""

    source: str
    target: str


@dataclass
class GraphSummary:
    """Simple module-level import graph."""

    edges: list[ImportEdge] = field(default_factory=list)
    central_modules: list[tuple[str, int]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "edges": [{"source": e.source, "target": e.target} for e in self.edges],
            "central_modules": [
                {"module": m, "in_degree": d} for m, d in self.central_modules
            ],
        }


@dataclass
class ExplainSection:
    """One section of the explain report, with optional citations and persona."""

    title: str
    body: str
    citations: list[str] = field(default_factory=list)
    citations_top: list[str] = field(default_factory=list)  # max 5 for display
    citations_all: list[str] = field(default_factory=list)  # full list
    persona: str = "all"  # "all" | "pm" | "engineer" | "ux" | "researcher"
    ai_summary: str = ""  # AI-generated understanding when api key provided


@dataclass
class AtAGlance:
    """Quick summary stats for the repo."""

    one_liner: str
    file_count: int
    entry_point_count: int
    central_modules_count: int
    user_facing_count: int  # web/API/CLI entry points


@dataclass
class FolderEdge:
    """Folder-level import: source_folder imports from target_folder."""

    source: str
    target: str
    count: int


@dataclass
class FolderGraph:
    """Aggregated folder-level import graph."""

    edges: list[FolderEdge] = field(default_factory=list)
    folder_stats: dict[str, dict[str, Any]] = field(default_factory=dict)


@dataclass
class FolderLayer:
    """A layer in the architecture (leaf, core, entry)."""

    name: str
    folders: list[str]


@dataclass
class DomainInfo:
    """KT-style domain summary: what a folder does, how it connects."""

    path: str
    file_count: int
    summary: str
    imports_from: list[str]
    imported_by: list[str]
    landmarks: list[str]
    citations: list[str] = field(default_factory=list)
    is_ai_summary: bool = False


@dataclass
class ExplainReport:
    """Full structured repo overview."""

    what: ExplainSection
    how_to_run: ExplainSection
    directories: list[DirectoryInfo] = field(default_factory=list)
    entrypoints: list[Entrypoint] = field(default_factory=list)
    architecture: GraphSummary = field(default_factory=GraphSummary)
    start_here: list[ExplainSection] = field(default_factory=list)
    tech_stack: list[str] = field(default_factory=list)
    key_capabilities: list[str] = field(default_factory=list)
    at_a_glance: AtAGlance | None = None
    domains: list[DomainInfo] = field(default_factory=list)
    architecture_layers: list[FolderLayer] = field(default_factory=list)
    architecture_narrative: str = ""
    directories_ai_summary: str = ""
    entrypoints_ai_summary: str = ""
    architecture_ai_summary: str = ""
    ux_design_overview: str = ""
    ux_design_ai_summary: str = ""
    role_explanations: dict[str, str] = field(default_factory=dict)  # pm, engineer, ux, researcher

    def to_dict(self) -> dict[str, Any]:
        def _section_dict(s: ExplainSection) -> dict[str, Any]:
            cites = s.citations_all if s.citations_all else s.citations
            top = s.citations_top if s.citations_top else cites[:5]
            return {
                "title": s.title,
                "body": s.body,
                "citations": cites,
                "citations_top": top[:5],
                "citations_all": cites,
                "persona": s.persona,
                "ai_summary": s.ai_summary,
            }

        return {
            "what": _section_dict(self.what),
            "how_to_run": _section_dict(self.how_to_run),
            "directories": [
                {
                    "path": d.path,
                    "file_count": d.file_count,
                    "extensions": d.extensions,
                    "description": d.description,
                }
                for d in self.directories
            ],
            "entrypoints": [
                {
                    "file_path": e.file_path,
                    "line": e.line,
                    "kind": e.kind,
                    "description": e.description,
                    "citation": e.citation(),
                }
                for e in self.entrypoints
            ],
            "architecture": self.architecture.to_dict(),
            "start_here": [
                {
                    "title": s.title,
                    "body": s.body,
                    "citations": s.citations_all if s.citations_all else s.citations,
                    "citations_top": (s.citations_top if s.citations_top else s.citations)[:5],
                    "citations_all": s.citations_all if s.citations_all else s.citations,
                    "persona": s.persona,
                    "ai_summary": s.ai_summary,
                }
                for s in self.start_here
            ],
            "tech_stack": self.tech_stack,
            "key_capabilities": self.key_capabilities,
            "at_a_glance": (
                {
                    "one_liner": self.at_a_glance.one_liner,
                    "file_count": self.at_a_glance.file_count,
                    "entry_point_count": self.at_a_glance.entry_point_count,
                    "central_modules_count": self.at_a_glance.central_modules_count,
                    "user_facing_count": self.at_a_glance.user_facing_count,
                }
                if self.at_a_glance
                else None
            ),
            "domains": [
                {
                    "path": d.path,
                    "file_count": d.file_count,
                    "summary": d.summary,
                    "imports_from": d.imports_from,
                    "imported_by": d.imported_by,
                    "landmarks": d.landmarks,
                    "citations": d.citations,
                    "is_ai_summary": d.is_ai_summary,
                }
                for d in self.domains
            ],
            "architecture_layers": [
                {"name": l.name, "folders": l.folders} for l in self.architecture_layers
            ],
            "architecture_narrative": self.architecture_narrative,
            "directories_ai_summary": self.directories_ai_summary,
            "entrypoints_ai_summary": self.entrypoints_ai_summary,
            "architecture_ai_summary": self.architecture_ai_summary,
            "ux_design_overview": self.ux_design_overview,
            "ux_design_ai_summary": self.ux_design_ai_summary,
            "role_explanations": self.role_explanations,
        }

    def render_text(self) -> str:
        out: list[str] = []
        out.append("=" * 60)
        out.append("  REPO OVERVIEW")
        out.append("=" * 60)
        out.append("")

        out.append(f"## {self.what.title}")
        out.append(self.what.body)
        if self.what.citations:
            out.append(f"  Citations: {', '.join(self.what.citations)}")
        out.append("")

        out.append(f"## {self.how_to_run.title}")
        out.append(self.how_to_run.body)
        if self.how_to_run.citations:
            out.append(f"  Citations: {', '.join(self.how_to_run.citations)}")
        out.append("")

        if self.at_a_glance:
            out.append("## At a glance")
            out.append(f"  {self.at_a_glance.one_liner}")
            out.append(
                f"  {self.at_a_glance.file_count} files, {self.at_a_glance.entry_point_count} entry points, "
                f"{self.at_a_glance.user_facing_count} user-facing"
            )
            out.append("")

        if self.tech_stack:
            out.append("## Tech Stack")
            out.append("  " + ", ".join(self.tech_stack[:12]))
            out.append("")

        if self.key_capabilities:
            out.append("## Key Capabilities")
            for c in self.key_capabilities[:6]:
                out.append(f"  - {c}")
            out.append("")

        if self.directories:
            out.append("## Key Directories")
            for d in self.directories:
                exts = ", ".join(d.extensions[:5])
                out.append(f"  {d.path}/ ({d.file_count} files: {exts}) - {d.description}")
            out.append("")

        if self.entrypoints:
            out.append("## Entry Points")
            for e in self.entrypoints:
                out.append(f"  [{e.kind}] {e.citation()} - {e.description}")
            out.append("")

        if self.architecture_layers:
            out.append("## Architecture layers")
            for layer in self.architecture_layers:
                out.append(f"  {layer.name}: {', '.join(layer.folders)}")
            out.append("")

        if self.architecture_narrative:
            out.append("## How it connects")
            out.append(f"  {self.architecture_narrative}")
            out.append("")

        if self.domains:
            out.append("## Domains (KT overview)")
            for d in self.domains[:15]:
                ai_tag = " [AI]" if d.is_ai_summary else ""
                out.append(f"  {d.path}/ ({d.file_count} files){ai_tag}")
                out.append(f"    {d.summary}")
                if d.imports_from:
                    out.append(f"    Imports from: {', '.join(d.imports_from)}")
                if d.imported_by:
                    out.append(f"    Imported by: {', '.join(d.imported_by)}")
                if d.landmarks:
                    out.append(f"    Start here: {', '.join(d.landmarks[:3])}")
            out.append("")

        if self.architecture.central_modules:
            out.append("## Top central modules")
            for mod, degree in self.architecture.central_modules:
                out.append(f"  {mod} (imported by {degree} modules)")
            out.append("")

        if self.start_here:
            out.append("## Start Here (suggested reading order)")
            for i, s in enumerate(self.start_here, 1):
                cites = f" [{', '.join(s.citations)}]" if s.citations else ""
                out.append(f"  {i}. {s.title}{cites}")
                out.append(f"     {s.body}")
            out.append("")

        return "\n".join(out)
