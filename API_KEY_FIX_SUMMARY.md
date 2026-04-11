# API Key Fix Summary

## Issues Fixed

### 1. ✅ Created `.gitignore`
- Added `.gitignore` file to exclude temporary/cache directories:
  - `.rtalk/` (generated cache and project files)
  - `__pycache__/`, `*.pyc` (Python cache)
  - `.venv/`, `venv/`, `env/` (Virtual environments)
  - `.vscode/`, `.idea/` (IDE directories)
  - `.DS_Store`, `.env` (OS/environment files)

### 2. ✅ Fixed `explain` Command API Key Handling

**Problem:** The `explain` command was not accepting or passing API keys to enable AI summaries.

**Root Cause:**
- The argument parser didn't have `--ai-key` argument defined
- The command handler wasn't passing the API key to `summarize_repo()`
- The `summarize_repo()` function has parameters for `use_ai_summary` and `ai_api_key`, but they weren't being used

**Solution:**
Added the following changes to `/rtalk/__main__.py`:

1. **Added `--ai-key` argument to the explain subparser** (line 55):
   ```python
   exp.add_argument("--ai-key", default=None, help="Groq API key for AI summaries")
   ```

2. **Updated the explain command handler** (lines 119-127) to pass the API key:
   ```python
   elif args.command == "explain":
       from rtalk.explain import summarize_repo

       report = summarize_repo(
           args.index,
           repo_path=args.repo,
           use_ai_summary=bool(args.ai_key),  # Enable AI if key provided
           ai_api_key=args.ai_key,             # Pass the API key
       )
       print(report.render_text())
   ```

## How to Use

Now you can use the explain command with AI summaries:

```bash
# Without AI (no summaries)
python -m rtalk explain --index .rtalk/index.jsonl

# With AI summaries
python -m rtalk explain --index .rtalk/index.jsonl --ai-key "your-groq-api-key"

# With repo path and AI
python -m rtalk explain --index .rtalk/index.jsonl --repo . --ai-key "your-groq-api-key"
```

## Verification

✅ The `--ai-key` argument now appears in `rtalk explain --help`

## Notes on API Key Storage

The codebase **does NOT store API keys anywhere**:
- No hardcoded API keys found
- No environment variable fallbacks that might be stale
- API keys must be passed explicitly via `--ai-key` argument
- This is secure and follows best practices

The `groq_client.py` validates that an API key is provided and raises `ValueError` if empty:
```python
if not api_key or not api_key.strip():
    raise ValueError("Groq API key is required")
```
