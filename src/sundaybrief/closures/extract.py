"""The LLM step: one EmailDoc -> list of raw closure facts.

Kept deliberately thin and swappable. `AnthropicExtractor` is production;
anything matching the `Extractor` shape (a callable taking an EmailDoc and
returning a list of dicts) can stand in for offline dev/testing.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

from .reader import EmailDoc

REQUIRED = {"school", "type", "active", "start_date", "end_date", "reason"}


def build_user_block(doc: EmailDoc) -> str:
    parts = [
        f"SENT_DATE: {doc.source_date.isoformat() if doc.source_date else 'unknown'}",
        f"FROM: {doc.source_from}",
        f"SUBJECT: {doc.source_subject}",
        "",
        doc.body,
    ]
    for name, text in doc.attachments:
        parts += ["", f"--- ATTACHMENT: {name} ---", text]
    return "\n".join(parts)


def parse_response(text: str) -> list[dict]:
    """Defensive: strip any fences, load JSON, keep only well-formed items."""
    t = text.strip()
    if t.startswith("```"):
        t = t.strip("`")
        t = t[t.find("["):]  # jump to the array
    try:
        data = json.loads(t)
    except json.JSONDecodeError:
        return []
    if not isinstance(data, list):
        return []
    out = []
    for item in data:
        if isinstance(item, dict) and REQUIRED.issubset(item):
            out.append(item)
    return out


class AnthropicExtractor:
    def __init__(self, client, model: str, prompt_path: str | Path):
        self.client = client
        self.model = model
        self.system_prompt = Path(prompt_path).read_text()

    def __call__(self, doc: EmailDoc) -> list[dict]:
        resp = self.client.messages.create(
            model=self.model,
            max_tokens=8192,
            system=[{
                "type": "text",
                "text": self.system_prompt,
                "cache_control": {"type": "ephemeral"},  # reuse across every email
            }],
            messages=[{"role": "user", "content": build_user_block(doc)}],
        )
        text = "".join(b.text for b in resp.content if b.type == "text")

        if resp.stop_reason == "max_tokens":
            print(f"  WARNING: response truncated at max_tokens for {doc.source_subject!r} "
                  f"— facts are likely missing.", file=sys.stderr)

        items = parse_response(text)
        if not items and text.strip() not in ("[]", ""):
            print(f"  WARNING: could not parse a JSON array from the extractor response for "
                  f"{doc.source_subject!r} ({len(text)} chars) — treating as 0 facts.",
                  file=sys.stderr)
        return items
