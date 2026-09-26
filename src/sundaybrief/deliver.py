"""Send the finished brief to the family via Pushover."""
from __future__ import annotations

import requests

PUSHOVER_ENDPOINT = "https://api.pushover.net/1/messages.json"


def split_message(text: str, limit: int = 1024) -> list[str]:
    """Split a multi-line message into chunks of at most `limit` chars, on line
    boundaries. A single over-long line is left whole (Pushover will truncate
    it) rather than cut mid-link."""
    chunks: list[str] = []
    current = ""
    for line in text.split("\n"):
        candidate = f"{current}\n{line}" if current else line
        if current and len(candidate) > limit:
            chunks.append(current)
            current = line
        else:
            current = candidate
    if current:
        chunks.append(current)
    return chunks


def send_pushover(
    token: str,
    user: str,
    title: str,
    message: str,
    *,
    url: str = "",
    url_title: str = "",
    timeout: int = 20,
) -> dict:
    payload = {
        "token": token,
        "user": user,
        "title": title[:250],
        "message": message,
        "html": 1,
    }
    if url:
        payload["url"] = url[:512]
        payload["url_title"] = (url_title or "Full brief")[:100]

    resp = requests.post(PUSHOVER_ENDPOINT, data=payload, timeout=timeout)
    resp.raise_for_status()
    return resp.json()
