"""Impact Analysis -- given a file or folder, compute dependents, affected entrypoints, risk score.
Supports single-file mode and top high-impact files mode."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any

from rtalk.models import IndexRecord, RecordKind, SymbolKind


@dataclass
class TopImpactEntry:
    """A high-impact file with its risk score and metadata."""

    file: str
    risk_score: float
    dependents_count: int
    entrypoints_count: int
    test_count: int
    folder: str = ""


@dataclass
class TopImpactReport:
    """Report of top high-impact files, optionally filtered by folder."""

    entries: list[TopImpactEntry] = field(default_factory=list)
    folder_filter: str | None = None
    ai_summary: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "entries": [
                {
                    "file": e.file,
                    "risk_score": round(e.risk_score, 2),
                    "dependents_count": e.dependents_count,
                    "entrypoints_count": e.entrypoints_count,
                    "test_count": e.test_count,
                    "folder": e.folder,
                }
                for e in self.entries
            ],
            "folder_filter": self.folder_filter,
            "ai_summary": self.ai_summary,
        }


@dataclass
class ImpactReport:
    """Result of impact analysis for a single file."""

    target_file: str
    dependents: list[str] = field(default_factory=list)
    affected_entrypoints: list[str] = field(default_factory=list)
    related_tests: list[str] = field(default_factory=list)
    risk_score: float = 0.0
    risk_breakdown: dict[str, float] = field(default_factory=dict)
    file_lines: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "target_file": self.target_file,
            "dependents": self.dependents,
            "affected_entrypoints": self.affected_entrypoints,
            "related_tests": self.related_tests,
            "risk_score": round(self.risk_score, 2),
            "risk_breakdown": {k: round(v, 2) for k, v in self.risk_breakdown.items()},
            "file_lines": self.file_lines,
        }

    def render_text(self) -> str:
        out: list[str] = []
        out.append("=" * 60)
        out.append(f"  IMPACT ANALYSIS: {self.target_file}")
        out.append("=" * 60)
        out.append("")
        out.append(f"Risk Score: {self.risk_score:.1f}/100")
        for k, v in self.risk_breakdown.items():
            out.append(f"  {k}: {v:.1f}")
        out.append("")
        out.append(f"File size: {self.file_lines} lines")
        out.append("")
        out.append(f"Direct dependents ({len(self.dependents)}):")
        for d in self.dependents:
            out.append(f"  - {d}")
        out.append("")
        out.append(f"Affected entrypoints ({len(self.affected_entrypoints)}):")
        for e in self.affected_entrypoints:
            out.append(f"  - {e}")
        out.append("")
        out.append(f"Related tests ({len(self.related_tests)}):")
        for t in self.related_tests:
            out.append(f"  - {t}")
        out.append("")
        return "\n".join(out)


def _load_graph(graph_path: str) -> dict[str, Any]:
    with open(graph_path, "r", encoding="utf-8") as f:
        return json.load(f)


def _load_records(index_path: str) -> list[IndexRecord]:
    records: list[IndexRecord] = []
    with open(index_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                records.append(IndexRecord.from_json_line(line))
    return records


def _find_all_dependents(
    target: str, reverse_deps: dict[str, list[str]]
) -> list[str]:
    """BFS to find all transitive dependents."""
    visited: set[str] = set()
    queue = [target]
    while queue:
        current = queue.pop(0)
        for dep in reverse_deps.get(current, []):
            if dep not in visited:
                visited.add(dep)
                queue.append(dep)
    return sorted(visited)


def _find_affected_entrypoints(
    dependents: list[str],
    records: list[IndexRecord],
) -> list[str]:
    """Find entrypoint files among dependents."""
    entrypoint_files: set[str] = set()
    all_files = set(dependents)

    for r in records:
        if r.record_kind != RecordKind.FILE_CHUNK or not r.chunk:
            continue
        if r.file_path not in all_files:
            continue
        text = r.chunk.text
        if 'if __name__' in text and '__main__' in text:
            entrypoint_files.add(r.file_path)
        if 'FastAPI(' in text or 'Flask(' in text:
            entrypoint_files.add(r.file_path)

    for r in records:
        if r.record_kind != RecordKind.FILE_CHUNK or not r.chunk:
            continue
        if r.file_path in all_files and r.file_path.endswith("__main__.py"):
            entrypoint_files.add(r.file_path)

    return sorted(entrypoint_files)


def _find_related_tests(
    target: str,
    records: list[IndexRecord],
) -> list[str]:
    """Find test files that import the target module or mention its symbols."""
    target_module = os.path.basename(target).replace(".py", "")
    target_symbols: set[str] = set()

    for r in records:
        if r.record_kind == RecordKind.SYMBOL and r.symbol:
            if r.file_path == target and r.symbol.kind != SymbolKind.IMPORT:
                target_symbols.add(r.symbol.name)

    test_files: set[str] = set()
    for r in records:
        if r.record_kind != RecordKind.FILE_CHUNK or not r.chunk:
            continue
        if "test" not in r.file_path.lower():
            continue
        text_lower = r.chunk.text.lower()
        if target_module.lower() in text_lower:
            test_files.add(r.file_path)
            continue
        for sym in target_symbols:
            if sym.lower() in text_lower:
                test_files.add(r.file_path)
                break

    return sorted(test_files)


def _git_churn(target: str, repo_path: str | None) -> int:
    """Count git commits touching this file. Returns 0 if git unavailable."""
    if not repo_path:
        return 0
    git = shutil.which("git")
    if not git:
        return 0
    try:
        result = subprocess.run(
            [git, "log", "--oneline", "--follow", "--", target],
            cwd=repo_path,
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode == 0:
            return len([l for l in result.stdout.strip().split("\n") if l])
    except (subprocess.TimeoutExpired, OSError):
        pass
    return 0


def _compute_risk(
    dependents_count: int,
    entrypoints_count: int,
    test_count: int,
    churn: int,
    file_lines: int,
) -> tuple[float, dict[str, float]]:
    """Weighted risk score out of 100."""
    dep_score = min(dependents_count * 10, 30)
    entry_score = min(entrypoints_count * 15, 25)
    test_penalty = max(0, 15 - test_count * 5)
    churn_score = min(churn * 2, 15)
    size_score = min(file_lines / 50, 15)

    total = dep_score + entry_score + test_penalty + churn_score + size_score
    total = min(total, 100)

    breakdown = {
        "dependents_impact": dep_score,
        "entrypoint_impact": entry_score,
        "test_coverage_gap": test_penalty,
        "churn_frequency": churn_score,
        "file_complexity": round(size_score, 1),
    }
    return total, breakdown


def analyze_impact(
    target_file: str,
    index_path: str,
    graph_path: str = ".rtalk/graph.json",
    repo_path: str | None = None,
) -> ImpactReport:
    """Run impact analysis for a given file."""
    graph = _load_graph(graph_path)
    records = _load_records(index_path)

    reverse_deps = graph.get("reverse_deps", {})
    dependents = _find_all_dependents(target_file, reverse_deps)
    affected_eps = _find_affected_entrypoints(dependents + [target_file], records)
    related_tests = _find_related_tests(target_file, records)

    file_lines = 0
    for node in graph.get("nodes", []):
        if node["id"] == target_file:
            file_lines = node.get("total_lines", 0)
            break

    churn = _git_churn(target_file, repo_path)

    risk, breakdown = _compute_risk(
        len(dependents), len(affected_eps), len(related_tests), churn, file_lines
    )

    return ImpactReport(
        target_file=target_file,
        dependents=dependents,
        affected_entrypoints=affected_eps,
        related_tests=related_tests,
        risk_score=risk,
        risk_breakdown=breakdown,
        file_lines=file_lines,
    )


def _get_folder(file_path: str, depth: int = 2) -> str:
    """Get folder prefix for a file path."""
    parts = file_path.split("/")
    if len(parts) <= 1:
        return "."
    return "/".join(parts[:-1][:depth])


def get_top_impact_files(
    index_path: str,
    graph_path: str,
    repo_path: str | None = None,
    top_n: int = 15,
    folder_filter: str | None = None,
    ai_api_key: str | None = None,
) -> TopImpactReport:
    """Return top N high-impact files, optionally filtered by folder.

    Impact = centrality + dependents + entrypoints - tests.
    """
    graph = _load_graph(graph_path)
    records = _load_records(index_path)
    reverse_deps = graph.get("reverse_deps", {})
    centrality_list = graph.get("centrality", [])
    centrality_map = {c["file"]: c.get("score", 0) for c in centrality_list}
    clusters = graph.get("clusters", {})
    folder_to_files: dict[str, list[str]] = defaultdict(list)
    for folder, files in clusters.items():
        for fp in files:
            folder_to_files[folder].append(fp)

    all_files: set[str] = set()
    if folder_filter:
        for folder, files in clusters.items():
            if folder == folder_filter or folder.startswith(folder_filter + "/"):
                all_files.update(files)
        if not all_files:
            all_files = set(clusters.get(folder_filter, []))
    else:
        all_files = {f for files in clusters.values() for f in files}
        if not all_files:
            all_files = {n["id"] for n in graph.get("nodes", [])}

    node_folder: dict[str, str] = {}
    for n in graph.get("nodes", []):
        node_folder[n["id"]] = n.get("folder", _get_folder(n["id"]))

    scored: list[tuple[str, float, int, int, int]] = []
    for fp in all_files:
        dependents = _find_all_dependents(fp, reverse_deps)
        affected_eps = _find_affected_entrypoints(dependents + [fp], records)
        related_tests = _find_related_tests(fp, records)
        risk, _ = _compute_risk(
            len(dependents), len(affected_eps), len(related_tests), 0, 0
        )
        cent = centrality_map.get(fp, 0)
        impact_score = risk * 0.6 + (cent * 50 if cent else 0)
        folder = node_folder.get(fp, _get_folder(fp))
        if folder_filter and folder_filter not in folder and folder != folder_filter:
            continue
        scored.append((fp, impact_score, len(dependents), len(affected_eps), len(related_tests)))

    scored.sort(key=lambda x: x[1], reverse=True)
    entries: list[TopImpactEntry] = []
    for fp, imp, dep_c, ep_c, test_c in scored[:top_n]:
        risk, _ = _compute_risk(dep_c, ep_c, test_c, 0, 0)
        entries.append(
            TopImpactEntry(
                file=fp,
                risk_score=risk,
                dependents_count=dep_c,
                entrypoints_count=ep_c,
                test_count=test_c,
                folder=node_folder.get(fp, _get_folder(fp)),
            )
        )

    ai_summary = ""
    if ai_api_key and ai_api_key.strip() and entries:
        try:
            from rtalk.groq_client import groq_chat

            summary_lines = [
                f"- {e.file}: risk {e.risk_score:.0f}, {e.dependents_count} dependents, {e.entrypoints_count} entrypoints"
                for e in entries[:8]
            ]
            prompt = (
                f"Top high-impact files in this codebase:\n" + "\n".join(summary_lines) + "\n\n"
                f"Write 2-3 sentences summarizing which areas are most critical to change carefully."
            )
            ai_summary = groq_chat(ai_api_key, [{"role": "user", "content": prompt}], max_tokens=150)
        except Exception:
            pass

    return TopImpactReport(
        entries=entries,
        folder_filter=folder_filter,
        ai_summary=ai_summary,
    )
