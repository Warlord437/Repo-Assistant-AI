"""Text sanitization for display: strip HTML, markdown images, normalize whitespace."""

from __future__ import annotations

import re


def strip_html(text: str) -> str:
    """Remove HTML tags, keep alt text where possible, collapse whitespace."""
    if not text:
        return ""

    # Extract alt text from img tags before stripping
    def _replace_img(m: re.Match[str]) -> str:
        alt = m.group(1) or ""
        return alt.strip()

    text = re.sub(r"<img[^>]*alt\s*=\s*[\"']([^\"']*)[\"'][^>]*>", _replace_img, text, flags=re.I)
    text = re.sub(r"<img[^>]*>", "", text, flags=re.I)

    # Remove all remaining HTML tags
    text = re.sub(r"<[^>]+>", " ", text)

    # Decode common entities
    text = text.replace("&nbsp;", " ")
    text = text.replace("&amp;", "&")
    text = text.replace("&lt;", "<")
    text = text.replace("&gt;", ">")
    text = text.replace("&quot;", '"')
    text = text.replace("&#39;", "'")

    # Collapse whitespace
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def strip_markdown_images(text: str) -> str:
    """Remove markdown image lines: ![alt](url) and <img ...>."""
    if not text:
        return ""

    # Remove ![alt](url) style
    text = re.sub(r"!\[[^\]]*\]\([^)]*\)\s*\n?", "\n", text)

    # Remove <img ...> tags (already handled in strip_html but keep for standalone use)
    text = re.sub(r"<img[^>]*>", "", text, flags=re.I)

    return text


def normalize_lines(text: str) -> str:
    """Trim each line and deduplicate blank lines."""
    if not text:
        return ""

    lines = [line.strip() for line in text.split("\n")]
    result: list[str] = []
    prev_blank = False

    for line in lines:
        is_blank = not line
        if is_blank and prev_blank:
            continue
        result.append(line)
        prev_blank = is_blank

    return "\n".join(result).strip()
