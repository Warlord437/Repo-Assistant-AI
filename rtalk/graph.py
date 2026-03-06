"""Repo Graph -- builds internal dependency graph, call graph, and centrality metrics.

Operates on the JSONL index. Filters to internal-only edges (ignores stdlib/third-party).
Outputs a graph JSON with nodes, edges, metrics, and clusters.
"""

from __future__ import annotations

import ast
import json
import os
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from rtalk.models import IndexRecord, RecordKind, SymbolKind

_STDLIB_TOP: set[str] = set(sys.stdlib_module_names) if hasattr(sys, "stdlib_module_names") else {
    "os", "sys", "re", "json", "math", "hashlib", "pathlib", "ast", "subprocess",
    "shutil", "collections", "typing", "enum", "dataclasses", "abc", "functools",
    "itertools", "io", "copy", "logging", "unittest", "argparse", "difflib",
    "textwrap", "string", "datetime", "time", "socket", "http", "urllib",
    "email", "html", "xml", "csv", "sqlite3", "pdb", "traceback", "warnings",
    "contextlib", "tempfile", "glob", "fnmatch", "stat", "struct", "codecs",
    "pickle", "shelve", "marshal", "base64", "binascii", "zlib", "gzip",
    "bz2", "lzma", "zipfile", "tarfile", "configparser", "secrets",
    "threading", "multiprocessing", "concurrent", "queue", "signal",
    "mmap", "ctypes", "select", "selectors", "asyncio", "importlib",
    "pkgutil", "inspect", "dis", "token", "tokenize", "pprint",
    "__future__", "builtins", "types", "weakref", "operator", "decimal",
    "fractions", "random", "statistics", "array", "heapq", "bisect",
}

_KNOWN_THIRD_PARTY: set[str] = {
    "fastapi", "uvicorn", "pydantic", "starlette", "httpx", "requests",
    "flask", "django", "click", "typer", "pytest", "numpy", "pandas",
    "scipy", "matplotlib", "sqlalchemy", "celery", "redis", "boto3",
    "setuptools", "wheel", "pip", "pkg_resources", "six", "attrs",
    "rich", "tqdm", "yaml", "toml", "dotenv", "jinja2", "aiohttp",
    "fastmcp", "pyodbc", "mcp", "openai", "anthropic", "websockets",
    "aiofiles", "httpcore", "sniffio", "anyio", "certifi", "idna",
    "h11", "charset_normalizer", "urllib3", "annotated_types",
    "pydantic_core", "typing_extensions", "textual", "playwright",
    "litellm", "openpyxl", "google", "cryptography", "dns",
    "diff_match_patch", "bs4", "psycopg2", "duckduckgo_search",
    "stripe", "twilio",
}


def _load_records(index_path: str) -> list[IndexRecord]:
    records: list[IndexRecord] = []
    with open(index_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                records.append(IndexRecord.from_json_line(line))
    return records


def _file_to_module(file_path: str) -> str:
    """Convert a file path like 'rtalk/retrieval.py' to module name 'rtalk.retrieval'."""
    mod = file_path.replace(os.sep, ".").replace("/", ".")
    if mod.endswith(".py"):
        mod = mod[:-3]
    if mod.endswith(".__init__"):
        mod = mod[:-9]
    return mod


def _all_repo_files(records: list[IndexRecord]) -> set[str]:
    return {r.file_path for r in records}


def _detect_package_roots(files: set[str]) -> list[tuple[str, str]]:
    """Detect importable package roots by finding __init__.py files and
    matching them against import patterns in the codebase.

    Returns a list of (directory_prefix, package_name) tuples.
    For example: ('core/framework', 'framework') means files under
    core/framework/ are importable as framework.*.
    """
    init_dirs: set[str] = set()
    for fp in files:
        if fp.endswith("__init__.py"):
            d = os.path.dirname(fp)
            if d:
                init_dirs.add(d)

    roots: list[tuple[str, str]] = []

    for d in sorted(init_dirs, key=len):
        parts = d.split("/")
        pkg_name = parts[-1]

        if len(parts) >= 2:
            parent = "/".join(parts[:-1])
            parent_init = parent + "/__init__.py"
            if parent_init not in files:
                roots.append((d, pkg_name))

        if len(parts) == 1:
            roots.append((d, pkg_name))

    return roots


def _build_module_map(files: set[str]) -> dict[str, str]:
    """Map module names to file paths for all repo .py files.

    Handles src-layout and nested package roots by detecting directories
    that contain __init__.py but whose parent does NOT, then registering
    alternate module paths.
    """
    mapping: dict[str, str] = {}

    for fp in files:
        if not fp.endswith(".py"):
            continue
        full_mod = _file_to_module(fp)
        mapping[full_mod] = fp

        parts = full_mod.split(".")
        for i in range(1, len(parts)):
            prefix = ".".join(parts[:i])
            if prefix not in mapping:
                init_path = "/".join(parts[:i]) + "/__init__.py"
                if init_path in files:
                    mapping[prefix] = init_path

    roots = _detect_package_roots(files)

    for dir_prefix, pkg_name in roots:
        prefix_mod = _file_to_module(dir_prefix)
        prefix_dot = prefix_mod + "."

        if prefix_mod == pkg_name:
            continue

        for fp in files:
            if not fp.endswith(".py"):
                continue
            full_mod = _file_to_module(fp)
            if full_mod == prefix_mod or full_mod.startswith(prefix_dot):
                suffix = full_mod[len(prefix_mod):]
                alt_mod = pkg_name + suffix
                if alt_mod not in mapping:
                    mapping[alt_mod] = fp

    return mapping


def _build_sibling_map(files: set[str]) -> dict[str, dict[str, str]]:
    """For each directory containing Python files, map bare module names
    to file paths. This handles imports like 'from base import Foo' when
    base.py is a sibling in the same package."""
    dir_modules: dict[str, dict[str, str]] = defaultdict(dict)
    for fp in files:
        if not fp.endswith(".py"):
            continue
        d = os.path.dirname(fp)
        basename = os.path.basename(fp)
        mod_name = basename[:-3]
        if mod_name == "__init__":
            continue
        dir_modules[d][mod_name] = fp

    for fp in files:
        if fp.endswith("__init__.py"):
            d = os.path.dirname(fp)
            for sub_dir_fp in files:
                sub_dir = os.path.dirname(sub_dir_fp)
                if sub_dir.startswith(d + "/") and sub_dir != d:
                    sub_name = sub_dir.split("/")[-1]
                    sub_init = sub_dir + "/__init__.py"
                    if sub_init in files and sub_name not in dir_modules[d]:
                        dir_modules[d][sub_name] = sub_init

    return dict(dir_modules)


def _is_internal(module_name: str, module_map: dict[str, str]) -> bool:
    """Check if a module is internal to the repo (not stdlib or third-party)."""
    if not module_name:
        return False
    top = module_name.split(".")[0]
    if top in _STDLIB_TOP:
        return False
    if top in _KNOWN_THIRD_PARTY:
        return False
    if module_name in module_map:
        return True
    if top in module_map:
        return True
    for known_mod in module_map:
        if known_mod.startswith(module_name + ".") or module_name.startswith(known_mod + "."):
            return True
    return False


def _resolve_module_to_file(module_name: str, module_map: dict[str, str]) -> str | None:
    """Resolve a module name to a file path in the repo."""
    if module_name in module_map:
        return module_map[module_name]
    parts = module_name.split(".")
    for i in range(len(parts), 0, -1):
        prefix = ".".join(parts[:i])
        if prefix in module_map:
            return module_map[prefix]
    return None


def _resolve_with_siblings(
    import_name: str,
    source_file: str,
    module_map: dict[str, str],
    sibling_map: dict[str, dict[str, str]],
) -> str | None:
    """Try to resolve an import, first via the full module map, then via
    sibling modules in the same directory."""
    result = _resolve_module_to_file(import_name, module_map)
    if result:
        return result

    top = import_name.split(".")[0]
    source_dir = os.path.dirname(source_file)
    siblings = sibling_map.get(source_dir, {})
    if top in siblings:
        return siblings[top]

    for d in sorted(sibling_map.keys(), key=len, reverse=True):
        if source_file.startswith(d + "/") or source_file.startswith(d + os.sep):
            if top in sibling_map[d]:
                return sibling_map[d][top]

    return None


def build_internal_import_graph(
    records: list[IndexRecord],
) -> tuple[list[dict[str, str]], dict[str, list[str]], dict[str, list[str]]]:
    """Build the internal-only import graph.

    Returns:
        edges: list of {"source": file, "target": file}
        forward_deps: file -> list of files it imports
        reverse_deps: file -> list of files that import it
    """
    all_files = _all_repo_files(records)
    module_map = _build_module_map(all_files)
    sibling_map = _build_sibling_map(all_files)

    edges: list[dict[str, str]] = []
    seen_edges: set[tuple[str, str]] = set()
    forward_deps: dict[str, list[str]] = defaultdict(list)
    reverse_deps: dict[str, list[str]] = defaultdict(list)

    for r in records:
        if r.record_kind != RecordKind.SYMBOL or not r.symbol:
            continue
        if r.symbol.kind != SymbolKind.IMPORT:
            continue

        source_file = r.file_path
        import_name = r.symbol.name

        if import_name.startswith("."):
            import_name = import_name.lstrip(".")
        if not import_name:
            continue

        full_module = import_name.split(".")[0]
        top = import_name.split(".")[0]
        if top in _STDLIB_TOP or top in _KNOWN_THIRD_PARTY:
            continue

        target_file = _resolve_with_siblings(
            import_name, source_file, module_map, sibling_map
        )
        if target_file is None:
            target_file = _resolve_with_siblings(
                full_module, source_file, module_map, sibling_map
            )
        if target_file is None or target_file == source_file:
            continue
        if target_file not in all_files:
            continue

        pair = (source_file, target_file)
        if pair not in seen_edges:
            seen_edges.add(pair)
            edges.append({"source": source_file, "target": target_file})
            forward_deps[source_file].append(target_file)
            reverse_deps[target_file].append(source_file)

    return edges, dict(forward_deps), dict(reverse_deps)


def build_call_graph(records: list[IndexRecord]) -> list[dict[str, str]]:
    """Build a best-effort call graph for Python functions.

    Statically resolves: bare calls foo(), module.foo(), self.foo() within same file.
    Returns edges as [{"source": "file:func", "target": "file:func"}, ...].
    """
    file_functions: dict[str, dict[str, tuple[int, int]]] = defaultdict(dict)
    file_sources: dict[str, str] = {}

    for r in records:
        if r.record_kind == RecordKind.SYMBOL and r.symbol:
            if r.symbol.kind == SymbolKind.FUNCTION:
                file_functions[r.file_path][r.symbol.name] = (
                    r.symbol.start_line,
                    r.symbol.end_line,
                )
        if r.record_kind == RecordKind.FILE_CHUNK and r.chunk:
            if r.file_path not in file_sources:
                file_sources[r.file_path] = ""
            file_sources[r.file_path] += r.chunk.text + "\n"

    call_edges: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()

    for fp, funcs in file_functions.items():
        source = file_sources.get(fp, "")
        if not source:
            continue
        lines = source.split("\n")

        for func_name, (start, end) in funcs.items():
            func_lines = lines[start - 1 : min(end, len(lines))]
            func_body = "\n".join(func_lines)

            for target_name in funcs:
                if target_name == func_name:
                    continue
                if f"{target_name}(" in func_body:
                    src_key = f"{fp}:{func_name}"
                    tgt_key = f"{fp}:{target_name}"
                    pair = (src_key, tgt_key)
                    if pair not in seen:
                        seen.add(pair)
                        call_edges.append({"source": src_key, "target": tgt_key})

    return call_edges


def compute_centrality(
    edges: list[dict[str, str]], all_files: set[str], iterations: int = 20
) -> list[tuple[str, float]]:
    """Approximate PageRank over file nodes.

    Uses a simplified power-iteration approach.
    """
    if not edges or not all_files:
        return []

    nodes = list(all_files)
    n = len(nodes)
    if n == 0:
        return []

    node_idx = {nd: i for i, nd in enumerate(nodes)}
    out_degree: list[int] = [0] * n
    in_edges: list[list[int]] = [[] for _ in range(n)]

    for e in edges:
        src = e["source"]
        tgt = e["target"]
        if src in node_idx and tgt in node_idx:
            si, ti = node_idx[src], node_idx[tgt]
            out_degree[si] += 1
            in_edges[ti].append(si)

    d = 0.85
    rank = [1.0 / n] * n

    for _ in range(iterations):
        new_rank = [(1.0 - d) / n] * n
        for i in range(n):
            for j in in_edges[i]:
                if out_degree[j] > 0:
                    new_rank[i] += d * rank[j] / out_degree[j]
        rank = new_rank

    ranked = sorted(enumerate(rank), key=lambda x: x[1], reverse=True)
    return [(nodes[i], round(score, 6)) for i, score in ranked if score > 0]


def _compute_folder(fp: str, depth: int = 2) -> str:
    """Compute a folder label at the given depth.

    For 'tools/src/aden_tools/credentials/discord.py' with depth=2:
      -> 'tools/src'
    With depth=3:
      -> 'tools/src/aden_tools'
    """
    parts = fp.split("/")
    if len(parts) <= 1:
        return "."
    folder_parts = parts[:-1]
    return "/".join(folder_parts[:depth]) if len(folder_parts) >= depth else "/".join(folder_parts)


def _pick_folder_depth(all_files: set[str]) -> int:
    """Automatically pick a folder depth so no single group dominates.
    Keeps increasing depth until the largest folder has < 40% of total files
    or we reach depth 5."""
    total = len(all_files)
    if total == 0:
        return 1
    for depth in range(1, 6):
        buckets: dict[str, int] = defaultdict(int)
        for fp in all_files:
            buckets[_compute_folder(fp, depth)] += 1
        largest = max(buckets.values())
        if largest <= total * 0.4 or depth >= 5:
            return depth
    return 2


def compute_clusters(all_files: set[str], depth: int = 1) -> dict[str, list[str]]:
    """Cluster files by directory at the given depth."""
    clusters: dict[str, list[str]] = defaultdict(list)
    for fp in sorted(all_files):
        folder = _compute_folder(fp, depth)
        clusters[folder].append(fp)
    return dict(clusters)


def build_graph(index_path: str, out_path: str = ".rtalk/graph.json") -> dict[str, Any]:
    """Build the full repo graph and write to JSON.

    Returns the graph dict.
    """
    records = _load_records(index_path)
    all_files = _all_repo_files(records)
    py_files = {f for f in all_files if f.endswith(".py")}

    import_edges, forward_deps, reverse_deps = build_internal_import_graph(records)
    call_edges = build_call_graph(records)
    centrality = compute_centrality(import_edges, py_files)

    folder_depth = _pick_folder_depth(all_files)
    clusters = compute_clusters(all_files, folder_depth)

    symbols_by_file: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in records:
        if r.record_kind == RecordKind.SYMBOL and r.symbol:
            if r.symbol.kind != SymbolKind.IMPORT:
                symbols_by_file[r.file_path].append({
                    "name": r.symbol.name,
                    "kind": r.symbol.kind.value,
                    "start_line": r.symbol.start_line,
                    "end_line": r.symbol.end_line,
                })

    file_lines: dict[str, int] = {}
    for r in records:
        if r.file_path not in file_lines or r.total_lines > file_lines[r.file_path]:
            file_lines[r.file_path] = r.total_lines

    nodes: list[dict[str, Any]] = []
    for fp in sorted(all_files):
        folder = _compute_folder(fp, folder_depth)
        is_test = "test" in fp.lower()
        nodes.append({
            "id": fp,
            "label": os.path.basename(fp),
            "folder": folder,
            "is_test": is_test,
            "is_python": fp.endswith(".py"),
            "total_lines": file_lines.get(fp, 0),
            "symbols": symbols_by_file.get(fp, []),
            "dependencies": forward_deps.get(fp, []),
            "dependents": reverse_deps.get(fp, []),
        })

    graph: dict[str, Any] = {
        "nodes": nodes,
        "import_edges": import_edges,
        "call_edges": call_edges,
        "centrality": [{"file": f, "score": s} for f, s in centrality],
        "clusters": clusters,
        "reverse_deps": reverse_deps,
        "forward_deps": forward_deps,
        "folder_depth": folder_depth,
        "stats": {
            "total_files": len(all_files),
            "python_files": len(py_files),
            "import_edges": len(import_edges),
            "call_edges": len(call_edges),
        },
    }

    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, "w", encoding="utf-8") as f:
        json.dump(graph, f, indent=2, ensure_ascii=False)

    print(f"Graph: {len(nodes)} nodes, {len(import_edges)} import edges, "
          f"{len(call_edges)} call edges -> {out_path}")

    return graph
