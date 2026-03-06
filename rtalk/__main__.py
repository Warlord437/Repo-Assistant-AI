"""Entry point for `python -m rtalk`."""

from __future__ import annotations

import argparse
import sys


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        prog="rtalk",
        description="Repo That Talks Back -- interactive repo assistant",
    )
    sub = parser.add_subparsers(dest="command")

    # --- index ---
    idx = sub.add_parser("index", help="Index a repository (local path or GitHub URL)")
    idx.add_argument(
        "--repo",
        required=True,
        help="Local path or GitHub URL (e.g. https://github.com/owner/repo)",
    )
    idx.add_argument(
        "--out",
        default=".rtalk/index.jsonl",
        help="Output path for the index file (default: .rtalk/index.jsonl)",
    )
    idx.add_argument(
        "--chunk-size",
        type=int,
        default=80,
        help="Lines per chunk (default: 80)",
    )
    idx.add_argument(
        "--refresh",
        action="store_true",
        help="Re-clone if repo is a GitHub URL (ignore cache)",
    )

    # --- graph ---
    gr = sub.add_parser("graph", help="Build the repo dependency graph")
    gr.add_argument("--index", default=".rtalk/index.jsonl", help="Path to index")
    gr.add_argument("--out", default=".rtalk/graph.json", help="Output graph JSON path")

    # --- ask ---
    ask = sub.add_parser("ask", help="Ask a question about the repo")
    ask.add_argument("--index", default=".rtalk/index.jsonl", help="Path to index")
    ask.add_argument("query", help="Your question")
    ask.add_argument("--top-k", type=int, default=5, help="Number of evidence snippets")

    # --- explain ---
    exp = sub.add_parser("explain", help="Generate a structured repo overview")
    exp.add_argument("--index", default=".rtalk/index.jsonl", help="Path to index")
    exp.add_argument("--repo", default=None, help="Repo path for tech stack extraction")

    # --- guide ---
    gd = sub.add_parser("guide", help="Run a guided investigation (query-driven RAG)")
    gd.add_argument("--index", default=".rtalk/index.jsonl", help="Path to index")
    gd.add_argument("--query", required=True, help="Topic to investigate (e.g. 'How does authentication work?')")
    gd.add_argument("--repo", default=None, help="Repo path")
    gd.add_argument("--ai-key", default=None, help="Groq API key for AI synthesis")

    # --- impact ---
    imp = sub.add_parser("impact", help="Analyze change impact for a file or list top high-impact files")
    imp.add_argument("--index", default=".rtalk/index.jsonl", help="Path to index")
    imp.add_argument("--graph", default=".rtalk/graph.json", help="Path to graph JSON")
    imp.add_argument("--file", default=None, help="Target file (omit for top-impact mode)")
    imp.add_argument("--repo", default=None, help="Repo path for git churn data")
    imp.add_argument("--top", type=int, default=15, help="Top N high-impact files (when --file omitted)")
    imp.add_argument("--folder", default=None, help="Filter by folder (e.g. rtalk)")

    # --- serve ---
    sub.add_parser("serve", help="Start the web UI server")

    args = parser.parse_args(argv)

    if args.command == "index":
        from rtalk.clone import is_github_url, resolve_repo
        from rtalk.index import build_index

        repo_path = args.repo
        if is_github_url(repo_path):
            print("Detected GitHub URL, cloning...")
            repo_path = resolve_repo(repo_path, refresh=args.refresh)
            print(f"Cloned to {repo_path}")

        build_index(repo_path=repo_path, out_path=args.out, chunk_size=args.chunk_size)

    elif args.command == "graph":
        from rtalk.graph import build_graph

        build_graph(index_path=args.index, out_path=args.out)

    elif args.command == "ask":
        from rtalk.retrieval import RetrievalEngine, detect_explain_intent
        from rtalk.answer import answer_question
        from rtalk.explain import summarize_repo

        if detect_explain_intent(args.query):
            report = summarize_repo(args.index)
            print(report.render_text())
        else:
            engine = RetrievalEngine.from_index(args.index)
            evidence = engine.search(args.query, top_k=args.top_k)
            result = answer_question(args.query, evidence)
            print(result.render_text())

    elif args.command == "explain":
        from rtalk.explain import summarize_repo

        report = summarize_repo(args.index, repo_path=args.repo)
        print(report.render_text())

    elif args.command == "guide":
        from rtalk.guide import run_guide

        report = run_guide(
            query=args.query,
            index_path=args.index,
            repo_path=args.repo,
            ai_api_key=args.ai_key,
        )
        print(report.render_text())

    elif args.command == "impact":
        from rtalk.impact import analyze_impact, get_top_impact_files

        if args.file:
            report = analyze_impact(
                target_file=args.file,
                index_path=args.index,
                graph_path=args.graph,
                repo_path=args.repo,
            )
            print(report.render_text())
        else:
            top_report = get_top_impact_files(
                index_path=args.index,
                graph_path=args.graph,
                repo_path=args.repo,
                top_n=args.top,
                folder_filter=args.folder,
            )
            for e in top_report.entries:
                print(f"{e.file}: risk {e.risk_score:.0f}, deps={e.dependents_count}, eps={e.entrypoints_count}")

    elif args.command == "serve":
        import uvicorn

        uvicorn.run("rtalk.server:app", host="127.0.0.1", port=8000, reload=True)

    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
