#!/usr/bin/env bash
set -euo pipefail

REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"
INDEX_PATH="$REPO_DIR/.rtalk/index.jsonl"
GRAPH_PATH="$REPO_DIR/.rtalk/graph.json"

echo "============================================"
echo "  Repo That Talks Back v2 -- Demo"
echo "============================================"
echo ""

echo "[1/5] Indexing this repository..."
python -m rtalk index --repo "$REPO_DIR" --out "$INDEX_PATH"
echo ""

echo "[2/5] Building dependency graph..."
python -m rtalk graph --index "$INDEX_PATH" --out "$GRAPH_PATH"
echo ""

echo "[3/5] Generating repo overview..."
python -m rtalk explain --index "$INDEX_PATH"
echo ""

echo "[4/5] Running guided investigation: explain_search_pipeline..."
python -m rtalk guide --index "$INDEX_PATH" --plan explain_search_pipeline
echo ""

echo "[5/5] Impact analysis on rtalk/retrieval.py..."
python -m rtalk impact --index "$INDEX_PATH" --graph "$GRAPH_PATH" --file rtalk/retrieval.py
echo ""

echo "Done! To start the web UI:"
echo "  python -m rtalk serve"
echo "  Then open http://127.0.0.1:8000"
echo ""
echo "============================================"
