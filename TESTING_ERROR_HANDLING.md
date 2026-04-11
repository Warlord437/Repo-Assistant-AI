# LLM Error Handling - Integration Testing Guide

## Quick Test Commands

### Test 1: Verify Help Text Includes --ai-key
```bash
python -m rtalk explain --help
# Should show: --ai-key AI_KEY  Groq API key for AI summaries

python -m rtalk guide --help
# Should show: --ai-key AI_KEY  Groq API key for AI synthesis

python -m rtalk impact --help
# Should NOT show --ai-key (impact doesn't pass it in main, only for report)
```

### Test 2: Test with No API Key (Baseline - Should Work)
```bash
# Index a repo first
python -m rtalk index --repo . --out .rtalk/index.jsonl

# Run without AI (should work fine)
python -m rtalk explain --index .rtalk/index.jsonl
python -m rtalk guide --index .rtalk/index.jsonl --query "How does search work?"
```

### Test 3: Test with Valid API Key
```bash
export GROQ_KEY="your-valid-groq-api-key"

# Run with AI
python -m rtalk explain --index .rtalk/index.jsonl --ai-key "$GROQ_KEY"
python -m rtalk guide --index .rtalk/index.jsonl --query "How does search work?" --ai-key "$GROQ_KEY"
python -m rtalk impact --index .rtalk/index.jsonl --ai-key "$GROQ_KEY" --top 10
```

### Test 4: Test with Invalid API Key
```bash
# Should print error message but continue
python -m rtalk explain --index .rtalk/index.jsonl --ai-key "invalid-key-12345"
# Expected stderr: ❌ API Key Error: Invalid Groq API key...
```

### Test 5: Test Rate Limit Handling (Stress Test)
```bash
# Make rapid requests to trigger rate limiting
for i in {1..5}; do
  python -m rtalk guide --index .rtalk/index.jsonl \
    --query "Authentication" \
    --ai-key "$GROQ_KEY" 2>&1 &
done
wait

# Watch for: ⚠️ Rate limit reached messages
```

### Test 6: Test Network Error (Offline)
```bash
# Disconnect network or use timeout
python -m rtalk explain --index .rtalk/index.jsonl --ai-key "$GROQ_KEY"
# Expected stderr: ❌ Connection error with Groq API...
```

## Error Scenarios to Verify

### Scenario 1: Rate Limit (HTTP 429)
**Trigger:** Make 5+ rapid requests
**Expected behavior:**
- Error message: `⚠️ Rate limit reached`
- App continues
- Returns results without AI summary
- Exit code: 0 (success)

**Example output:**
```
⚠️  Rate limit reached: Rate limited by Groq API. Please wait and retry.
   Continuing with lexical search results only.

[Results without AI]
```

### Scenario 2: Invalid API Key (HTTP 401)
**Trigger:** Pass `--ai-key "invalid"`
**Expected behavior:**
- Error message: `❌ API Key Error`
- App continues
- Returns results without AI summary
- Exit code: 0 (success)

**Example output:**
```
❌ API Key Error: Invalid Groq API key. Please check your API key.
```

### Scenario 3: Server Error (HTTP 5xx)
**Trigger:** (Rare - wait for Groq server issue)
**Expected behavior:**
- Error message: `❌ API Error: Groq API error (HTTP 500)`
- App continues
- Returns results without AI summary
- Exit code: 0 (success)

### Scenario 4: Network Error
**Trigger:** Disconnect network before running
**Expected behavior:**
- Error message: `❌ Connection error with Groq API`
- App continues
- Returns results without AI summary
- Exit code: 0 (success)

### Scenario 5: Unexpected Error
**Trigger:** (Should rarely happen)
**Expected behavior:**
- Error message: `❌ Unexpected error: [ErrorType]: [details]`
- App continues
- Returns results without AI summary
- Exit code: 0 (success)

## Verification Checklist

### ✅ Code Quality
- [ ] All Python files compile without syntax errors
- [ ] No import errors
- [ ] Exception classes properly defined
- [ ] All modules import error classes correctly

### ✅ Error Messages
- [ ] Rate limit message uses ⚠️ emoji
- [ ] API error messages use ❌ emoji
- [ ] Messages printed to stderr (not stdout)
- [ ] Messages are user-friendly and actionable

### ✅ Graceful Degradation
- [ ] No AI features don't require --ai-key
- [ ] Invalid key → continues with non-AI results
- [ ] Rate limit → continues with non-AI results
- [ ] Network error → continues with non-AI results

### ✅ Exit Codes
- [ ] Successful run: exit code 0
- [ ] Rate limit: exit code 0 (continues)
- [ ] Invalid key: exit code 0 (continues)
- [ ] Network error: exit code 0 (continues)
- [ ] Missing required args: exit code != 0

### ✅ Functions Updated
- [ ] groq_client.py: `groq_chat()` has try/except
- [ ] explain.py: `_call_groq()` handles errors
- [ ] guide.py: guide generation handles errors
- [ ] impact.py: AI summary handles errors
- [ ] answer.py: `GroqAdapter.generate()` handles errors
- [ ] node_context.py: `explain_file()` handles errors
- [ ] node_context.py: `explain_folder()` handles errors
- [ ] generation.py: `extract_toc_nodes_with_llm()` handles errors

### ✅ Exception Classes
- [ ] `RateLimitError` defined in groq_client.py
- [ ] `APIError` defined in groq_client.py
- [ ] Both inherit from Exception
- [ ] Can be imported by other modules

### ✅ Documentation
- [ ] ERROR_HANDLING.md created
- [ ] LLM_ERROR_HANDLING_SUMMARY.md created
- [ ] API_KEY_FIX_SUMMARY.md updated
- [ ] Comments in code explain error handling

## Debugging Tips

### Check if errors are being caught:
```bash
# Run with verbose output
python -m rtalk explain --index .rtalk/index.jsonl --ai-key "bad" 2>&1 | tee debug.log
grep -E "^❌|^⚠️" debug.log  # Should show error messages
```

### Check module imports:
```bash
python -c "from rtalk.groq_client import RateLimitError, APIError; print('OK')"
```

### Check exception handling:
```python
# Quick test script
from rtalk.groq_client import RateLimitError, APIError

try:
    raise RateLimitError("test")
except RateLimitError as e:
    print(f"Caught: {e}")

try:
    raise APIError("test")
except APIError as e:
    print(f"Caught: {e}")
```

## Known Limitations

1. **Groq SDK version compatibility:**
   - Error handling depends on Groq SDK raising specific exceptions
   - May need adjustment if Groq SDK changes exception hierarchy

2. **Rate limit details:**
   - Exact rate limit values vary by Groq plan
   - No exponential backoff retry (yet)
   - User must manually retry after waiting

3. **Error message localization:**
   - All error messages in English
   - Could be translated if needed

4. **Logging:**
   - Errors printed to stderr only
   - No persistent log file
   - Could add file logging if needed

## Success Criteria

The error handling is working correctly if:

✅ Running command without --ai-key works
✅ Running command with valid --ai-key works
✅ Running command with invalid --ai-key prints error but continues
✅ Rate limit error shows ⚠️ warning
✅ API/network errors show ❌ error
✅ All commands exit with code 0 (even on errors)
✅ Error messages appear in stderr
✅ Non-AI features work regardless of API status
