# Code Changes Reference

## Files Modified

### 1. `.gitignore` (NEW)
- Created to exclude `.rtalk/` and other temporary directories
- Prevents cache from being committed to git

### 2. `rtalk/__main__.py`
**Changes:**
- Line 55: Added `exp.add_argument("--ai-key", default=None, help="Groq API key for AI summaries")`
- Lines 119-127: Updated explain handler to pass `use_ai_summary` and `ai_api_key` parameters
- Lines 163-167: Added try/except for uvicorn import in serve command

**Key sections:**
```python
# Before
exp.add_argument("--repo", default=None, help="Repo path for tech stack extraction")

# After
exp.add_argument("--repo", default=None, help="Repo path for tech stack extraction")
exp.add_argument("--ai-key", default=None, help="Groq API key for AI summaries")

# Before
report = summarize_repo(args.index, repo_path=args.repo)

# After
report = summarize_repo(
    args.index,
    repo_path=args.repo,
    use_ai_summary=bool(args.ai_key),
    ai_api_key=args.ai_key,
)
```

### 3. `rtalk/groq_client.py`
**Changes:**
- Imported additional Groq SDK exceptions: `RateLimitError`, `APIStatusError`, `APIConnectionError`
- Enhanced `groq_chat()` function with structured error handling
- Added two new exception classes: `RateLimitError` and `APIError`

**Key sections:**
```python
# Added exception classes
class RateLimitError(Exception):
    """Raised when Groq API returns 429 rate limit error."""
    pass

class APIError(Exception):
    """Raised for other Groq API errors."""
    pass

# Enhanced groq_chat with exception handling
try:
    completion = client.chat.completions.create(...)
except RateLimitError as e:
    raise RateLimitError(f"Rate limited...") from e
except APIStatusError as e:
    if e.status_code == 401:
        raise ValueError(f"Invalid API key...") from e
    elif e.status_code == 429:
        raise RateLimitError(...) from e
    else:
        raise APIError(...) from e
except APIConnectionError as e:
    raise APIError(f"Connection error...") from e
```

### 4. `rtalk/explain.py`
**Changes:**
- Enhanced `_call_groq()` function to catch specific exceptions and print error messages

**Key sections:**
```python
# Before
def _call_groq(api_key: str, prompt: str, max_tokens: int = 150) -> str:
    try:
        from rtalk.groq_client import groq_chat
        return groq_chat(...)
    except Exception:
        return ""

# After
def _call_groq(api_key: str, prompt: str, max_tokens: int = 150) -> str:
    try:
        from rtalk.groq_client import groq_chat, RateLimitError, APIError
        return groq_chat(...)
    except RateLimitError as e:
        import sys
        print(f"⚠️  API Rate Limit: {str(e)}", file=sys.stderr)
        print("   Please wait a moment and retry.", file=sys.stderr)
        return ""
    except ValueError as e:
        import sys
        print(f"❌ API Key Error: {str(e)}", file=sys.stderr)
        return ""
    # ... more specific error handling
```

### 5. `rtalk/guide.py`
**Changes:**
- Updated error handling in guide generation to catch specific exceptions
- Added detailed error messages with user guidance

**Key sections:**
```python
# Before
try:
    ai_text = groq_chat(...)
    if ai_text:
        ...
except Exception:
    pass

# After
try:
    ai_text = groq_chat(...)
    if ai_text:
        ...
except RateLimitError as e:
    import sys
    print(f"⚠️  Rate limit reached: {str(e)}", file=sys.stderr)
    print("   Continuing with lexical search results only.", file=sys.stderr)
except (ValueError, APIError) as e:
    import sys
    print(f"❌ API Error: {str(e)}", file=sys.stderr)
    print("   Continuing with lexical search results only.", file=sys.stderr)
```

### 6. `rtalk/impact.py`
**Changes:**
- Enhanced AI summary generation with specific error handling
- Imports `RateLimitError` and `APIError` from groq_client

**Key sections:**
```python
# Before
try:
    from rtalk.groq_client import groq_chat
    ai_summary = groq_chat(...)
except Exception:
    pass

# After
try:
    from rtalk.groq_client import groq_chat, RateLimitError, APIError
    ai_summary = groq_chat(...)
except RateLimitError as e:
    import sys
    print(f"⚠️  Rate limit reached: {str(e)}", file=sys.stderr)
except (ValueError, APIError) as e:
    import sys
    print(f"❌ API Error: {str(e)}", file=sys.stderr)
```

### 7. `rtalk/answer.py`
**Changes:**
- Enhanced `GroqAdapter.generate()` method with specific error handling
- Imports error classes and provides detailed error messages

**Key sections:**
```python
# Before
def generate(self, prompt: str, max_tokens: int = 512) -> str:
    if not self.api_key:
        return ""
    try:
        from rtalk.groq_client import groq_chat
        return groq_chat(...)
    except Exception:
        return ""

# After
def generate(self, prompt: str, max_tokens: int = 512) -> str:
    if not self.api_key:
        return ""
    try:
        from rtalk.groq_client import groq_chat, RateLimitError, APIError
        return groq_chat(...)
    except RateLimitError as e:
        import sys
        print(f"⚠️  Rate limit reached: {str(e)}", file=sys.stderr)
        return ""
    # ... more specific error handling
```

### 8. `rtalk/node_context.py`
**Changes:**
- Enhanced both `explain_file()` and `explain_folder()` with specific error handling
- Returns error message in tuple for caller to handle

**Key sections:**
```python
# Before (both functions)
try:
    from rtalk.groq_client import groq_chat
    result = groq_chat(...)
    return (result or "", None)
except Exception as e:
    return "", str(e)

# After (both functions)
try:
    from rtalk.groq_client import groq_chat, RateLimitError, APIError
    result = groq_chat(...)
    return (result or "", None)
except RateLimitError as e:
    return "", f"Rate limit: {str(e)}"
except ValueError as e:
    return "", f"API key error: {str(e)}"
except APIError as e:
    return "", f"API error: {str(e)}"
except Exception as e:
    return "", f"{type(e).__name__}: {str(e)}"
```

### 9. `rtalk/generation.py`
**Changes:**
- Enhanced `extract_toc_nodes_with_llm()` with specific error handling
- Prints error messages to stderr and returns empty list on failure

**Key sections:**
```python
# Before
try:
    from rtalk.groq_client import groq_chat
    raw = groq_chat(...)
    # parse and return
except Exception:
    return []

# After
try:
    from rtalk.groq_client import groq_chat, RateLimitError, APIError
    raw = groq_chat(...)
    # parse and return
except RateLimitError as e:
    import sys
    print(f"⚠️  Rate limit reached: {str(e)}", file=sys.stderr)
    return []
except (ValueError, APIError) as e:
    import sys
    print(f"❌ API Error: {str(e)}", file=sys.stderr)
    return []
```

## Summary of Error Handling Pattern

All modules now follow this pattern:

```python
# 1. Import error classes
from rtalk.groq_client import groq_chat, RateLimitError, APIError

# 2. Try the API call
try:
    result = groq_chat(api_key, messages, max_tokens)
    # Success path
except RateLimitError as e:
    # Handle rate limit specifically
    import sys
    print(f"⚠️  Rate limit reached: {str(e)}", file=sys.stderr)
    return fallback_value
except ValueError as e:
    # Handle API key errors
    import sys
    print(f"❌ API Key Error: {str(e)}", file=sys.stderr)
    return fallback_value
except APIError as e:
    # Handle server/network errors
    import sys
    print(f"❌ API Error: {str(e)}", file=sys.stderr)
    return fallback_value
except Exception as e:
    # Fallback for unexpected errors
    import sys
    print(f"❌ Unexpected error: {type(e).__name__}: {str(e)}", file=sys.stderr)
    return fallback_value

# 3. Return fallback (empty string, empty list, tuple with error, etc.)
```

## Testing Changes

All syntax changes verified with:
```bash
python -m py_compile rtalk/groq_client.py rtalk/explain.py \
  rtalk/guide.py rtalk/impact.py rtalk/answer.py \
  rtalk/node_context.py rtalk/generation.py
```

✅ All files compile without errors
