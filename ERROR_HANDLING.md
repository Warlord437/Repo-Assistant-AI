# LLM Error Handling & Rate Limiting

## Overview

The codebase now includes comprehensive error handling for Groq API calls to gracefully handle:
- ✅ Rate limiting (HTTP 429)
- ✅ Authentication errors (HTTP 401 - invalid API key)
- ✅ Server errors (HTTP 5xx)
- ✅ Network/connection errors
- ✅ Unexpected exceptions

## Error Types Handled

### 1. **Rate Limiting (HTTP 429)**
**What happens:** Groq API has rate limits on requests/tokens per minute/hour.
**Error raised:** `RateLimitError`
**User experience:** 
- ⚠️ Warning message printed to stderr: `"Rate limit reached: ..."`
- ✅ Application continues gracefully
- ✅ Returns empty string or fallback behavior (no AI summary)

### 2. **Invalid API Key (HTTP 401)**
**What happens:** Provided API key is invalid or expired.
**Error raised:** `ValueError` with descriptive message
**User experience:**
- ❌ Error message: `"API Key Error: Invalid Groq API key..."`
- ✅ Application continues (no crash)
- ✅ Works with non-AI features

### 3. **Server Errors (HTTP 5xx)**
**What happens:** Groq API server is experiencing issues
**Error raised:** `APIError`
**User experience:**
- ❌ Error message: `"API Error: Groq API error (HTTP 500): ..."`
- ✅ Application continues gracefully
- ✅ Falls back to lexical/non-AI results

### 4. **Network Errors**
**What happens:** Connection timeout, DNS failure, etc.
**Error raised:** `APIError`
**User experience:**
- ❌ Error message: `"Connection error with Groq API..."`
- ✅ Application continues
- ✅ Lexical search still available

### 5. **Unexpected Errors**
**What happens:** Any other exception
**Error raised:** `APIError`
**User experience:**
- ❌ Error message: `"Unexpected error: [ErrorType]: [details]"`
- ✅ Application continues
- ✅ Graceful degradation

## Affected Commands & Modules

### Commands with Enhanced Error Handling:
1. **`explain`** - AI summaries for domains, sections, role explanations
2. **`guide`** - AI-powered step-by-step guides
3. **`impact`** - AI summaries of high-impact files
4. **`ask`** - AI answer synthesis (via GroqAdapter)

### Modules Updated:
- `groq_client.py` - Core API client with exception mapping
- `explain.py` - `_call_groq()` function
- `guide.py` - Guide generation with AI fallback
- `impact.py` - Impact analysis summaries
- `answer.py` - `GroqAdapter.generate()` method
- `node_context.py` - File/folder explanations
- `generation.py` - LLM tree search node extraction

## Behavior Flow

```
User runs command with --ai-key
    ↓
groq_chat() called with API key
    ↓
Try to call Groq API
    ↓
    ├─ Success → Return AI response
    │
    └─ Error → Catch specific exception
        ├─ RateLimitError → Print warning, return empty/fallback
        ├─ ValueError (auth) → Print error, return empty/fallback
        ├─ APIError (server/network) → Print error, return empty/fallback
        └─ Other → Print error, return empty/fallback
    ↓
Application continues with non-AI results
```

## Error Messages

### Rate Limit Message
```
⚠️  Rate limit reached: Rate limited by Groq API. Please wait and retry.
   Continuing with lexical search results only.
```

### Invalid API Key Message
```
❌ API Key Error: Invalid Groq API key. Please check your API key.
```

### Server Error Message
```
❌ API Error: Groq API error (HTTP 500): Internal Server Error
```

### Network Error Message
```
❌ Connection error with Groq API. Check your network and API endpoint.
```

## Graceful Degradation

All commands that use AI features gracefully degrade when the API fails:

| Command | Without AI | With AI (failed) |
|---------|-----------|-----------------|
| `explain` | Basic structure only | Basic + attempted summaries (skipped on error) |
| `guide` | Evidence list only | Evidence + steps (skipped, shows evidence) |
| `impact` | Files + risk scores | Files + risk scores + summary (skipped) |
| `ask` | Keyword matching + heuristics | + AI synthesis (skipped on error) |

## Testing Rate Limits

To test rate limit handling:

```bash
# Make rapid requests to trigger rate limiting
for i in {1..10}; do
  python -m rtalk guide --index .rtalk/index.jsonl \
    --query "How does authentication work?" \
    --ai-key "your-api-key" &
done
wait
```

You should see:
- ⚠️ Rate limit warnings in stderr
- ✅ Commands continue and complete with fallback results

## Configuration

The rate limiting behavior is handled entirely by the Groq SDK. You cannot configure limits in this codebase, but you can:

1. **Reduce API calls:** Use lexical search only (no `--ai-key`)
2. **Batch operations:** Wait between multiple commands with AI
3. **Check Groq limits:** Visit your Groq API dashboard
4. **Upgrade plan:** Groq may have higher limits for paid accounts

## API Key Validation

API keys are validated in `groq_client.py`:

```python
if not api_key or not api_key.strip():
    raise ValueError("Groq API key is required")
```

If you see this error:
- Ensure you're passing `--ai-key "your-actual-key"`
- Check for extra spaces: `key.strip()`
- Verify the key isn't empty: `bool(api_key)`

## Future Improvements

Potential enhancements:
- [ ] Exponential backoff retry logic for rate limits
- [ ] Request queuing system
- [ ] Caching of AI responses
- [ ] Fallback to alternative LLM providers
- [ ] Request logging for debugging
- [ ] Rate limit header parsing (`X-RateLimit-*`)

## Debugging

To see detailed error information:

```bash
# Enable stderr output to see error messages
python -m rtalk explain --index .rtalk/index.jsonl --ai-key "key" 2>&1 | cat

# Check if it's a network issue
ping api.groq.com  # (Groq may use different domain)

# Validate API key format
echo "Your key:" $GROQ_API_KEY
```
