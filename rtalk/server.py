"""FastAPI server exposing index, search, answer, explain, graph, guide, and impact endpoints."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from rtalk.clone import is_github_url, resolve_repo
from rtalk.index import build_index
from rtalk.retrieval import RetrievalEngine, detect_explain_intent
from rtalk.answer import answer_question
from rtalk.explain import summarize_repo
from rtalk.graph import build_graph
from rtalk.guide import run_guide
from rtalk.impact import analyze_impact, get_top_impact_files
from rtalk.node_context import explain_file, explain_folder
from rtalk.issues import fetch_issues, Issue

app = FastAPI(
    title="Repo That Talks Back",
    version="2.0.0",
    description="Turn any repository into an interactive, citation-grounded assistant.",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

WEB_DIR = Path(__file__).resolve().parent.parent / "web"
if WEB_DIR.is_dir():
    app.mount("/static", StaticFiles(directory=str(WEB_DIR)), name="static")


# ---------------------------------------------------------------------------
# Project-path helpers
# ---------------------------------------------------------------------------

def _project_slug(repo_path: str) -> str:
    """Deterministic slug from a repo path: ``<basename>_<md5[:10]>``."""
    abs_path = os.path.abspath(repo_path)
    digest = hashlib.md5(abs_path.encode()).hexdigest()[:10]
    return f"{os.path.basename(abs_path)}_{digest}"


def _project_paths(repo_path: str) -> tuple[str, str]:
    """Return ``(index_path, graph_path)`` under ``.rtalk/projects/<slug>/``."""
    slug = _project_slug(repo_path)
    project_dir = os.path.join(".rtalk", "projects", slug)
    os.makedirs(project_dir, exist_ok=True)
    return (
        os.path.join(project_dir, "index.jsonl"),
        os.path.join(project_dir, "graph.json"),
    )


# ---------------------------------------------------------------------------
# Request / Response models
# ---------------------------------------------------------------------------

class IndexRequest(BaseModel):
    repo_path: str
    out_path: str = ".rtalk/index.jsonl"
    chunk_size: int = 80
    refresh: bool = False


class IndexResponse(BaseModel):
    records: int
    index_path: str
    repo_local_path: str


class SearchRequest(BaseModel):
    index_path: str = ".rtalk/index.jsonl"
    repo_path: str | None = None
    query: str
    top_k: int = 5


class SnippetOut(BaseModel):
    file_path: str
    start_line: int
    end_line: int
    text: str
    score: float
    method: str
    citation: str


class SearchResponse(BaseModel):
    snippets: list[SnippetOut]


class AnswerRequest(BaseModel):
    index_path: str = ".rtalk/index.jsonl"
    repo_path: str | None = None
    graph_path: str | None = None
    query: str
    top_k: int = 5
    ai_api_key: str | None = None


class AnswerResponse(BaseModel):
    query: str
    summary: str
    evidence: list[SnippetOut]
    explanation: str
    refused: bool
    refusal_reason: str
    missing_info: str
    is_ai_generated: bool = False


class ExplainRequest(BaseModel):
    index_path: str = ".rtalk/index.jsonl"
    repo_path: str | None = None
    use_ai_summary: bool = False
    ai_api_key: str | None = None


class ExplainResponse(BaseModel):
    report: dict[str, Any]
    text: str


class GraphRequest(BaseModel):
    index_path: str = ".rtalk/index.jsonl"
    out_path: str = ".rtalk/graph.json"
    repo_path: str | None = None


class GraphResponse(BaseModel):
    graph: dict[str, Any]


class GuideRequest(BaseModel):
    index_path: str = ".rtalk/index.jsonl"
    query: str
    repo_path: str | None = None
    graph_path: str | None = None
    ai_api_key: str | None = None


class GuideResponse(BaseModel):
    report: dict[str, Any]
    text: str


class ImpactRequest(BaseModel):
    index_path: str = ".rtalk/index.jsonl"
    graph_path: str = ".rtalk/graph.json"
    target_file: str | None = None
    repo_path: str | None = None
    mode: str = "single"
    folder_filter: str | None = None
    top_n: int = 15
    ai_api_key: str | None = None


class ImpactResponse(BaseModel):
    report: dict[str, Any]
    text: str
    top_impact: dict[str, Any] | None = None


class NodeContextRequest(BaseModel):
    index_path: str = ".rtalk/index.jsonl"
    target: str  # file path (e.g. rtalk/retrieval.py) or folder (e.g. tools/src/aden_tools)
    is_folder: bool = False
    ai_api_key: str | None = None
    graph_path: str | None = None


class NodeContextResponse(BaseModel):
    summary: str


class IssuesRequest(BaseModel):
    repo_path: str
    per_page: int = 5
    page: int = 1
    state: str = "open"
    github_token: str | None = None
    labels: str | None = None
    sort: str = "created"
    direction: str = "desc"


class IssuesResponse(BaseModel):
    issues: list[dict[str, Any]]
    by_label: dict[str, list[dict[str, Any]]]
    has_more: bool


class AutoBuildRequest(BaseModel):
    repo_path: str
    refresh: bool = False


class AutoBuildResponse(BaseModel):
    index_path: str
    graph_path: str
    graph: dict[str, Any]
    cached: bool
    repo_local_path: str


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@app.get("/")
def root():
    index_html = WEB_DIR / "index.html"
    if index_html.is_file():
        return FileResponse(str(index_html))
    return {"message": "Repo That Talks Back API. See /docs for endpoints."}


@app.post("/auto-build", response_model=AutoBuildResponse)
def api_auto_build(req: AutoBuildRequest):
    repo_input = req.repo_path

    if is_github_url(repo_input):
        try:
            repo_local = resolve_repo(repo_input, refresh=req.refresh)
        except (ValueError, RuntimeError) as exc:
            raise HTTPException(status_code=400, detail=str(exc))
    else:
        repo_local = os.path.abspath(repo_input)
        if not os.path.isdir(repo_local):
            hint = ""
            if "github.com" in repo_input.lower() or "git@" in repo_input:
                hint = (
                    " It looks like a URL but was not recognised. "
                    "Try a full URL like https://github.com/owner/repo"
                )
            raise HTTPException(
                status_code=400,
                detail=f"Not a directory: {repo_input}.{hint}",
            )

    index_path, graph_path = _project_paths(repo_local)
    cached = True

    if not os.path.isfile(index_path) or req.refresh:
        build_index(repo_path=repo_local, out_path=index_path)
        cached = False

    if not os.path.isfile(graph_path) or req.refresh:
        build_graph(index_path=index_path, out_path=graph_path)
        cached = False

    with open(graph_path, "r") as f:
        graph_data = json.load(f)

    return AutoBuildResponse(
        index_path=index_path,
        graph_path=graph_path,
        graph=graph_data,
        cached=cached,
        repo_local_path=repo_local,
    )


@app.post("/index", response_model=IndexResponse)
def api_index(req: IndexRequest):
    repo_input = req.repo_path

    if is_github_url(repo_input):
        try:
            repo_local = resolve_repo(repo_input, refresh=req.refresh)
        except (ValueError, RuntimeError) as exc:
            raise HTTPException(status_code=400, detail=str(exc))
    else:
        repo_local = os.path.abspath(repo_input)
        if not os.path.isdir(repo_local):
            hint = ""
            if "github.com" in repo_input.lower() or "git@" in repo_input:
                hint = (
                    " It looks like a URL but was not recognised. "
                    "Try a full URL like https://github.com/owner/repo"
                )
            raise HTTPException(
                status_code=400,
                detail=f"Not a directory: {repo_input}.{hint}",
            )

    index_path, _ = _project_paths(repo_local)
    count = build_index(repo_path=repo_local, out_path=index_path, chunk_size=req.chunk_size)
    return IndexResponse(records=count, index_path=index_path, repo_local_path=repo_local)


@app.post("/search", response_model=SearchResponse)
def api_search(req: SearchRequest):
    if not os.path.isfile(req.index_path):
        raise HTTPException(status_code=400, detail=f"Index not found: {req.index_path}")
    engine = RetrievalEngine.from_index(req.index_path, repo_path=req.repo_path)
    snippets = engine.search(req.query, top_k=req.top_k)
    return SearchResponse(
        snippets=[
            SnippetOut(
                file_path=s.file_path,
                start_line=s.start_line,
                end_line=s.end_line,
                text=s.text,
                score=round(s.score, 4),
                method=s.method,
                citation=s.citation(),
            )
            for s in snippets
        ]
    )


def _load_graph_schema(graph_path: str | None) -> str:
    """Load graph and build a compact schema summary for AI context."""
    if not graph_path or not os.path.isfile(graph_path):
        return ""
    try:
        import json
        with open(graph_path, "r", encoding="utf-8") as f:
            g = json.load(f)
        parts: list[str] = []
        nodes = g.get("nodes", [])
        centrality = g.get("centrality", [])[:15]
        import_edges = g.get("import_edges", [])[:30]
        clusters = g.get("clusters", {})

        if nodes:
            folders: dict[str, int] = {}
            for n in nodes:
                folder = n.get("folder", ".")
                folders[folder] = folders.get(folder, 0) + 1
            top_folders = sorted(folders.items(), key=lambda x: -x[1])[:10]
            parts.append("Folders: " + ", ".join(f"{f} ({c})" for f, c in top_folders))
        if centrality:
            parts.append("Central files: " + ", ".join(c["file"] for c in centrality[:8]))
        if import_edges:
            edges_str = "; ".join(f"{e['source']}->{e['target']}" for e in import_edges[:12])
            parts.append("Key imports: " + edges_str)
        if clusters:
            part_names = list(clusters.keys())[:8]
            parts.append("Modules: " + ", ".join(part_names))
        return "\n".join(parts)[:1500]
    except Exception:
        return ""


@app.post("/answer", response_model=AnswerResponse)
def api_answer(req: AnswerRequest):
    if not os.path.isfile(req.index_path):
        raise HTTPException(status_code=400, detail=f"Index not found: {req.index_path}")

    if detect_explain_intent(req.query):
        report = summarize_repo(req.index_path)
        return AnswerResponse(
            query=req.query,
            summary=report.what.body,
            evidence=[],
            explanation=report.render_text(),
            refused=False,
            refusal_reason="",
            missing_info="",
        )

    from rtalk.answer import GroqAdapter

    graph_path = req.graph_path
    if not graph_path and req.index_path:
        idx_dir = os.path.dirname(req.index_path)
        candidate = os.path.join(idx_dir, "graph.json")
        if os.path.isfile(candidate):
            graph_path = candidate

    schema = _load_graph_schema(graph_path)

    engine = RetrievalEngine.from_index(req.index_path, repo_path=req.repo_path)
    evidence = engine.search(
        req.query,
        top_k=req.top_k,
        ai_api_key=req.ai_api_key,
        schema_understanding=schema,
    )
    llm = GroqAdapter(req.ai_api_key) if req.ai_api_key else None
    result = answer_question(req.query, evidence, llm=llm, schema_understanding=schema)
    return AnswerResponse(
        query=result.query,
        summary=result.summary,
        evidence=[
            SnippetOut(
                file_path=ev.file_path,
                start_line=ev.start_line,
                end_line=ev.end_line,
                text=ev.text,
                score=round(ev.score, 4),
                method=ev.method,
                citation=ev.citation(),
            )
            for ev in result.evidence
        ],
        explanation=result.explanation,
        refused=result.refused,
        refusal_reason=result.refusal_reason,
        missing_info=result.missing_info,
        is_ai_generated=result.is_ai_generated,
    )


@app.post("/explain", response_model=ExplainResponse)
def api_explain(req: ExplainRequest):
    if not os.path.isfile(req.index_path):
        raise HTTPException(status_code=400, detail=f"Index not found: {req.index_path}")
    report = summarize_repo(
        req.index_path,
        repo_path=req.repo_path,
        use_ai_summary=req.use_ai_summary,
        ai_api_key=req.ai_api_key,
    )
    return ExplainResponse(report=report.to_dict(), text=report.render_text())


@app.post("/graph", response_model=GraphResponse)
def api_graph(req: GraphRequest):
    if req.repo_path:
        repo_local = os.path.abspath(req.repo_path)
        index_path, graph_out = _project_paths(repo_local)
    else:
        index_path = req.index_path
        graph_out = req.out_path

    if not os.path.isfile(index_path):
        raise HTTPException(status_code=400, detail=f"Index not found: {index_path}")
    graph_data = build_graph(index_path=index_path, out_path=graph_out)
    return GraphResponse(graph=graph_data)


@app.post("/guide", response_model=GuideResponse)
def api_guide(req: GuideRequest):
    if not os.path.isfile(req.index_path):
        raise HTTPException(status_code=400, detail=f"Index not found: {req.index_path}")
    graph_path = req.graph_path
    if not graph_path and req.index_path:
        idx_dir = os.path.dirname(req.index_path)
        candidate = os.path.join(idx_dir, "graph.json")
        if os.path.isfile(candidate):
            graph_path = candidate
    schema = _load_graph_schema(graph_path)
    report = run_guide(
        query=req.query,
        index_path=req.index_path,
        repo_path=req.repo_path,
        graph_path=graph_path,
        ai_api_key=req.ai_api_key,
        schema_understanding=schema,
    )
    return GuideResponse(report=report.to_dict(), text=report.render_text())


@app.post("/impact", response_model=ImpactResponse)
def api_impact(req: ImpactRequest):
    if not os.path.isfile(req.index_path):
        raise HTTPException(status_code=400, detail=f"Index not found: {req.index_path}")
    if not os.path.isfile(req.graph_path):
        raise HTTPException(status_code=400, detail=f"Graph not found: {req.graph_path}. Run /graph first.")

    if req.mode == "top" or not req.target_file:
        top_report = get_top_impact_files(
            index_path=req.index_path,
            graph_path=req.graph_path,
            repo_path=req.repo_path,
            top_n=req.top_n,
            folder_filter=req.folder_filter,
            ai_api_key=req.ai_api_key,
        )
        return ImpactResponse(
            report={},
            text="",
            top_impact=top_report.to_dict(),
        )

    report = analyze_impact(
        target_file=req.target_file,
        index_path=req.index_path,
        graph_path=req.graph_path,
        repo_path=req.repo_path,
    )
    return ImpactResponse(report=report.to_dict(), text=report.render_text())


@app.post("/node-context", response_model=NodeContextResponse)
def api_node_context(req: NodeContextRequest):
    """File-specific RAG: retrieves chunks for the target from index, sends to LLM."""
    if not os.path.isfile(req.index_path):
        raise HTTPException(status_code=400, detail=f"Index not found: {req.index_path}")
    if not req.ai_api_key or not req.ai_api_key.strip():
        raise HTTPException(status_code=400, detail="ai_api_key required for node context")

    centrality_list: list[dict] | None = None
    if req.graph_path and os.path.isfile(req.graph_path):
        try:
            with open(req.graph_path, "r", encoding="utf-8") as f:
                graph_data = json.load(f)
            centrality_list = graph_data.get("centrality", [])
        except Exception:
            pass

    if req.is_folder:
        summary, err = explain_folder(
            req.index_path,
            req.target.rstrip("/"),
            req.ai_api_key,
            centrality_list=centrality_list,
        )
    else:
        summary, err = explain_file(
            req.index_path,
            req.target,
            req.ai_api_key,
        )

    if err:
        raise HTTPException(status_code=500, detail=err)
    if not summary:
        raise HTTPException(status_code=500, detail="Failed to generate context")
    return NodeContextResponse(summary=summary)


@app.post("/issues", response_model=IssuesResponse)
def api_issues(req: IssuesRequest):
    """Fetch GitHub issues for a repo, categorized by labels."""
    try:
        issues, by_label, has_more = fetch_issues(
            repo_input=req.repo_path,
            per_page=req.per_page,
            page=req.page,
            state=req.state,
            github_token=req.github_token,
            labels=req.labels,
            sort=req.sort,
            direction=req.direction,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    def _issue_to_dict(i: Issue) -> dict[str, Any]:
        return {
            "number": i.number,
            "title": i.title,
            "state": i.state,
            "html_url": i.html_url,
            "labels": i.labels,
            "created_at": i.created_at,
            "body_preview": i.body_preview,
            "updated_at": i.updated_at,
            "comments": i.comments,
        }

    return IssuesResponse(
        issues=[_issue_to_dict(i) for i in issues],
        by_label={
            k if k != "_unlabeled" else "unlabeled": [_issue_to_dict(i) for i in v]
            for k, v in by_label.items()
        },
        has_more=has_more,
    )


