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

Exact title+day matching misses the same LOCAL event worded differently by two
different aggregator sources (e.g. "Late Summer Streets in Maplewood" vs
"Maplewood Village Summer Streets") — plain text-similarity can't reliably
tell that apart from two genuinely different events sharing a category word
(two different towns' farmers markets both saying "Farmers Market" scored
almost identically to the real match in testing), so an optional second pass
asks an LLM instead, batched one call per day rather than per pair.
"""
from __future__ import annotations

import re

from .models import Event


def dedupe(events: list[Event], model: str | None = None, api_key: str | None = None) -> list[Event]:
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

    if api_key:
        kept = _llm_dedupe_local(kept, model or "claude-sonnet-5", api_key)

    return sorted(kept, key=lambda ev: (ev.start, ev.title.lower()))


def _llm_dedupe_local(events: list[Event], model: str, api_key: str) -> list[Event]:
    """Semantic second pass, LOCAL category only (the lowest-stakes category,
    and the one where different aggregator sources actually collide) — asks
    the model to group same-day titles describing the same real event,
    batched once per day rather than once per pair. Returns `events`
    unchanged (including on any failure) if anthropic isn't installed or the
    call errors, same fail-soft posture as the rest of the LLM-optional
    pipeline.
    """
    try:
        from anthropic import Anthropic
    except ImportError:
        return events

    by_day: dict = {}
    for e in events:
        if e.category == "local":
            by_day.setdefault(e.day, []).append(e)

    to_drop: set[int] = set()
    client = Anthropic(api_key=api_key)
    for day_events in by_day.values():
        if len(day_events) < 2 or len({e.source for e in day_events}) < 2:
            continue
        numbered = list(enumerate(day_events, 1))
        listing = "\n".join(f"{n}. {e.title}" for n, e in numbered)
        prompt = (
            "Below is a list of numbered local-event titles, all happening on "
            "the same day, from different sources. Some may describe the SAME "
            "real-world event worded differently by different sources (e.g. "
            '"Late Summer Streets in Maplewood" and "Maplewood Village Summer '
            'Streets" are the same town event, just worded differently) — '
            "group those together. Most are genuinely different events and "
            "should NOT be grouped, even if they share words (e.g. two "
            "DIFFERENT towns' farmers markets are NOT the same event, even "
            'though both say "Farmers Market").\n\n'
            "Output ONLY groups of 2+ matching numbers, comma-separated, one "
            "group per line, nothing else. If nothing matches, output nothing "
            "at all.\n\n"
            f"{listing}"
        )
        try:
            resp = client.messages.create(
                model=model, max_tokens=300, thinking={"type": "disabled"},
                messages=[{"role": "user", "content": prompt}],
            )
            raw = "".join(b.text for b in resp.content if b.type == "text").strip()
        except Exception:
            continue

        by_num = dict(numbered)
        for line in raw.splitlines():
            nums = dict.fromkeys(int(n) for n in re.findall(r"\d+", line) if int(n) in by_num)
            group = [by_num[n] for n in nums]
            if len(group) < 2:
                continue
            winner, *losers = group
            for loser in losers:
                if id(loser) in to_drop:
                    continue
                winner.sources.append(loser.source)
                if not winner.url and loser.url:
                    winner.url = loser.url
                to_drop.add(id(loser))

    return [e for e in events if id(e) not in to_drop]
