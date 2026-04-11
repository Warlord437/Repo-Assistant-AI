"""Groq API client for chat completions. Used by Explain (AI summaries) and Ask (answer synthesis)."""

from __future__ import annotations

import re as _re
import time as _time

GROQ_MODEL = "llama-3.3-70b-versatile"
GROQ_FALLBACK_MODEL = "llama-3.1-8b-instant"
MAX_RETRIES = 2
_RETRY_WAIT_RE = _re.compile(r"try again in (\d+(?:\.\d+)?)s")
_DAILY_LIMIT_RE = _re.compile(r"tokens per day", _re.IGNORECASE)

# ~4 chars per token for mixed text/code; 7500 tokens ≈ 30k chars
CHARS_PER_TOKEN = 4
MAX_INPUT_TOKENS = 7500
MAX_INPUT_CHARS = MAX_INPUT_TOKENS * CHARS_PER_TOKEN


def _truncate_messages_to_fit(
    messages: list[dict[str, str]],
    max_chars: int = MAX_INPUT_CHARS,
) -> list[dict[str, str]]:
    """Aggressively truncate message contents to stay WELL under max_chars limit.
    
    This ensures we NEVER exceed the token limit to prevent rate limiting.
    Strategy: prioritize keeping earlier messages intact, truncate later ones.
    """
    total = sum(len(m.get("content", "") or "") for m in messages)
    
    # If we're already under limit, return as-is
    if total <= max_chars:
        return messages

    # Calculate aggressive truncation marker and buffer
    trunc_marker = "\n\n[... truncated for token limit ...]"
    safety_buffer = 500  # Extra buffer to be safe
    safe_limit = max_chars - len(trunc_marker) - safety_buffer
    
    # Calculate how much space earlier messages take
    others_len = sum(len(m.get("content", "") or "") for m in messages[:-1])
    last_budget = safe_limit - others_len
    
    # If earlier messages already exceed safe limit, aggressively truncate them too
    if others_len > safe_limit:
        out: list[dict[str, str]] = []
        running_total = 0
        for m in messages[:-1]:
            msg_len = len(m.get("content", "") or "")
            if running_total + msg_len <= safe_limit:
                out.append(dict(m))
                running_total += msg_len
            else:
                # Truncate this message
                available = safe_limit - running_total - len(trunc_marker)
                if available > 0:
                    truncated_content = m.get("content", "")[:available] + trunc_marker
                    out.append({**m, "content": truncated_content})
                break
        # Add final message with remaining budget
        final_budget = safe_limit - running_total
        if final_budget > 100:  # Only add if meaningful space left
            final_msg = dict(messages[-1])
            final_content = messages[-1].get("content", "") or ""
            if len(final_content) > final_budget:
                final_msg["content"] = final_content[:final_budget] + trunc_marker
            out.append(final_msg)
        return out
    
    # Normal case: last message is too long, truncate only it
    if last_budget <= 0:
        last_budget = max(100, safe_limit - others_len - len(trunc_marker))

    out = [dict(m) for m in messages]
    last_content = out[-1].get("content", "") or ""
    if len(last_content) > last_budget:
        out[-1] = {**out[-1], "content": last_content[:last_budget] + trunc_marker}
    
    return out


def _parse_retry_wait(error_msg: str, default: float = 15.0) -> float:
    """Extract wait seconds from Groq rate-limit message, e.g. 'try again in 12.44s'."""
    m = _RETRY_WAIT_RE.search(error_msg)
    if m:
        return min(float(m.group(1)) + 1.0, 60.0)
    return default


def _groq_chat_single(client, model: str, messages, max_tokens: int) -> str | None:
    """Try a single model. Returns result string on success, None on daily rate limit."""
    from groq import RateLimitError as GroqRateLimitError, APIConnectionError, APIStatusError

    last_exc: Exception | None = None
    for attempt in range(1 + MAX_RETRIES):
        try:
            completion = client.chat.completions.create(
                model=model,
                messages=messages,
                temperature=1,
                max_completion_tokens=max_tokens,
                top_p=1,
                stream=True,
                stop=None,
            )

            result = ""
            for chunk in completion:
                result += chunk.choices[0].delta.content or ""
            return result.strip()

        except (GroqRateLimitError, APIStatusError) as e:
            status = getattr(e, "status_code", 429)
            is_rate_limit = isinstance(e, GroqRateLimitError) or status == 429
            is_daily = bool(_DAILY_LIMIT_RE.search(str(e)))

            if is_daily:
                return None

            if is_rate_limit and attempt < MAX_RETRIES:
                wait = _parse_retry_wait(str(e))
                _time.sleep(wait)
                last_exc = e
                continue

            if is_rate_limit:
                raise RateLimitError(
                    f"Rate limited by Groq API ({model}) after {attempt + 1} attempt(s). Details: {str(e)}"
                ) from e
            if status == 401:
                raise ValueError(
                    f"Invalid Groq API key. Please check your API key. Details: {str(e)}"
                ) from e
            raise APIError(
                f"Groq API error (HTTP {status}): {str(e)}"
            ) from e

        except APIConnectionError as e:
            raise APIError(
                f"Connection error with Groq API. Check your network and API endpoint. Details: {str(e)}"
            ) from e
        except Exception as e:
            raise APIError(
                f"Unexpected error calling Groq API: {str(e)}"
            ) from e

    raise RateLimitError(f"Rate limited ({model}) after {MAX_RETRIES + 1} attempts") from last_exc


def groq_chat(
    api_key: str,
    messages: list[dict[str, str]],
    max_tokens: int = 1024,
    max_input_tokens: int = MAX_INPUT_TOKENS,
    model: str | None = None,
) -> str:
    """Call Groq chat completions API with automatic model fallback on daily limits.

    Tries the primary model first. If the daily token quota (TPD) is exhausted,
    automatically retries with the fallback model (smaller but higher quota).

    Raises:
        ValueError: If API key is missing or invalid.
        RateLimitError: If rate limited (HTTP 429) on both models.
        APIError: For other API errors (500s, network issues, etc).
    """
    if not api_key or not api_key.strip():
        raise ValueError("Groq API key is required")

    from groq import Groq, RateLimitError as GroqRateLimitError, APIConnectionError, APIStatusError

    max_chars = max_input_tokens * CHARS_PER_TOKEN
    messages = _truncate_messages_to_fit(messages, max_chars=max_chars)

    total_chars = sum(len(m.get("content", "") or "") for m in messages)
    if total_chars > max_chars:
        import sys
        print(
            f"⚠️  Input truncated: {total_chars} chars > {max_chars} char limit ({max_input_tokens} tokens max)",
            file=sys.stderr
        )

    use_model = model or GROQ_MODEL
    client = Groq(api_key=api_key.strip())

    result = _groq_chat_single(client, use_model, messages, max_tokens)
    if result is not None:
        return result

    if use_model != GROQ_FALLBACK_MODEL:
        result = _groq_chat_single(client, GROQ_FALLBACK_MODEL, messages, max_tokens)
        if result is not None:
            return result

    raise RateLimitError(
        f"Rate limited on both {use_model} and {GROQ_FALLBACK_MODEL}. "
        "Daily token quota exhausted. Try again later or upgrade your Groq plan."
    )


class RateLimitError(Exception):
    """Raised when Groq API returns 429 rate limit error."""
    pass


class APIError(Exception):
    """Raised for other Groq API errors."""
    pass
