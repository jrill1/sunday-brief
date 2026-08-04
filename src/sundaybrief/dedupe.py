"""Collapse the same real-world event appearing across multiple local feeds."""
from __future__ import annotations

from .models import Event


def dedupe(events: list[Event]) -> list[Event]:
    seen: dict[str, Event] = {}
    for e in events:
        key = e.dedupe_key()
        if key in seen:
            # Keep the first, but remember every feed it showed up in.
            for s in e.sources:
                if s not in seen[key].sources:
                    seen[key].sources.append(s)
            # Prefer a version that carries a URL if the kept one lacks it.
            if not seen[key].url and e.url:
                seen[key].url = e.url
        else:
            seen[key] = e
    return sorted(seen.values(), key=lambda e: (e.start, e.title.lower()))
