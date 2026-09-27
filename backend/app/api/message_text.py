"""Shared helper: LangChain message content (str, or a list of text/other blocks)
flattened to plain text for the API. Never passes through raw HTML or tool-call blocks."""

from collections.abc import Sequence


def content_to_text(content: str | Sequence[object]) -> str:
    if isinstance(content, str):
        return content
    parts: list[str] = []
    for block in content:
        if isinstance(block, str):
            parts.append(block)
        elif isinstance(block, dict) and block.get("type") == "text":
            parts.append(str(block.get("text", "")))
    return "".join(parts)
