"""Turn a flat list of events into the *interesting* signals for the week.

This is the bit that makes the brief worth reading: not "here's everything on
your calendar" but "here's the Wednesday half-day with no coverage, and here
are two local things that fit the open Saturday."
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

from .models import Event

CLOSURE_KEYWORDS = (
    "closed", "closure", "no school", "no care", "holiday", "in-service",
    "in service", "staff development", "professional development", "pd day",
)
HALFDAY_KEYWORDS = (
    "half day", "half-day", "early dismissal", "early release",
    "noon dismissal", "early close",
)


@dataclass
class WeekSignals:
    window_start: datetime
    window_end: datetime
    parent_events: list[Event] = field(default_factory=list)
    daycare_events: list[Event] = field(default_factory=list)
    local_events: list[Event] = field(default_factory=list)
    closures: list[Event] = field(default_factory=list)
    half_days: list[Event] = field(default_factory=list)
    focus_days: list[date] = field(default_factory=list)   # closures + weekends
    picks: list[Event] = field(default_factory=list)        # local events on focus days


def _matches(title: str, keywords) -> bool:
    t = title.lower()
    return any(k in t for k in keywords)


def _weekend_days(start: datetime, end: datetime) -> list[date]:
    days, d = [], start.date()
    while d <= end.date():
        if d.weekday() >= 5:  # 5 = Sat, 6 = Sun
            days.append(d)
        d += timedelta(days=1)
    return days


def analyze(events: list[Event], window_start: datetime, window_end: datetime) -> WeekSignals:
    sig = WeekSignals(window_start=window_start, window_end=window_end)

    for e in events:
        if e.category in ("work", "personal"):
            sig.parent_events.append(e)
        elif e.category == "daycare":
            sig.daycare_events.append(e)
        elif e.category == "local":
            sig.local_events.append(e)

    for e in sig.daycare_events:
        if _matches(e.title, CLOSURE_KEYWORDS):
            sig.closures.append(e)
        elif _matches(e.title, HALFDAY_KEYWORDS):
            sig.half_days.append(e)

    closure_days = {e.day for e in sig.closures} | {e.day for e in sig.half_days}
    weekend = set(_weekend_days(window_start, window_end))
    sig.focus_days = sorted(closure_days | weekend)

    # Local events that land on a focus day are the natural suggestions.
    focus_set = set(sig.focus_days)
    sig.picks = sorted(
        (e for e in sig.local_events if e.day in focus_set),
        key=lambda e: e.start,
    )
    return sig
