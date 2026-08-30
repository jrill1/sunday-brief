"""iCal ingestion — the backbone of the whole system.

Every clean source flows through here: the four parent Google calendars (via
their secret .ics URLs), the Brightwheel feed if the center enabled personal-
calendar sync, the Goddard term calendar you generate once and drop on disk,
and any LibCal / municipal feed that offers iCal.

Recurring events matter a lot for parent calendars (standing standups, weekly
piano). Google's secret feed ships RRULEs, not expanded instances, so we use
`recurring_ical_events` to materialize concrete occurrences inside the target
week.
"""
from __future__ import annotations

import base64
import re
import urllib.parse
from datetime import datetime
from pathlib import Path

import recurring_ical_events
import requests
from icalendar import Calendar

from ..models import Event, to_local


def _calendar_id_from_url(url: str) -> str | None:
    """Best-effort extraction of the calendar's own id (its owner's email,
    URL-encoded into Google's secret iCal URL) so we can build per-event
    deep-links. Google-specific; returns None for anything else (a local
    .ics file, a non-Google feed) so link-building is just skipped rather
    than guessed at.
    """
    m = re.search(r"calendar\.google\.com/calendar/ical/([^/]+)/", url)
    return urllib.parse.unquote(m.group(1)) if m else None


def _event_link(calendar_id: str | None, uid) -> str:
    """A Google Calendar deep-link to one event (calendar.google.com/.../event
    ?eid=<base64 of "event_id calendar_id">), or "" if there isn't enough to
    build one from — a non-Google source, or a UID that isn't Google's.

    For a recurring event this links to the series (Google's UID doesn't
    distinguish occurrences the way a precise per-instance link would need),
    not the one specific date's instance — good enough to be useful, not
    guaranteed to land on the exact day.
    """
    if not calendar_id or not uid:
        return ""
    uid = str(uid)
    if "@google.com" not in uid:
        return ""
    event_id = uid.split("@")[0]
    eid = base64.urlsafe_b64encode(f"{event_id} {calendar_id}".encode()).decode().rstrip("=")
    return f"https://calendar.google.com/calendar/event?eid={eid}"


def _read_source(url: str, timeout: int = 30) -> str:
    """Accept an http(s) URL or a local path / file:// URL (for the Goddard .ics)."""
    if url.startswith(("http://", "https://")):
        headers = {"User-Agent": "sunday-brief/0.1 (personal calendar aggregator)"}
        resp = requests.get(url, timeout=timeout, headers=headers)
        resp.raise_for_status()
        return resp.text
    path = Path(url.removeprefix("file://"))
    return path.read_text()


def ingest_ical(
    source: dict,
    window_start: datetime,
    window_end: datetime,
    timeout: int = 30,
) -> list[Event]:
    raw = _read_source(source["url"], timeout=timeout)
    cal = Calendar.from_ical(raw)
    calendar_id = _calendar_id_from_url(source["url"])

    # Expand recurrences into concrete occurrences within the window.
    occurrences = recurring_ical_events.of(cal).between(window_start, window_end)

    events: list[Event] = []
    for comp in occurrences:
        summary = str(comp.get("summary", "")).strip() or "(untitled)"
        dtstart = comp.get("dtstart").dt
        dtend_prop = comp.get("dtend")
        all_day = not isinstance(dtstart, datetime)
        events.append(
            Event(
                title=summary,
                start=to_local(dtstart),
                end=to_local(dtend_prop.dt) if dtend_prop else None,
                all_day=all_day,
                source=source["name"],
                category=source["category"],
                location=str(comp.get("location", "")).strip(),
                person=source.get("person", ""),
                child=source.get("child", ""),
                url=_event_link(calendar_id, comp.get("uid")),
            )
        )
    return events
