# Architecture

High-level architecture of Repo That Talks Back. For implementation details, see [logic.md](logic.md).

---

## System Overview

```
┌─────────────────────────────────────────────────────────────────────────┐
│                           Repo That Talks Back                           │
├─────────────────────────────────────────────────────────────────────────┤
│                                                                         │
│   Repo (local / GitHub)                                                  │
│         │                                                               │
│         ▼                                                               │
│   ┌──────────┐     ┌──────────┐                                        │
│   │  index   │────▶│  graph   │                                        │
│   │  .py     │     │  .py     │                                        │
│   └────┬─────┘     └────┬─────┘                                        │
│        │                │                                               │
│        ▼                ▼                                               │
│   JSONL index      graph.json                                           │
│        │                │                                               │
│        └───────┬────────┘                                               │
│                ▼                                                        │
│        ┌───────────────┐                                                │
│        │     toc.py     │  Table of contents (folder → file → symbol)    │
│        └───────┬───────┘                                                │
│                ▼                                                        │
│        ┌───────────────┐     ┌──────────────┐                          │
│        │  retrieval.py  │────▶│ generation.py │  (optional LLM nav)     │
│        │  TOC+TF-IDF+  │     │  extract +    │                          │
│        │  exact search │     │  synthesize   │                          │
│        └───────┬───────┘     └──────┬───────┘                          │
│                │                    │                                   │
│                ▼                    ▼                                   │
│        ┌─────────────────────────────────────┐                         │
│        │  answer / guide / explain / impact   │                         │
│        │  node_context (Map panel)            │                         │
│        └─────────────────────────────────────┘                         │
│                                                                         │
└─────────────────────────────────────────────────────────────────────────┘
```

---

## Data Flow

```
                    Repo
                      │
        ┌─────────────┴─────────────┐
        ▼                           ▼
   index.py                    clone.py (if GitHub URL)
        │                           │
        ▼                           ▼
   index.jsonl                 local path
        │
        ├──────────────────────────┐
        ▼                          ▼
   toc.py                      graph.py
        │                          │
        ▼                          ▼
   TOC tree                   graph.json
        │                          │
        └──────────┬──────────────┘
                   ▼
            retrieval.py
                   │
        ┌──────────┼──────────┐
        ▼          ▼          ▼
   TOC search  TF-IDF    Exact (ripgrep)
        │          │          │
        └──────────┼──────────┘
                   ▼
            EvidenceSnippets
                   │
        ┌──────────┼──────────┐
        ▼          ▼          ▼
   answer.py  guide.py   explain.py
   impact.py  node_context.py
```

---

## Component Responsibilities

| Component | Responsibility |
|-----------|----------------|
| **index.py** | Scan repo, chunk files, extract symbols (AST). Output JSONL. |
| **graph.py** | Build import graph, PageRank centrality, folder clusters. |
| **toc.py** | Build hierarchical TOC (folder → file → symbol → chunk). |
| **retrieval.py** | TOC + TF-IDF + exact search. Optional LLM tree navigation. |
| **generation.py** | LLM node selection, chunk extraction, answer synthesis. |
| **answer.py** | Citation-grounded answering. Refuse when evidence insufficient. |
| **guide.py** | Query-driven investigation. RAG + optional AI steps. |
| **explain.py** | Structured repo overview. Persona-filtered. |
| **impact.py** | Change impact (single file or top N). Risk scoring. |
| **node_context.py** | File-specific RAG for Map panel. |
| **issues.py** | GitHub issues fetch, label grouping. |

---

## Retrieval Pipeline (Vectorless)

```
Query
  │
  ├─▶ TOC search      (match folder/file/symbol titles)
  ├─▶ TF-IDF          (lexical over chunk text)
  └─▶ Exact search    (ripgrep when repo path available)
  │
  ▼
Merge, deduplicate, rank
  │
  ▼
EvidenceSnippets (file:start-end)
```

**Optional:** When Groq API key provided, LLM tree search runs first — LLM selects TOC node IDs, then chunks are extracted from those nodes and merged with lexical results.

---

## Web UI Flow

```
Browser
   │
   ▼
FastAPI (server.py)
   │
   ├─▶ /auto-build    → index + graph
   ├─▶ /search        → retrieval
   ├─▶ /answer        → answer.py
   ├─▶ /explain       → explain.py
   ├─▶ /guide         → guide.py
   ├─▶ /impact        → impact.py
   ├─▶ /node-context   → node_context.py
   └─▶ /issues        → issues.py
```

---

## File Layout

```
.rtalk/
  projects/
    <repo_slug>/
      index.jsonl    # Chunks + symbols
      graph.json     # Nodes, edges, centrality
  repos/             # Cloned GitHub repos (when URL used)
```

---

## References

- [logic.md](logic.md) — Detailed logic for each module
- [PageIndex Vectorless RAG](https://docs.pageindex.ai/cookbook/vectorless-rag-pageindex)
- [LLM Tree Search](https://docs.pageindex.ai/tutorials/tree-search/llm)
