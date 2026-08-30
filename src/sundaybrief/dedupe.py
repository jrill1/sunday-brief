"""Collapse the same real-world event appearing across multiple feeds.

Dedupe only merges events that share a title and day AND come from *different*
sources. Two entries in the same calendar are never merged — your own calendar
is authoritative, so same-title repeats there (two appointments at different
times, an all-day banner plus a timed entry) are real and kept. Merging is
reserved for the case it was built for: one town event showing up across several
local feeds at once.

One exception to "keep whichever copy was scanned first": if a merge finds a
personal-calendar copy of an event that a work calendar also has, personal
wins. Someone cross-posting a plan to their work calendar too doesn't make it
work-related — putting it on their personal calendar at all is the signal that
matters, and it doesn't depend on the order sources.yaml happens to list feeds in.
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
            # A duplicate on someone's personal calendar is a stronger signal
            # than which feed happened to be scanned first: they put it there
            # on purpose, so it's personal even if it's *also* on their work
            # calendar (e.g. a same-day social plan cross-posted to both).
            if e.category == "personal" and match.category != "personal":
                match.category = "personal"
                match.person = e.person or match.person
        else:
            kept.append(e)
            index.setdefault(key, []).append(e)
    return sorted(kept, key=lambda ev: (ev.start, ev.title.lower()))