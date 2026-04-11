"""Explain Repo -- generates a structured overview of a repository from its index.

Operates entirely on the JSONL index records. No LLM required.
Produces sections: What, How to Run, Directories, Entrypoints, Architecture, Start Here.
"""

from __future__ import annotations

import os
import re
from collections import Counter, defaultdict
from typing import Any

from rtalk.text_clean import normalize_lines, strip_html, strip_markdown_images
from rtalk.toc import build_toc
from rtalk.models import (
    AtAGlance,
    DirectoryInfo,
    DomainInfo,
    Entrypoint,
    ExplainReport,
    ExplainSection,
    FolderEdge,
    FolderGraph,
    FolderLayer,
    GraphSummary,
    ImportEdge,
    IndexRecord,
    RecordKind,
    SymbolKind,
)


def _load_records(index_path: str) -> list[IndexRecord]:
    records: list[IndexRecord] = []
    with open(index_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                records.append(IndexRecord.from_json_line(line))
    return records


def _unique_files(records: list[IndexRecord]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for r in records:
        if r.file_path not in seen:
            seen.add(r.file_path)
            result.append(r.file_path)
    return result


def _extract_tech_stack(repo_path: str | None) -> list[str]:
    """Extract tech stack from pyproject.toml and requirements.txt. No paid APIs."""
    stack: list[str] = []
    if not repo_path or not os.path.isdir(repo_path):
        return stack

    # pyproject.toml
    for candidate in ("pyproject.toml", "pyproject.yaml"):
        path = os.path.join(repo_path, candidate)
        if os.path.isfile(path) and candidate.endswith(".toml"):
            import tomllib
            try:
                with open(path, "rb") as f:
                    data = tomllib.load(f)
                deps = data.get("project", {}).get("dependencies", [])
                for d in deps[:12]:
                    if isinstance(d, str) and "==" in d:
                        pkg = d.split("==")[0].strip()
                        if not pkg.startswith(("#", ";")):
                            stack.append(pkg)
            except Exception:
                pass
            break

    # requirements.txt
    for name in ("requirements.txt", "requirements-base.txt"):
        path = os.path.join(repo_path, name)
        if os.path.isfile(path):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    for line in f:
                        line = line.strip().split("#")[0]
                        if "==" in line:
                            pkg = line.split("==")[0].strip()
                            if pkg and pkg not in stack:
                                stack.append(pkg)
            except Exception:
                pass
            break

    return stack[:15]


def _extract_key_capabilities(records: list[IndexRecord]) -> list[str]:
    """Extract key features/capabilities from README Features section."""
    caps: list[str] = []
    for r in records:
        if r.record_kind != RecordKind.FILE_CHUNK or not r.chunk:
            continue
        base = os.path.basename(r.file_path).lower()
        if not base.startswith("readme"):
            continue
        text = r.chunk.text
        lines = text.split("\n")
        in_features = False
        for line in lines:
            h = line.strip().lstrip("#").strip().lower()
            if "feature" in h or "capabilit" in h or "key" in h and "point" not in h:
                in_features = True
                continue
            if in_features:
                if line.strip().startswith("#"):
                    break
                bullet = line.strip().lstrip("-*").strip()
                if bullet and len(bullet) > 10 and len(bullet) < 120:
                    cleaned = strip_html(strip_markdown_images(bullet))
                    if cleaned and not cleaned.startswith("```"):
                        caps.append(cleaned[:100])
        if caps:
            break
    return caps[:8]


def _find_readme_chunk(records: list[IndexRecord]) -> tuple[str, str, str] | None:
    """Find the first chunk of a README file. Returns (file_path, text, citation) or None."""
    for r in records:
        if r.record_kind == RecordKind.FILE_CHUNK and r.chunk:
            base = os.path.basename(r.file_path).lower()
            if base.startswith("readme"):
                citation = f"{r.file_path}:{r.chunk.start_line}-{r.chunk.end_line}"
                return r.file_path, r.chunk.text, citation
    return None


def _get_all_readme_chunks(records: list[IndexRecord]) -> list[tuple[str, str, int, int]]:
    """Get all README chunks. Returns list of (file_path, text, start_line, end_line)."""
    out: list[tuple[str, str, int, int]] = []
    for r in records:
        if r.record_kind == RecordKind.FILE_CHUNK and r.chunk:
            base = os.path.basename(r.file_path).lower()
            if base.startswith("readme"):
                out.append((r.file_path, r.chunk.text, r.chunk.start_line, r.chunk.end_line))
    return sorted(out, key=lambda x: (x[0], x[2]))


def _extract_what(records: list[IndexRecord], all_files: list[str]) -> ExplainSection:
    """Build the 'What this repo is' section from README. Sanitizes HTML and images."""
    readme = _find_readme_chunk(records)
    citations_all: list[str] = []

    if readme:
        file_path, text, citation = readme
        citations_all.append(citation)

        cleaned = strip_markdown_images(text)
        cleaned = strip_html(cleaned)
        cleaned = normalize_lines(cleaned)

        lines = [ln.strip() for ln in cleaned.split("\n") if ln.strip()]
        title_line = ""
        desc_lines: list[str] = []
        for line in lines:
            if line.startswith("#"):
                heading = line.lstrip("# ").strip()
                if not title_line:
                    title_line = heading
                continue
            if title_line and line and not line.startswith(("!", "[", "|", "-" * 3)):
                desc_lines.append(line)
                if len(desc_lines) >= 3:
                    break

        body = title_line
        if desc_lines:
            para = " ".join(desc_lines)
            if len(para) > 400:
                para = para[:397] + "..."
            body = (title_line + ": " + para) if title_line else para

        if not body or body.isspace():
            first_heading = next((ln.lstrip("# ").strip() for ln in lines if ln.startswith("#")), "")
            first_para = next((ln for ln in lines if ln and not ln.startswith("#")), "")
            body = (first_heading + ": " + first_para) if first_heading else first_para or "Unable to determine from README."

        return ExplainSection(
            title="What this repo is",
            body=body or "Unable to determine from README.",
            citations=citations_all,
            citations_top=citations_all[:5],
            citations_all=citations_all,
        )

    py_count = sum(1 for f in all_files if f.endswith(".py"))
    return ExplainSection(
        title="What this repo is",
        body=f"A repository with {len(all_files)} indexed files ({py_count} Python).",
        citations=[],
        citations_top=[],
        citations_all=[],
    )


_SECTION_HEADERS = (
    "quick start", "quickstart", "install", "usage", "run", "getting started",
    "installation", "setup", "running",
)

_PREFERRED_PREFIXES = ("pip ", "python ", "npm ", "make ", "docker ", "uvicorn ", "poetry ")


def _extract_how_to_run(records: list[IndexRecord]) -> ExplainSection:
    """Extract 'How to run' from README code fences in Quick Start, Install, etc."""
    commands: list[str] = []
    citations_all: list[str] = []
    seen_commands: set[str] = set()

    readme_chunks = _get_all_readme_chunks(records)
    in_relevant_section = False
    section_headers = re.compile(r"^#+\s*(.+)", re.I)

    for file_path, text, start_line, end_line in readme_chunks:
        lines = text.split("\n")
        in_fence = False
        fence_lines: list[str] = []
        fence_start = 0

        for i, line in enumerate(lines):
            lineno = start_line + i
            stripped = line.strip()

            heading_match = section_headers.match(stripped)
            if heading_match:
                if in_fence and fence_lines:
                    for cmd in fence_lines:
                        c = cmd.strip().lstrip("$").strip()
                        if c and c not in seen_commands:
                            seen_commands.add(c)
                            commands.append(c)
                            cite = f"{file_path}:{fence_start}-{lineno}"
                            if cite not in citations_all:
                                citations_all.append(cite)
                in_fence = False
                fence_lines = []
                h = heading_match.group(1).lower()
                in_relevant_section = any(sh in h for sh in _SECTION_HEADERS)

            if in_relevant_section:
                if stripped.startswith("```"):
                    if in_fence and fence_lines:
                        for cmd in fence_lines:
                            c = cmd.strip().lstrip("$").strip()
                            if c and c not in seen_commands:
                                seen_commands.add(c)
                                commands.append(c)
                                cite = f"{file_path}:{fence_start}-{lineno}"
                                if cite not in citations_all:
                                    citations_all.append(cite)
                    in_fence = not in_fence
                    fence_lines = []
                    fence_start = lineno
                elif in_fence:
                    if stripped and not stripped.startswith("#"):
                        fence_lines.append(stripped)

        if in_fence and fence_lines:
            for cmd in fence_lines:
                c = cmd.strip().lstrip("$").strip()
                if c and c not in seen_commands:
                    seen_commands.add(c)
                    commands.append(c)
                    cite = f"{file_path}:{fence_start}-{end_line}"
                    if cite not in citations_all:
                        citations_all.append(cite)

    if not commands:
        for r in records:
            if r.record_kind != RecordKind.FILE_CHUNK or not r.chunk:
                continue
            base = os.path.basename(r.file_path).lower()
            if base.startswith("readme"):
                for line in r.chunk.text.split("\n"):
                    stripped = line.strip().lstrip("$").strip()
                    if any(stripped.startswith(p) for p in _PREFERRED_PREFIXES):
                        if stripped not in seen_commands:
                            seen_commands.add(stripped)
                            commands.append(stripped)
                            cite = f"{r.file_path}:{r.chunk.start_line}-{r.chunk.end_line}"
                            if cite not in citations_all:
                                citations_all.append(cite)

    preferred = [c for c in commands if any(c.startswith(p) for p in _PREFERRED_PREFIXES)]
    other = [c for c in commands if c not in preferred]
    ordered = (preferred + other)[:8]

    if ordered:
        body = "\n".join(f"  $ {c}" for c in ordered)
        return ExplainSection(
            title="How to run",
            body=body,
            citations=citations_all,
            citations_top=citations_all[:5],
            citations_all=citations_all,
            persona="all",
        )

    return ExplainSection(
        title="How to run",
        body="No explicit run commands found in README or config files.",
        citations=[],
        citations_top=[],
        citations_all=[],
        persona="all",
    )


_DIR_DESCRIPTIONS: dict[str, str] = {
    "tests": "Test suites. Engineers: run tests here. PMs: coverage and quality.",
    "test": "Test suites. Engineers: run tests here.",
    "docs": "Documentation. PMs/UX: specs, guides. Researchers: architecture notes.",
    "doc": "Documentation.",
    "web": "Frontend or web UI. UX: user-facing flows. Engineers: client code.",
    "frontend": "Frontend code. UX: UI components and flows.",
    "src": "Source code. Engineers: core logic. PMs: feature boundaries.",
    "core": "Core framework or library. Engineers: central modules.",
    "rtalk": "Main package. Engineers: entry points and modules.",
    "app": "Application code. Engineers: entry points.",
    "api": "API layer. Engineers: endpoints. UX: integration points.",
    "cli": "Command-line interface. Engineers: CLI entry points.",
    "tools": "Utilities and tooling. Engineers: scripts and helpers.",
    "scripts": "Build and utility scripts. Engineers: automation.",
    "config": "Configuration. Engineers: env and settings.",
    ".github": "CI/CD and GitHub config. Engineers: workflows.",
}


def _compute_directories(all_files: list[str]) -> list[DirectoryInfo]:
    """Compute top-level directory summaries with persona-relevant descriptions."""
    dir_files: defaultdict[str, list[str]] = defaultdict(list)
    for f in all_files:
        parts = f.split("/")
        if len(parts) > 1:
            dir_files[parts[0]].append(f)
        else:
            dir_files["."].append(f)

    dirs: list[DirectoryInfo] = []
    for d, files in sorted(dir_files.items(), key=lambda x: -len(x[1])):
        exts = sorted(set(os.path.splitext(f)[1] for f in files if os.path.splitext(f)[1]))
        py_count = sum(1 for f in files if f.endswith(".py"))
        test_count = sum(1 for f in files if "test" in f.lower() or f.endswith("_test.py"))
        html_js = sum(1 for f in files if f.endswith((".html", ".js", ".ts", ".tsx", ".css")))

        if d == ".":
            desc = "Root-level files (config, README)"
        elif d.lower() in ("web", "frontend", "ui", "static", "templates"):
            desc = f"UX: User-facing flows, UI components ({html_js} web assets)" if html_js else _DIR_DESCRIPTIONS.get(d.lower(), f"Contains {', '.join(exts[:3])} files")
        elif d.lower() in _DIR_DESCRIPTIONS:
            desc = _DIR_DESCRIPTIONS[d.lower()]
            if d.lower() in ("tests", "test") and test_count:
                desc += f" ({test_count} test files)"
            elif d.lower() in ("web", "frontend") and html_js:
                desc += f" ({html_js} web assets)"
        elif py_count > 0:
            desc = f"Python package ({py_count} .py files)"
        else:
            desc = f"Contains {', '.join(exts[:3])} files"
        dirs.append(DirectoryInfo(path=d, file_count=len(files), extensions=exts, description=desc))

    return dirs[:15]


def _is_code_line(line: str) -> bool:
    """Return True if the line is actual code (not a string/comment containing patterns)."""
    stripped = line.strip()
    if stripped.startswith("#"):
        return False
    if stripped.startswith(("'", '"', "f'", 'f"', "r'", 'r"')):
        return False
    quote_count = stripped.count('"') + stripped.count("'")
    if quote_count >= 4:
        return False
    return True


def detect_entrypoints(records: list[IndexRecord]) -> list[Entrypoint]:
    """Detect entry points: __main__ blocks, FastAPI/Flask apps, Click/Typer CLIs."""
    entrypoints: list[Entrypoint] = []
    seen: set[str] = set()

    for r in records:
        if r.record_kind != RecordKind.FILE_CHUNK or not r.chunk:
            continue
        if not r.file_path.endswith(".py"):
            continue

        text = r.chunk.text
        lines = text.split("\n")
        base_line = r.chunk.start_line

        for i, line in enumerate(lines):
            lineno = base_line + i
            stripped = line.strip()
            key = f"{r.file_path}:{lineno}"

            if key in seen:
                continue

            if stripped.startswith("if __name__") and "__main__" in stripped:
                main_key = f"{r.file_path}:main_block"
                if main_key not in seen:
                    entrypoints.append(Entrypoint(
                        file_path=r.file_path,
                        line=lineno,
                        kind="main_block",
                        description=f"Main entry block in {r.file_path}",
                    ))
                    seen.add(key)
                    seen.add(main_key)

            if _is_code_line(line) and re.match(r"\w+\s*=\s*FastAPI\(", stripped):
                entrypoints.append(Entrypoint(
                    file_path=r.file_path,
                    line=lineno,
                    kind="fastapi_app",
                    description=f"FastAPI application in {r.file_path}",
                ))
                seen.add(key)

            if _is_code_line(line) and re.match(r"\w+\s*=\s*Flask\(", stripped):
                entrypoints.append(Entrypoint(
                    file_path=r.file_path,
                    line=lineno,
                    kind="flask_app",
                    description=f"Flask application in {r.file_path}",
                ))
                seen.add(key)

            if _is_code_line(line) and stripped.startswith("@click."):
                if "@click.command" in stripped or "@click.group" in stripped:
                    entrypoints.append(Entrypoint(
                        file_path=r.file_path,
                        line=lineno,
                        kind="click_cli",
                        description=f"Click CLI in {r.file_path}",
                    ))
                    seen.add(key)

            if _is_code_line(line) and re.match(r"\w+\s*=\s*typer\.Typer\(", stripped):
                entrypoints.append(Entrypoint(
                    file_path=r.file_path,
                    line=lineno,
                    kind="typer_cli",
                    description=f"Typer CLI in {r.file_path}",
                ))
                seen.add(key)

    return entrypoints


def _file_to_folder(file_path: str) -> str:
    """auth/service.py -> auth. Root files -> '.'"""
    parts = file_path.split("/")
    return parts[0] if len(parts) > 1 else "."


def _build_module_to_folder(all_files: list[str]) -> dict[str, str]:
    """Map module names to top-level folder. framework -> core if core/framework/ exists."""
    mod_to_folder: dict[str, str] = {}
    for f in all_files:
        parts = f.split("/")
        for i, part in enumerate(parts):
            if part.endswith(".py"):
                part = part[:-3]
            if part and part != "__init__":
                folder = parts[0] if parts else "."
                mod_to_folder[part] = folder
    return mod_to_folder


_EXTERNAL_TOP: set[str] = {
    "os", "sys", "re", "json", "math", "hashlib", "pathlib", "ast", "subprocess",
    "shutil", "collections", "typing", "enum", "dataclasses", "abc", "functools",
    "itertools", "io", "unittest", "argparse", "fastapi", "uvicorn", "pydantic",
    "flask", "click", "typer", "pytest", "setuptools", "httpx", "asyncio",
}


def build_folder_graph(
    records: list[IndexRecord],
    all_files: list[str],
) -> FolderGraph:
    """Build folder-level import graph from symbol records. Internal imports only."""
    mod_to_folder = _build_module_to_folder(all_files)
    edge_counts: dict[tuple[str, str], int] = {}
    folder_in: dict[str, set[str]] = defaultdict(set)
    folder_out: dict[str, set[str]] = defaultdict(set)
    folder_files: dict[str, set[str]] = defaultdict(set)

    for f in all_files:
        folder = _file_to_folder(f)
        if folder:
            folder_files[folder].add(f)

    for r in records:
        if r.record_kind != RecordKind.SYMBOL or not r.symbol:
            continue
        if r.symbol.kind != SymbolKind.IMPORT:
            continue

        source_file = r.file_path
        target_module = r.symbol.name.split(".")[0]

        if target_module in _EXTERNAL_TOP:
            continue
        if not target_module or target_module.startswith("_"):
            continue

        target_folder = mod_to_folder.get(target_module)
        if not target_folder or target_folder == ".":
            continue

        source_folder = _file_to_folder(source_file)
        if not source_folder or source_folder == ".":
            continue
        if source_folder == target_folder:
            continue

        key = (source_folder, target_folder)
        edge_counts[key] = edge_counts.get(key, 0) + 1
        folder_out[source_folder].add(target_folder)
        folder_in[target_folder].add(source_folder)

    edges = [
        FolderEdge(source=s, target=t, count=c)
        for (s, t), c in sorted(edge_counts.items(), key=lambda x: -x[1])
    ]

    folder_stats: dict[str, dict[str, Any]] = {}
    all_folders = set(folder_files.keys()) | set(folder_in.keys()) | set(folder_out.keys())
    for folder in all_folders:
        folder_stats[folder] = {
            "file_count": len(folder_files.get(folder, set())),
            "in_degree": len(folder_in.get(folder, set())),
            "out_degree": len(folder_out.get(folder, set())),
        }

    return FolderGraph(edges=edges, folder_stats=folder_stats)


def compute_folder_layers(folder_graph: FolderGraph) -> list[FolderLayer]:
    """Compute architecture layers: leaf (no internal deps), core, entry (many dependents)."""
    folders_by_in: dict[str, int] = {
        f: s["in_degree"] for f, s in folder_graph.folder_stats.items()
    }
    folders_by_out: dict[str, int] = {
        f: s["out_degree"] for f, s in folder_graph.folder_stats.items()
    }

    out_edges: dict[str, set[str]] = defaultdict(set)
    for e in folder_graph.edges:
        out_edges[e.source].add(e.target)

    leaf: list[str] = []
    core: list[str] = []
    entry: list[str] = []

    for folder in folder_graph.folder_stats:
        out = len(out_edges.get(folder, set()))
        inc = folders_by_in.get(folder, 0)

        if out == 0 and inc > 0:
            leaf.append(folder)
        elif inc >= 3 or (inc >= 2 and out <= 2):
            entry.append(folder)
        else:
            core.append(folder)

    layers: list[FolderLayer] = []
    if leaf:
        layers.append(FolderLayer(name="leaf", folders=sorted(leaf)[:12]))
    if core:
        layers.append(FolderLayer(name="core", folders=sorted(core)[:12]))
    if entry:
        layers.append(FolderLayer(name="entry", folders=sorted(entry)[:12]))

    return layers


def _extract_folder_summary(
    records: list[IndexRecord],
    folder: str,
    all_files: list[str],
    central_modules: list[tuple[str, int]],
) -> tuple[str, list[str]]:
    """Extract summary from folder README or __init__.py. Returns (summary, citations)."""
    citations: list[str] = []
    folder_prefix = folder + "/"

    for r in records:
        if r.record_kind != RecordKind.FILE_CHUNK or not r.chunk:
            continue
        if not r.file_path.startswith(folder_prefix) and r.file_path != folder:
            continue

        base = os.path.basename(r.file_path).lower()
        if base.startswith("readme"):
            text = r.chunk.text
            cleaned = strip_markdown_images(text)
            cleaned = strip_html(cleaned)
            lines = [ln.strip() for ln in cleaned.split("\n") if ln.strip()]
            for line in lines[:20]:
                if line.startswith("#") or line.startswith(("!", "[", "|", "-", "```")):
                    continue
                if "=" in line and "(" in line and ")" in line and not line.startswith(("The", "This", "A ", "An ")):
                    continue
                if len(line) > 25 and len(line) < 200 and not line.startswith(("  ", "\t")):
                    summary = line[:150] + ("..." if len(line) > 150 else "")
                    cite = f"{r.file_path}:{r.chunk.start_line}-{r.chunk.end_line}"
                    citations.append(cite)
                    return summary, citations[:3]

        if base == "__init__.py":
            text = r.chunk.text
            match = re.search(r'"""(.*?)"""', text, re.DOTALL)
            if match:
                doc = match.group(1).strip().split("\n")[0][:120]
                if len(doc) > 15:
                    cite = f"{r.file_path}:{r.chunk.start_line}-{r.chunk.end_line}"
                    citations.append(cite)
                    return doc + ("..." if len(doc) >= 120 else ""), citations[:3]

    py_files = [f for f in all_files if f.startswith(folder_prefix) and f.endswith(".py")]
    key_names = list({os.path.splitext(os.path.basename(f))[0] for f in py_files[:8]})
    key_names = [k for k in key_names if k != "__init__"][:4]
    summary = f"Contains {len(py_files)} Python files. Key modules: {', '.join(key_names)}."
    return summary, []


def _build_domains(
    all_files: list[str],
    folder_graph: FolderGraph,
    layers: list[FolderLayer],
    records: list[IndexRecord],
    graph: GraphSummary,
) -> list[DomainInfo]:
    """Build domain summaries for each folder."""
    folder_set = set(folder_graph.folder_stats.keys())
    in_edges: dict[str, set[str]] = defaultdict(set)
    out_edges: dict[str, set[str]] = defaultdict(set)
    for e in folder_graph.edges:
        out_edges[e.source].add(e.target)
        in_edges[e.target].add(e.source)

    dir_files: dict[str, list[str]] = defaultdict(list)
    for f in all_files:
        folder = _file_to_folder(f)
        if folder:
            dir_files[folder].append(f)

    domains: list[DomainInfo] = []
    for folder in sorted(folder_set, key=lambda x: -len(dir_files.get(x, [])))[:20]:
        files = dir_files.get(folder, [])
        if not files:
            continue

        summary, citations = _extract_folder_summary(
            records, folder, all_files, graph.central_modules
        )
        imports_from = sorted(out_edges.get(folder, set()))
        imported_by = sorted(in_edges.get(folder, set()))

        landmarks: list[str] = []
        readme = next((f for f in files if os.path.basename(f).lower().startswith("readme")), None)
        if readme:
            landmarks.append(readme)
        init_py = folder + "/__init__.py"
        if init_py in files:
            landmarks.append(init_py)
        for mod, _ in graph.central_modules:
            for f in files:
                if mod in f or f.endswith(f"/{mod}.py"):
                    if f not in landmarks:
                        landmarks.append(f)
                    break
        landmarks = landmarks[:3]

        domains.append(DomainInfo(
            path=folder,
            file_count=len(files),
            summary=summary,
            imports_from=imports_from,
            imported_by=imported_by,
            landmarks=landmarks,
            citations=citations,
        ))

    return domains


_rate_limit_warnings: list[str] = []

def _call_groq(api_key: str, prompt: str, max_tokens: int = 150) -> str:
    """Call Groq API with automatic retry on rate limits. Returns empty string on failure."""
    try:
        from rtalk.groq_client import groq_chat, RateLimitError, APIError

        return groq_chat(api_key, [{"role": "user", "content": prompt}], max_tokens=max_tokens)
    except RateLimitError as e:
        import sys
        _rate_limit_warnings.append(str(e))
        print(f"⚠️  API Rate Limit (after retries): {str(e)}", file=sys.stderr)
        return ""
    except ValueError as e:
        import sys
        print(f"❌ API Key Error: {str(e)}", file=sys.stderr)
        return ""
    except APIError as e:
        import sys
        print(f"❌ API Error: {str(e)}", file=sys.stderr)
        return ""
    except Exception as e:
        import sys
        print(f"❌ Unexpected error calling Groq API: {type(e).__name__}: {str(e)}", file=sys.stderr)
        return ""


def _toc_to_compact_json(nodes: list, max_depth: int = 3, max_chars: int = 3500) -> str:
    """Serialize TOC to compact JSON for AI context. Limits depth and total size."""
    import json

    def _serialize(node: Any, depth: int) -> dict:
        if depth >= max_depth:
            return {"title": getattr(node, "title", str(node))[:60], "kind": getattr(node, "kind", "")}
        d: dict[str, Any] = {
            "title": getattr(node, "title", "")[:80],
            "kind": getattr(node, "kind", ""),
            "file_path": getattr(node, "file_path", "")[:100] or None,
        }
        children = getattr(node, "children", [])
        if children:
            d["children"] = [_serialize(c, depth + 1) for c in children[:12]]
        return d

    out = [_serialize(n, 0) for n in nodes[:15]]
    s = json.dumps(out, indent=0)[:max_chars]
    if len(json.dumps(out)) > max_chars:
        s = s[: max_chars - 30] + "..."
    return s


def _generate_detailed_explanation(
    report: ExplainReport,
    records: list[IndexRecord],
    api_key: str,
) -> str:
    """Generate one comprehensive AI explanation covering the entire codebase."""
    toc = build_toc(records)
    toc_json = _toc_to_compact_json(toc, max_chars=4000)

    ctx: list[str] = []
    ctx.append(f"## What this repo is\n{report.what.body[:400]}")
    ctx.append(f"## How to run\n{report.how_to_run.body[:300]}")
    if report.directories:
        dirs = "\n".join(f"- {d.path}/ ({d.file_count} files) — {d.description}" for d in report.directories[:10])
        ctx.append(f"## Directories\n{dirs}")
    if report.entrypoints:
        eps = "\n".join(f"- [{e.kind}] {e.file_path}:{e.line} — {e.description}" for e in report.entrypoints[:8])
        ctx.append(f"## Entry points\n{eps}")
    if report.domains:
        doms = "\n".join(
            f"- {d.path}/ ({d.file_count} files): {d.summary[:120]}"
            + (f" | imports from: {', '.join(d.imports_from)}" if d.imports_from else "")
            + (f" | imported by: {', '.join(d.imported_by)}" if d.imported_by else "")
            for d in report.domains[:10]
        )
        ctx.append(f"## Domains / folders\n{doms}")
    if report.architecture_narrative:
        ctx.append(f"## Architecture\n{report.architecture_narrative[:500]}")
    if report.architecture_layers:
        layers = "\n".join(f"- {l.name}: {', '.join(l.folders)}" for l in report.architecture_layers)
        ctx.append(f"## Architecture layers\n{layers}")
    if report.tech_stack:
        ctx.append(f"## Tech stack\n{', '.join(report.tech_stack[:12])}")
    if report.key_capabilities:
        caps = "\n".join(f"- {c}" for c in report.key_capabilities[:8])
        ctx.append(f"## Key capabilities\n{caps}")
    if report.start_here:
        recs = "\n".join(f"{i+1}. {s.title} — {s.body}" for i, s in enumerate(report.start_here[:8]))
        ctx.append(f"## Suggested reading order\n{recs}")

    report_context = "\n\n".join(ctx)

    prompt = (
        "You are a senior engineer writing a detailed knowledge-transfer document for a codebase.\n\n"
        "Below is structured data extracted from the repository index. Use ALL of it to write a "
        "comprehensive explanation that anyone (engineer, PM, researcher, designer) can reference.\n\n"
        "Cover these topics in order:\n"
        "1. **What this project does** — purpose, key features, value proposition\n"
        "2. **How to run it** — setup, install, launch commands\n"
        "3. **Architecture overview** — how the codebase is organized, what each major folder/module does\n"
        "4. **Key components & how they connect** — dependency flow, which modules are foundational vs consumers\n"
        "5. **Entry points** — where does execution start, what APIs/CLIs exist\n"
        "6. **Where to start reading** — recommended file order for someone new\n\n"
        "Rules:\n"
        "- Be SPECIFIC to this repo. Reference actual file names, folders, and modules.\n"
        "- Use markdown formatting (headers, bullets, bold) for readability.\n"
        "- Be thorough but concise — aim for a complete reference, not a summary.\n"
        "- Use ONLY the information provided below. Do not invent features.\n\n"
        f"TOC (codebase structure):\n{toc_json}\n\n"
        f"Extracted data:\n{report_context}"
    )

    return _call_groq(api_key, prompt, max_tokens=1024)


def _enhance_domains_with_ai(
    domains: list[DomainInfo],
    records: list[IndexRecord],
    api_key: str,
) -> list[DomainInfo]:
    """Optionally enhance domain summaries using Groq API. Falls back to original on failure."""
    enhanced: list[DomainInfo] = []
    for d in domains[:5]:
        chunks: list[str] = []
        for r in records:
            if r.record_kind != RecordKind.FILE_CHUNK or not r.chunk:
                continue
            if not r.file_path.startswith(d.path + "/") and r.file_path != d.path:
                continue
            if r.file_path.endswith(".py") or "readme" in r.file_path.lower():
                text = r.chunk.text[:1200]
                chunks.append(f"[{r.file_path}]\n{text}")
        chunks = chunks[:10]

        if not chunks:
            enhanced.append(d)
            continue

        meta_parts: list[str] = []
        if d.imports_from:
            meta_parts.append(f"Imports from: {', '.join(d.imports_from)}")
        if d.imported_by:
            meta_parts.append(f"Imported by: {', '.join(d.imported_by)}")
        meta_parts.append(f"File count: {d.file_count}")

        prompt = (
            f"Given this codebase evidence for folder '{d.path}/', "
            f"write ONE sentence (max 100 chars) describing what this folder does. "
            f"Be specific. Only use information from the evidence.\n\n"
            f"Context: {'; '.join(meta_parts)}\n\n"
            f"Evidence:\n" + "\n\n".join(chunks)
        )

        summary = _call_groq(api_key, prompt, max_tokens=150)
        if summary and len(summary) < 150:
            enhanced.append(DomainInfo(
                path=d.path,
                file_count=d.file_count,
                summary=summary,
                imports_from=d.imports_from,
                imported_by=d.imported_by,
                landmarks=d.landmarks,
                citations=d.citations,
                is_ai_summary=True,
            ))
        else:
            enhanced.append(d)
    return enhanced + domains[5:]


def _enhance_with_ai(
    report: ExplainReport,
    records: list[IndexRecord],
    api_key: str,
) -> ExplainReport:
    """Generate one detailed AI explanation for the entire report."""
    explanation = _generate_detailed_explanation(report, records, api_key)
    if explanation:
        report.ai_explanation = explanation
    return report


def _build_architecture_narrative(
    layers: list[FolderLayer],
    folder_graph: FolderGraph,
) -> str:
    """Generate human-readable dependency narrative with clear hierarchy and flow."""
    if not layers:
        return ""

    parts: list[str] = []
    folder_to_layer: dict[str, str] = {}
    for layer in layers:
        for f in layer.folders:
            folder_to_layer[f] = layer.name

    # Entry layers = foundation (many depend on them) — explain their role
    entry_folders = []
    leaf_folders = []
    core_folders = []
    for layer in layers:
        if layer.name == "entry" and layer.folders:
            entry_folders = layer.folders[:6]
        elif layer.name == "leaf" and layer.folders:
            leaf_folders = layer.folders[:6]
        elif layer.name == "core" and layer.folders:
            core_folders = layer.folders[:6]

    if entry_folders:
        parts.append(
            f"Foundation modules (imported by many): {', '.join(entry_folders)}. "
            "These are the core packages other code depends on."
        )
    extra_core = [f for f in core_folders if f not in entry_folders]
    if extra_core:
        parts.append(f"Core modules (internal orchestration): {', '.join(extra_core)}.")
    if leaf_folders:
        parts.append(
            f"Leaf modules (self-contained, no internal deps): {', '.join(leaf_folders)}."
        )

    # Key flows: show direction of dependencies
    edges = folder_graph.edges[:15]
    if edges:
        deps: list[str] = []
        for e in edges[:8]:
            deps.append(f"{e.source} → {e.target}")
        parts.append("Key flows: " + "; ".join(deps[:6]) + ".")

    return " ".join(parts)[:500]


def build_import_graph(records: list[IndexRecord]) -> GraphSummary:
    """Build a simple module import graph from symbol records."""
    edges: list[ImportEdge] = []
    in_degree: Counter[str] = Counter()
    seen: set[tuple[str, str]] = set()

    for r in records:
        if r.record_kind != RecordKind.SYMBOL or not r.symbol:
            continue
        if r.symbol.kind != SymbolKind.IMPORT:
            continue

        source = r.file_path
        target_module = r.symbol.name.split(".")[0]

        pair = (source, target_module)
        if pair not in seen:
            seen.add(pair)
            edges.append(ImportEdge(source=source, target=target_module))
            in_degree[target_module] += 1

    central = in_degree.most_common(5)
    return GraphSummary(edges=edges, central_modules=central)


def _build_start_here(
    all_files: list[str],
    entrypoints: list[Entrypoint],
    graph: GraphSummary,
) -> list[ExplainSection]:
    """Suggest a reading order of 5-10 files, tagged by persona."""
    suggestions: list[ExplainSection] = []
    added: set[str] = set()

    def _sec(title: str, body: str, cites: list[str], persona: str = "all") -> ExplainSection:
        return ExplainSection(
            title=title,
            body=body,
            citations=cites,
            citations_top=cites[:5],
            citations_all=cites,
            persona=persona,
        )

    readme_files = [f for f in all_files if os.path.basename(f).lower().startswith("readme")]
    for f in readme_files[:1]:
        cites = [f"{f}:1-1"]
        suggestions.append(_sec(f, "Start here for project overview and setup instructions.", cites, "all"))
        added.add(f)

    config_files = [
        f for f in all_files
        if os.path.basename(f) in ("pyproject.toml", "setup.py", "setup.cfg", "package.json")
    ]
    for f in config_files[:1]:
        if f not in added:
            cites = [f"{f}:1-1"]
            suggestions.append(_sec(f, "Project configuration, dependencies, and build metadata.", cites, "engineer"))
            added.add(f)

    for ep in entrypoints[:3]:
        if ep.file_path not in added:
            cites = [ep.citation()]
            persona = "ux" if ep.kind in ("fastapi_app", "flask_app") else "engineer"
            suggestions.append(_sec(ep.file_path, f"Entry point ({ep.kind}): {ep.description}", cites, persona))
            added.add(ep.file_path)

    for mod, degree in graph.central_modules:
        candidates = [f for f in all_files if _module_matches_file(mod, f)]
        for f in candidates[:1]:
            if f not in added and len(suggestions) < 10:
                cites = [f"{f}:1-1"]
                suggestions.append(_sec(f, f"Central module imported by {degree} other files.", cites, "engineer"))
                added.add(f)

    init_files = [f for f in all_files if f.endswith("__init__.py") and f.count("/") == 1]
    for f in init_files[:1]:
        if f not in added and len(suggestions) < 10:
            cites = [f"{f}:1-1"]
            suggestions.append(_sec(f, "Package init. Shows top-level exports and version.", cites, "engineer"))
            added.add(f)

    return suggestions[:10]


def _module_matches_file(module_name: str, file_path: str) -> bool:
    """Check if a module name plausibly corresponds to a file path."""
    base = os.path.basename(file_path).replace(".py", "")
    return base == module_name


def _build_ux_design_overview(
    entrypoints: list[Entrypoint],
    directories: list[DirectoryInfo],
    at_a_glance: AtAGlance | None,
) -> str:
    """Build a non-code UX overview for designers: user flows, screens, interaction areas."""
    parts: list[str] = []
    ux_eps = [e for e in entrypoints if e.kind in ("fastapi_app", "flask_app")]
    ux_dirs = [d for d in directories if d.path.lower() in ("web", "frontend", "app", "ui", "static", "templates")]
    if ux_eps:
        kinds = ", ".join(sorted(set(e.kind.replace("_app", " app") for e in ux_eps)))
        parts.append(f"User-facing: {kinds} ({len(ux_eps)} entry point{'s' if len(ux_eps) != 1 else ''})")
    if ux_dirs:
        dir_desc = ", ".join(f"{d.path}/" for d in ux_dirs[:4])
        parts.append(f"UI areas: {dir_desc}")
    if at_a_glance and at_a_glance.user_facing_count and not ux_eps:
        parts.append(f"{at_a_glance.user_facing_count} user-facing entry point(s) (CLI or API)")
    if not parts:
        return ""
    return ". ".join(parts)


def _compute_at_a_glance(
    what: ExplainSection,
    all_files: list[str],
    entrypoints: list[Entrypoint],
    graph: GraphSummary,
) -> AtAGlance:
    """Build at-a-glance summary for PMs and quick orientation."""
    first = what.body.split(".")[0].strip()
    one_liner = (first[:117] + "...") if len(first) > 120 else first
    user_facing = sum(
        1 for e in entrypoints
        if e.kind in ("fastapi_app", "flask_app", "click_cli", "typer_cli")
    )
    return AtAGlance(
        one_liner=one_liner,
        file_count=len(all_files),
        entry_point_count=len(entrypoints),
        central_modules_count=len(graph.central_modules),
        user_facing_count=user_facing,
    )


def summarize_repo(
    index_path: str,
    repo_path: str | None = None,
    use_ai_summary: bool = False,
    ai_api_key: str | None = None,
) -> ExplainReport:
    """Generate a full structured repo overview from a JSONL index."""
    records = _load_records(index_path)
    all_files = _unique_files(records)

    what = _extract_what(records, all_files)
    how_to_run = _extract_how_to_run(records)
    directories = _compute_directories(all_files)
    entrypoints = detect_entrypoints(records)
    graph = build_import_graph(records)
    start_here = _build_start_here(all_files, entrypoints, graph)

    tech_stack = _extract_tech_stack(repo_path) if repo_path else []
    key_capabilities = _extract_key_capabilities(records)
    at_a_glance = _compute_at_a_glance(what, all_files, entrypoints, graph)

    folder_graph = build_folder_graph(records, all_files)
    layers = compute_folder_layers(folder_graph)
    domains = _build_domains(all_files, folder_graph, layers, records, graph)
    architecture_narrative = _build_architecture_narrative(layers, folder_graph)

    ux_design_overview = _build_ux_design_overview(entrypoints, directories, at_a_glance)

    report = ExplainReport(
        what=what,
        how_to_run=how_to_run,
        directories=directories,
        entrypoints=entrypoints,
        architecture=graph,
        start_here=start_here,
        tech_stack=tech_stack,
        key_capabilities=key_capabilities,
        at_a_glance=at_a_glance,
        domains=domains,
        architecture_layers=layers,
        architecture_narrative=architecture_narrative,
        ux_design_overview=ux_design_overview,
    )

    if use_ai_summary and ai_api_key:
        _rate_limit_warnings.clear()
        report = _enhance_with_ai(report, records, ai_api_key)
        if _rate_limit_warnings:
            is_daily = any("tokens per day" in w.lower() or "tpd" in w.lower() for w in _rate_limit_warnings)
            if is_daily:
                report.warnings.append(
                    "Daily token limit reached on your Groq API key. "
                    "AI explanation will be available when the daily quota resets (usually within the hour). "
                    "Try again later, or upgrade your Groq plan."
                )
            else:
                report.warnings.append(
                    "Rate limited by Groq API. Wait ~30 seconds and re-run, or upgrade your Groq plan."
                )
            _rate_limit_warnings.clear()

    return report
