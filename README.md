# Repo That Talks Back v2

<img width="1465" height="831" alt="image" src="https://github.com/user-attachments/assets/31a8aa1b-148c-45bc-81b9-08301d625cd9" />


[![Python 3.11+](https://img.shields.io/badge/python-3.11+-3776AB?style=flat&logo=python&logoColor=white)](https://www.python.org/downloads/)
[![Tests](https://img.shields.io/badge/tests-pytest-green?style=flat)](https://docs.pytest.org/)
[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg?style=flat)](https://opensource.org/licenses/MIT)

Turn any GitHub repository into an interactive assistant with **file + line citations**, a visual **dependency map**, **query-driven guided investigation**, and **change impact analysis**. Uses **vectorless RAG** (no embeddings) with an optional Groq API for AI synthesis.

## Why vectorless RAG?

We use **lexical search** (TOC + TF-IDF + exact) instead of embeddings. Tradeoffs:

| | Vectorless | Vector / Embedding-based |
|--|------------|--------------------------|
| **Cost** | No embedding API or model. Index is plain JSONL. | Embedding API calls or local model. Vector DB. |
| **Offline** | Core retrieval works fully offline. | Typically needs API or GPU for embeddings. |
| **Simplicity** | No vector DB, no embedding pipeline. Easy to debug. | More moving parts. |
| **Recall on synonyms** | Weak. "auth" won't match "authentication" unless you add hints. | Strong. Semantic similarity finds related terms. |

**When vectorless works well:** Codebases where exact terms, file paths, and symbol names matter. Queries like "How does TF-IDF scoring work?" or "Where is `RetrievalEngine` defined?" — lexical + TOC is effective.

**Mitigation for synonyms:** We use `_FILENAME_HINTS` (e.g. "search" → "retrieval search index query") and optional LLM tree search to steer retrieval when lexical match is sparse.

## What makes it different

- **Citations or refusal.** Every claim includes `file:start-end` citations. If evidence is insufficient, the system refuses instead of hallucinating.
- **Vectorless RAG.** No embeddings or vector DB. PageIndex-style: TOC + LLM tree search (when AI key) + TF-IDF + exact search. See [architecture.md](architecture.md) and [logic.md](logic.md).
- **Free and offline.** Core retrieval works fully offline. Add a Groq API key for AI-powered Ask, Guide, Explain, Impact, and Map node context.
- **Interactive Repo Map.** Visual dependency graph (folder drill-down, top 25 files per folder), treemap, sunburst, risk heatmap. Click a file for **file-specific AI context** (RAG over that file's indexed chunks).
- **Query-driven Guide.** Enter any topic; retrieval finds evidence and (with API key) AI generates a step-by-step guide.
- **Impact Analysis.** Single-file mode or top high-impact files by folder. Optional AI summary.
- **Explain output** is persona-oriented (PM, Engineer, UX, Researcher) with optional AI summaries and **role-specific AI explanations** per persona.
- **DOS CMD theme** with blinking cursor, CMD-style loading animations, and Groq key helper link.

## Quickstart

```bash
# Install
pip install -e ".[dev]"

# Index a repo
python -m rtalk index --repo . --out .rtalk/index.jsonl

# Build the dependency graph
python -m rtalk graph --index .rtalk/index.jsonl

# Ask a question (add --ai-key for Groq synthesis)
python -m rtalk ask --index .rtalk/index.jsonl "How does search work?"

# Explain the repo
python -m rtalk explain --index .rtalk/index.jsonl

# Run a guided investigation (query-driven, no hardcoded plans)
python -m rtalk guide --index .rtalk/index.jsonl --query "How does retrieval rank results?"

# Analyze change impact (single file or top high-impact)
python -m rtalk impact --index .rtalk/index.jsonl --file rtalk/retrieval.py
python -m rtalk impact --index .rtalk/index.jsonl --top 15 --folder rtalk

# Start the web UI
python -m rtalk serve
# Open http://127.0.0.1:8000
```

## Demo Flow

```bash
# 1. Index
python -m rtalk index --repo .

# 2. Build graph
python -m rtalk graph

# 3. Open the web UI
python -m rtalk serve

# In the browser:
# - Add your Groq API key in the Repository panel (optional, for AI)
# - Click "Build Graph" in the header panel
# - Map: interactive dependency graph, treemap, sunburst, heatmap
# - Ask: "How does TF-IDF scoring work?" (uses vectorless RAG + optional AI)
# - Guide: enter a topic, get a step-by-step investigation
# - Impact: view top high-impact files or analyze a single file
# - Explain: persona-filtered repo overview with optional AI summaries and role-specific explanations
# - Map: click a file node to get AI context (file-specific RAG)
```

## Project structure

```
repo-that-talks-back/
  rtalk/
    __init__.py          # Package init
    __main__.py          # CLI: index, graph, ask, explain, guide, impact, serve
    models.py            # Shared data models
    clone.py             # GitHub URL detection and shallow clone
    index.py             # File scanner, chunker, AST symbol extractor
    toc.py               # Table-of-contents tree (PageIndex-style)
    retrieval.py         # TOC + TF-IDF + exact search (vectorless RAG)
    explain.py           # Structured repo overview, persona-oriented
    graph.py             # Internal import graph, call graph, PageRank
    guide.py             # Query-driven guided investigation (RAG + AI)
    generation.py        # PageIndex-style: LLM tree search, extract, synthesize
    impact.py            # Change impact (single file or top high-impact)
    answer.py            # Citation-grounded answering engine
    node_context.py      # File-specific RAG for Map node panel
    groq_client.py       # Groq API client (llama-3.3-70b-versatile)
    server.py            # FastAPI server (all endpoints)
  web/
    index.html           # Web UI (Ask, Explain, Map, Guide, Impact)
  tests/
    test_toc.py         # TOC and vectorless RAG tests
    test_plans.py       # Guide tests
    ...
  architecture.md       # High-level architecture
  logic.md             # Detailed logic documentation
  README.md
  pyproject.toml
```

## API Endpoints

| Method | Path          | Description                              |
|--------|---------------|------------------------------------------|
| POST   | /index        | Index a repo (local path or GitHub URL)  |
| POST   | /auto-build   | Index + graph in one call                |
| POST   | /graph        | Build dependency graph                   |
| POST   | /search       | Search for evidence snippets             |
| POST   | /answer       | Get a grounded answer (with graph schema)|
| POST   | /explain      | Get a structured repo overview           |
| POST   | /guide        | Run a query-driven guided investigation  |
| POST   | /impact       | Analyze impact (single file or top N)   |
| POST   | /node-context | File-specific AI context for Map panel   |

## Dependencies

- **Core:** fastapi, uvicorn, pydantic
- **AI (optional):** groq — add API key for Ask, Guide, Explain, Impact synthesis

## Running tests

```bash
pytest -v
```

## Roadmap

- [ ] tree-sitter for multi-language support (JS, TS, Go, Rust)
- [ ] Local LLM integration via the LLMAdapter interface
- [ ] GitHub Action for PR-triggered analysis
- [ ] Live file watching and incremental re-indexing

## License

MIT
