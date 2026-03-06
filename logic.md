# Logic Documentation

This document details the core logic of Repo That Talks Back: indexing, retrieval, answering, and analysis.

---

## 1. Indexing (`rtalk/index.py`)

### Input
- Repository path (local directory or GitHub URL)
- Chunk size (default 80 lines)

### Process
1. **File collection:** Walk directory or use `git ls-files` if available. Filter by extensions: `.py`, `.md`, `.txt`, `.yml`, `.yaml`, `.toml`, `.json`.
2. **Per-file processing:**
   - **Chunking:** Split file into fixed-size chunks (overlapping by default). Each chunk has `start_line`, `end_line`, `text`.
   - **Symbol extraction (Python only):** Use `ast.parse` to extract:
     - `FunctionDef` / `AsyncFunctionDef` → function
     - `ClassDef` → class
     - `Import` / `ImportFrom` → import
3. **Output:** JSONL file. Each line is an `IndexRecord`:
   - `record_kind`: `file_chunk` or `symbol`
   - `file_path`, `sha256`, `total_lines`
   - `chunk` (for file_chunk) or `symbol` (for symbol)

### Data flow
```
Repo → Files → Chunks + Symbols → JSONL index
```

---

## 2. Table of Contents (`rtalk/toc.py`)

**Reference:** [PageIndex Vectorless RAG](https://docs.pageindex.ai/cookbook/vectorless-rag-pageindex)

### Purpose
Build a hierarchical "table of contents" before retrieval. Enables two-phase search: (1) navigate tree by titles/summaries, (2) fetch chunk content.

### Structure
```
folder
  └── file
        └── symbol (class/function)
              └── chunk
```

### `build_toc(records)`
1. Group records by file: symbols (class/function only) and chunks.
2. For each file:
   - If Python with symbols: create symbol nodes, attach chunks that overlap symbol line ranges.
   - Else: create file node with top chunks as children.
3. Group files by folder (path depth 2).
4. Return root list of folder nodes.

### `TOCIndex`
- Inverted index over node `title`, `text`, `file_path` (tokenized).
- `search_nodes(query)`: Phase 1 — lexical match on node text, return scored nodes with chunk records.
- `get_chunk_records_from_nodes()`: Phase 2 — extract `IndexRecord` from matched nodes.

---

## 3. Retrieval (`rtalk/retrieval.py`)

### Vectorless RAG Pipeline

No embeddings. Three retrieval methods combined:

| Method | Source | When |
|--------|--------|------|
| **TOC** | Table-of-contents tree | First: match query to folder/file/symbol titles |
| **Lexical** | TF-IDF over chunk text | Always: term frequency × inverse document frequency |
| **Exact** | ripgrep or Python substring | When repo path available |

### Flow
```
Query
  → TOC search (titles, file paths, symbol names)
  → TF-IDF search (chunk text)
  → Exact search (ripgrep / substring)
  → Merge, deduplicate (overlap threshold 0.3), rank by score
  → Fallback: if no results, expand query with _FILENAME_HINTS (e.g. "search" → "retrieval search index query")
  → Return top_k EvidenceSnippets
```

### TF-IDF
- Tokenization: `[a-z0-9_]+` lowercase.
- Score: `Σ (tf × idf)` per query token, with:
  - `tf = term_freq / len(tokens)`
  - `idf = log((n_docs + 1) / (df + 1)) + 1`
- Boosts: filename match (+0.3 per token), symbol match (+0.4), `_FILENAME_HINTS` (+0.15).

### EvidenceSnippet
- `file_path`, `start_line`, `end_line`, `text`, `score`, `method` (`toc` | `lexical` | `exact` | `llm_nav`).

### LLM Tree Search (when `ai_api_key` provided)
PageIndex-style reasoning over TOC before lexical search:
1. Serialize TOC to compact JSON (node_id, title, summary).
2. LLM prompt: "Identify which node IDs are MOST likely to contain the answer. Output comma-separated node IDs."
3. Parse LLM response → node IDs.
4. Extract chunk records for those nodes (and their descendants).
5. Merge with TOC + TF-IDF + exact results (LLM-nav results ranked first).

---

## 4. Generation (`rtalk/generation.py`)

**Reference:** [PageIndex Vectorless RAG](https://docs.pageindex.ai/cookbook/vectorless-rag-pageindex), [LLM Tree Search](https://docs.pageindex.ai/tutorials/tree-search/llm)

### PageIndex-Style Pipeline

| Phase | Input | Output |
|-------|-------|--------|
| **2A (Navigate)** | TOC tree + Query + Schema | Selected node IDs (LLM reasoning) |
| **2B (Extract)** | Node IDs | Chunk records (no LLM) |
| **2C (Answer)** | Extracted text + Query | Final answer with citations (LLM) |

### Phase 2A: `llm_select_nodes()`
- Serialize TOC to JSON: `node_id`, `title`, `summary`, `kind`, nested `nodes`.
- Prompt: "You are a codebase navigation agent. Study the TOC summaries. Output ONLY comma-separated node_id values. Return 1 to 5 node IDs."
- Parse response for `n0001`, `n0002`, etc.

### Phase 2B: `extract_chunks_from_node_ids()`
- Walk TOC tree, collect `chunk_record` for selected nodes and their descendants.
- No LLM — pure tree traversal.

### Phase 2C: `synthesize_answer_prompt()`
- PageIndex-style: "You have been given a RELEVANT EXCERPT (already pre-selected by a reasoning agent). Answer using ONLY this excerpt. At the end, cite the file(s) and line(s) you drew from."
- Used by `answer.py` when LLM is available.

---

## 5. Answering (`rtalk/answer.py`)

### Input
- Query
- Evidence snippets (from retrieval)
- Optional: `LLMAdapter` (Groq), `schema_understanding` (graph summary)

### Logic
1. **No evidence:**
   - If LLM + schema: generate answer from structure only.
   - Else: refuse with guidance (index first, try different terms).
2. **Low relevance** (best score < 0.01): refuse with file list.
3. **With evidence:**
   - Rank by `_compute_relevance` (keyword overlap + score).
   - If LLM: build prompt with evidence + schema, call Groq.
   - Else: deterministic `_build_summary` and `_build_explanation`.

### Groq
- Model: `llama-3.3-70b-versatile`
- Input truncated to ~7.5k tokens (30k chars) to stay under 12k total.

---

## 6. Guide (`rtalk/guide.py`)

### Query-driven (no hardcoded plans)
1. Run `RetrievalEngine.search(query, top_k=8)`.
2. If no evidence: return empty report.
3. If AI key:
   - Build prompt: query + schema + evidence.
   - Ask Groq for 3–6 steps (title + description per step).
   - Parse `STEP N: Title\nDescription` into `GuideStep`.
4. Else: single step with evidence list.

### Shared pipeline
- Same `RetrievalEngine` as Ask (TOC + TF-IDF + exact).

---

## 7. Explain (`rtalk/explain.py`)

### Structure
- **At-a-glance:** one-liner, file count, entry points, user-facing, central modules.
- **Sections:** What, How to run, Start Here, Key Directories, Entry Points, Domains, Architecture.
- **Personas:** `all` | `pm` | `engineer` | `ux` | `researcher` — filter sections by tag.
- **Domains:** Folder-level with imports, centrality, landmarks.
- **AI:** Optional Groq summaries per domain/section when API key provided.
- **Role explanations:** When AI enabled, generates persona-specific AI summaries (PM, Engineer, UX, Researcher) using TOC + report context.

### Architecture narrative
- **How it connects:** Foundation modules (entry layers), core modules, leaf modules. Key flows with `→` direction. AI summary is 2–3 sentences (not generic).

### Data sources
- Index (chunks, symbols)
- Graph (centrality, clusters, import edges)
- README, pyproject.toml, requirements.txt

---

## 8. Impact (`rtalk/impact.py`)

### Single-file mode
- Input: `target_file`, index, graph.
- Compute: dependents (BFS on reverse_deps), affected entrypoints, related tests.
- Risk: weighted sum of dependents (×10), entrypoints (×15), test gap, churn, file size.
- Output: `ImpactReport` with risk score and breakdown.

### Top high-impact mode
- Input: index, graph, `top_n`, optional `folder_filter`.
- For each file (or files in folder): compute impact score = risk × 0.6 + centrality × 50.
- Sort by impact, return top N.
- Optional AI summary of critical areas when API key provided.

---

## 9. Graph (`rtalk/graph.py`)

### Build
- Load index → extract internal import edges (exclude stdlib/third-party).
- Build call graph from symbol references.
- Compute PageRank centrality.
- Cluster by folder (depth auto-picked).
- Output: `nodes`, `import_edges`, `reverse_deps`, `centrality`, `clusters`.

### Use
- Map visualization, impact analysis, schema for Ask/Guide.

### Map UI (web)
- **Folder drill:** Click folder → file-level graph. Capped at 25 files (by centrality) + 15 external deps for large repos.
- **Node panel:** Click file → metadata + AI context via `/node-context` (file-specific RAG).

---

## 10. Node Context (`rtalk/node_context.py`)

### Purpose
File-specific RAG for the Map node panel. When a user clicks a file in the graph, the side panel shows an AI explanation based on that file's actual code — not generic repo overview.

### Flow
1. **File lookup:** Filter index records by `file_path == target` (no query-based search).
2. **Chunk assembly:** Collect all `file_chunk` records for the target, sort by `start_line`.
3. **Symbol extraction:** Get classes/functions from `symbol` records for context.
4. **Prompt:** Send code (up to ~8k chars) + symbols to LLM: "What does this file do? Key responsibilities? Main functions/classes?"
5. **Return:** 2–4 sentence summary.

### Folder mode
- For folders: get top 6 files by centrality, assemble chunks, send to LLM for folder overview.

### Relation to vectorless RAG
- Uses the same index as Ask/Guide/Explain.
- **Different retrieval:** No TF-IDF or TOC search. Direct file-path filter. Guarantees the prompt contains only that file's code.

---

## 11. Token Limits (Groq)

- **Max input:** ~7.5k tokens (30k chars at 4 chars/token).
- **Truncation:** `_truncate_messages_to_fit()` in `groq_client.py` truncates last message if over limit.
- **Max completion:** 1024 tokens (configurable per call).

---

## 12. Data Flow Summary

```
Repo
  → index.py → JSONL (chunks + symbols)
  → graph.py → graph.json (nodes, edges, centrality, clusters)

JSONL + graph
  → toc.py → TOC tree
  → retrieval.py → EvidenceSnippets (LLM nav + TOC + TF-IDF + exact when AI key)
  → generation.py → Phase 2A (LLM select nodes), 2B (extract), 2C (synthesize prompt)

Query + Evidence
  → answer.py → StructuredAnswer (PageIndex-style prompt when Groq)
  → guide.py → GuideReport (RAG + optional Groq)
  → explain.py → ExplainReport (structure + optional Groq)
  → impact.py → ImpactReport / TopImpactReport

File path (Map node click)
  → node_context.py → index filter by file_path → chunks + symbols → LLM → summary
```

---

## 13. Future Considerations

- **Session memory:** Temp context store for chat (last N turns), refreshed when index changes. Would enable follow-up questions ("What about error handling?") without re-sending full context.
