"""Groq API client for chat completions. Used by Explain (AI summaries) and Ask (answer synthesis)."""

from __future__ import annotations

GROQ_MODEL = "llama-3.3-70b-versatile"

# ~4 chars per token for mixed text/code; 7500 tokens ≈ 30k chars
CHARS_PER_TOKEN = 4
MAX_INPUT_TOKENS = 7500
MAX_INPUT_CHARS = MAX_INPUT_TOKENS * CHARS_PER_TOKEN


def _truncate_messages_to_fit(
    messages: list[dict[str, str]],
    max_chars: int = MAX_INPUT_CHARS,
) -> list[dict[str, str]]:
    """Truncate message contents so total input stays under max_chars (~7.5k tokens)."""
    total = sum(len(m.get("content", "") or "") for m in messages)
    if total <= max_chars:
        return messages

    trunc_marker = "\n\n[... truncated for token limit ...]"
    others_len = sum(len(m.get("content", "") or "") for m in messages[:-1])
    last_budget = max_chars - others_len - len(trunc_marker)
    if last_budget <= 0:
        # Earlier messages exceed budget; cap last message to fit
        last_budget = max(200, max_chars - others_len - len(trunc_marker))

    out = [dict(m) for m in messages]
    last_content = out[-1].get("content", "") or ""
    if len(last_content) > last_budget:
        out[-1] = {**out[-1], "content": last_content[:last_budget] + trunc_marker}
    return out


def groq_chat(
    api_key: str,
    messages: list[dict[str, str]],
    max_tokens: int = 1024,
    max_input_tokens: int = MAX_INPUT_TOKENS,
) -> str:
    """Call Groq chat completions API. Returns the assistant message content or raises on error.
    Input is truncated to max_input_tokens (~7.5k) to keep total payload under ~12k tokens."""
    if not api_key or not api_key.strip():
        raise ValueError("Groq API key is required")

    from groq import Groq

    max_chars = max_input_tokens * CHARS_PER_TOKEN
    messages = _truncate_messages_to_fit(messages, max_chars=max_chars)

    client = Groq(api_key=api_key.strip())
    completion = client.chat.completions.create(
        model=GROQ_MODEL,
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
