from __future__ import annotations

import json
from typing import Any


class ChatContentError(ValueError):
    pass


def message_text(message: dict[str, Any]) -> str:
    """Normalize OpenAI-compatible assistant content to plain text.

    Text-only endpoints usually return a string. Multimodal/OpenAI-compatible
    servers may return an array of text parts instead. We accept only textual
    parts and never reinterpret reasoning/tool payloads as final answer content.
    """
    content = message.get("content")
    if isinstance(content, str):
        text = content
    elif isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, str):
                parts.append(item)
                continue
            if not isinstance(item, dict):
                continue
            value = item.get("text")
            if isinstance(value, str):
                parts.append(value)
            elif isinstance(value, dict) and isinstance(value.get("value"), str):
                parts.append(value["value"])
        text = "".join(parts)
    elif isinstance(content, dict):
        value = content.get("text")
        if isinstance(value, str):
            text = value
        elif isinstance(value, dict) and isinstance(value.get("value"), str):
            text = value["value"]
        else:
            raise ChatContentError("assistant_content_not_text")
    else:
        raise ChatContentError("assistant_content_missing")

    text = text.strip()
    if not text:
        raise ChatContentError("assistant_content_empty")
    return text


def json_text(message: dict[str, Any]) -> str:
    """Return JSON text, tolerating a single Markdown JSON fence."""
    text = message_text(message).strip()
    fence = chr(96) * 3
    if text.startswith(fence):
        lines = text.splitlines()
        if len(lines) >= 3 and lines[-1].strip() == fence:
            first = lines[0].strip().casefold()
            if first in {fence, fence + "json", fence + "javascript", fence + "js"}:
                text = "\n".join(lines[1:-1]).strip()
    return text


def json_object(message: dict[str, Any]) -> dict[str, Any]:
    parsed = json.loads(json_text(message))
    if not isinstance(parsed, dict):
        raise ChatContentError("assistant_json_not_object")
    return parsed
