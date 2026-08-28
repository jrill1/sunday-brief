"""Collapse the same real-world event appearing across multiple feeds.

Dedupe only merges events that share a title and day AND come from *different*
sources. Two entries in the same calendar are never merged — your own calendar
is authoritative, so same-title repeats there (two appointments at different
times, an all-day banner plus a timed entry) are real and kept. Merging is
reserved for the case it was built for: one town event showing up across several
local feeds at once.
"""
from __future__ import annotations

from .models import Event


def dedupe(events: list[Event]) -> list[Event]:
    kept: list[Event] = []
    index: dict[tuple, list[Event]] = {}
    for e in sorted(events, key=lambda ev: (ev.start, ev.title.lower())):
        key = (e.title.strip().lower(), e.day)
        match = None
        for other in index.get(key, []):
            if e.source not in other.sources:   # same title+day, but a different feed
                match = other
                break
        if match is not None:
            match.sources.append(e.source)
            if not match.url and e.url:
                match.url = e.url
        else:
            kept.append(e)
            index.setdefault(key, []).append(e)
    return sorted(kept, key=lambda ev: (ev.start, ev.title.lower()))