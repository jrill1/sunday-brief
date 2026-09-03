"""Turn a flat list of events into the *interesting* signals for the week.

This is the bit that makes the brief worth reading: not "here's everything on
your calendar" but "here's the Wednesday half-day with no coverage, and here
are two local things that fit the open Saturday."

Closures can come from two places that need reconciling: the closures ledger
(structured, sourced from school emails/webpages — see docs/closure-ledger.md)
and the calendar itself (a synced daycare feed, or a closure someone hand-typed
onto a personal calendar). The ledger wins when both cover the same (child,
date); a calendar-only closure is kept, not dropped, but flagged unconfirmed
since it may be real signal the ledger hasn't caught up to yet.
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import date, datetime, timedelta

from .closures.ledger import CHILD_BY_SCHOOL, effective_closures, effective_notes, source_link
from .models import Event, to_local

CLOSURE_KEYWORDS = (
    "closed", "closure", "no school", "no care", "holiday", "in-service",
    "in service", "staff development", "professional development", "pd day",
)
HALFDAY_KEYWORDS = (
    "half day", "half-day", "early dismissal", "early release",
    "noon dismissal", "early close",
)

_KNOWN_CHILDREN = {name.lower(): name for name in CHILD_BY_SCHOOL.values()}


@dataclass
class WeekSignals:
    window_start: datetime
    window_end: datetime
    parent_events: list[Event] = field(default_factory=list)
    daycare_events: list[Event] = field(default_factory=list)
    local_events: list[Event] = field(default_factory=list)
    closures: list[Event] = field(default_factory=list)
    half_days: list[Event] = field(default_factory=list)
    notes: list[Event] = field(default_factory=list)         # ledger notes: fees, packing items, etc.
    focus_days: list[date] = field(default_factory=list)     # closures + weekends
    picks: list[Event] = field(default_factory=list)         # local events on focus days


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


def _days_in_window(event: Event, window_start: date, window_end: date) -> list[date]:
    """Every day `event` covers, clipped to [window_start, window_end].

    The only place iCal's exclusive-end DTEND convention needs interpreting —
    a one-day all-day event's DTEND is the *next* day, not the same day.
    """
    last_day = event.day
    if event.all_day and event.end and event.end.date() > event.day:
        last_day = event.end.date() - timedelta(days=1)
    start = max(event.day, window_start)
    end = min(last_day, window_end)
    days, d = [], start
    while d <= end:
        days.append(d)
        d += timedelta(days=1)
    return days


def _scan_calendar_closures(
    events: list[Event], window_start: date, window_end: date
) -> tuple[list[Event], list[Event]]:
    """Find closure/half-day mentions in the calendar itself, exploded to one
    Event per day within the window.

    A multi-day all-day event (e.g. a hand-typed "kid1 school closed"
    banner spanning a week) is broken into individual day-atoms clipped to the
    window: a brief for Aug 29-Sep 5 only needs to know Monday the 31st is
    closed, not that the underlying calendar entry actually started Aug 24.
    Everything downstream — the ledger merge, the display collapse — then
    only ever deals in single days, whatever the source.

    A daycare-category event (a synced daycare feed) is matched directly, same
    as always. Any other category is matched only if the title also names a
    known kid — this is what catches a closure hand-typed onto a personal/work
    calendar (e.g. "kid2 school closed") that would otherwise never be
    flagged, since only daycare-category sources carry a `child` tag.
    """
    closures, half_days = [], []
    for e in events:
        if e.category != "daycare":
            t = e.title.lower()
            name = next((n for n in _KNOWN_CHILDREN if n in t), None)
            if not name:
                continue
            e = replace(e, child=_KNOWN_CHILDREN[name])
        if _matches(e.title, CLOSURE_KEYWORDS):
            bucket = closures
        elif _matches(e.title, HALFDAY_KEYWORDS):
            bucket = half_days
        else:
            continue
        for d in _days_in_window(e, window_start, window_end):
            bucket.append(replace(e, start=to_local(d), end=None))
    return closures, half_days


def _ledger_event(row: dict) -> Event:
    d = date.fromisoformat(row["date"])
    return Event(
        title=row["title"], start=to_local(d), end=None, all_day=True,
        source="closures-ledger", category="daycare",
        child=row.get("child", ""), url=source_link(row), context=row.get("context", ""),
    )


def _collapse_events(events: list[Event]) -> list[Event]:
    """Group consecutive-day events representing the same fact (same child,
    title, source) into one Event spanning the run, so a week-long closure
    displays as one line instead of one per day. Applies uniformly whether the
    underlying facts came from the ledger or the calendar — everything reaching
    this point is already a single-day Event (see _scan_calendar_closures /
    _ledger_event), so grouping is just "consecutive identical days," no
    span/end-date reasoning needed.
    """
    if not events:
        return []
    groups: list[list[Event]] = []
    for ev in sorted(events, key=lambda e: (e.child, e.title, e.source, e.day)):
        if groups:
            prev = groups[-1][-1]
            same_run = (
                ev.child == prev.child and ev.title == prev.title
                and ev.source == prev.source and ev.day == prev.day + timedelta(days=1)
            )
            if same_run:
                groups[-1].append(ev)
                continue
        groups.append([ev])
    collapsed = [g[0] if len(g) == 1 else replace(g[0], end=to_local(g[-1].day)) for g in groups]
    return sorted(collapsed, key=lambda e: e.start)


def days_covered(event: Event) -> set[date]:
    """Every day a *collapsed* event covers (see _collapse_events). Its `.end`
    is inclusive by construction there, so this needs no exclusive-end
    handling — unlike a raw calendar event, this is never ambiguous."""
    last_day = event.end.date() if event.end else event.day
    days, d = set(), event.day
    while d <= last_day:
        days.add(d)
        d += timedelta(days=1)
    return days


def _merge_closures(
    calendar_closures: list[Event],
    calendar_half_days: list[Event],
    ledger_rows: list[dict],
    window_start: date,
    window_end: date,
) -> tuple[list[Event], list[Event], list[Event]]:
    ledger_closed = effective_closures(ledger_rows, window_start, window_end)
    ledger_keys = {(r["child"], r["date"]) for r in ledger_closed}

    closures, half_days = [], []
    for row in ledger_closed:
        ev = _ledger_event(row)
        (half_days if row["type"] == "partial_closure" else closures).append(ev)

    for ev in calendar_closures:
        if (ev.child, ev.day.isoformat()) not in ledger_keys:
            closures.append(replace(ev, title=f"{ev.title} (unconfirmed — not from a school email)"))
    for ev in calendar_half_days:
        if (ev.child, ev.day.isoformat()) not in ledger_keys:
            half_days.append(replace(ev, title=f"{ev.title} (unconfirmed — not from a school email)"))

    notes = [_ledger_event(r) for r in effective_notes(ledger_rows, window_start, window_end)]
    return _collapse_events(closures), _collapse_events(half_days), _collapse_events(notes)


def analyze(
    events: list[Event],
    window_start: datetime,
    window_end: datetime,
    ledger_rows: list[dict] | None = None,
) -> WeekSignals:
    sig = WeekSignals(window_start=window_start, window_end=window_end)

    for e in events:
        if e.category in ("work", "personal"):
            sig.parent_events.append(e)
        elif e.category == "daycare":
            sig.daycare_events.append(e)
        elif e.category == "local":
            sig.local_events.append(e)

    calendar_closures, calendar_half_days = _scan_calendar_closures(
        events, window_start.date(), window_end.date(),
    )

    if ledger_rows is not None:
        sig.closures, sig.half_days, sig.notes = _merge_closures(
            calendar_closures, calendar_half_days, ledger_rows,
            window_start.date(), window_end.date(),
        )
    else:
        sig.closures = _collapse_events(calendar_closures)
        sig.half_days = _collapse_events(calendar_half_days)

    closure_days: set[date] = set()
    for e in sig.closures + sig.half_days:
        closure_days |= days_covered(e)
    weekend = set(_weekend_days(window_start, window_end))
    sig.focus_days = sorted(closure_days | weekend)

    # Local events that land on a focus day are the natural suggestions.
    focus_set = set(sig.focus_days)
    sig.picks = sorted(
        (e for e in sig.local_events if e.day in focus_set),
        key=lambda e: e.start,
    )
    return sig
