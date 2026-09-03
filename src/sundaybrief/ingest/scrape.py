"""Non-iCal local sources: RSS feeds, WordPress "The Events Calendar" JSON,
a LibNet/EventKeeper library-calendar JSON endpoint, a "Modern Events
Calendar" WordPress plugin HTML scrape, and (stubbed) headless rendering.

Order of preference — always try the cheapest that works:
  1. iCal feed    -> use ingest.ical instead (best)
  2. RSS          -> ingest_rss (coarse: uses publish date, good for newsletters)
  3. wp-events    -> ingest_wp_events (The Events Calendar REST API, real dates)
  4. libnet-events -> ingest_libnet_events (LibNet/EventKeeper's own JSON, no auth)
  5. mec-events   -> ingest_mec_events (Modern Events Calendar, server-rendered HTML)
  6. headless     -> render JS with Playwright (last resort; not implemented yet)
"""
from __future__ import annotations

import json
import re
from datetime import datetime

import feedparser
import requests

from ..models import Event, to_local


def ingest_rss(source: dict, window_start: datetime, window_end: datetime, **_) -> list[Event]:
    """Parse an RSS/Atom feed. Coarse by nature — most feeds carry a publish
    date, not an event date — so this is best for newsletters and community
    blogs. Prefer a real event feed (iCal / wp-events) when one exists.
    """
    feed = feedparser.parse(source["url"])
    events: list[Event] = []
    for entry in feed.entries:
        parsed = entry.get("published_parsed") or entry.get("updated_parsed")
        if not parsed:
            continue
        start = to_local(datetime(*parsed[:6]))
        if not (window_start <= start <= window_end):
            continue
        events.append(
            Event(
                title=entry.get("title", "(untitled)").strip(),
                start=start,
                end=None,
                all_day=True,
                source=source["name"],
                category=source["category"],
                url=entry.get("link", ""),
            )
        )
    return events


def ingest_wp_events(source: dict, window_start: datetime, window_end: datetime, timeout: int = 30) -> list[Event]:
    """Best-effort reader for WordPress 'The Events Calendar' REST API.

    Point `url` at the site root (e.g. https://example.com); this hits
    /wp-json/tribe/events/v1/events with a date range. Experimental — plugin
    versions vary, so it's wrapped defensively and returns [] on any mismatch.
    """
    base = source["url"].rstrip("/")
    endpoint = f"{base}/wp-json/tribe/events/v1/events"
    params = {
        "start_date": window_start.strftime("%Y-%m-%d %H:%M:%S"),
        "end_date": window_end.strftime("%Y-%m-%d %H:%M:%S"),
        "per_page": 50,
    }
    try:
        resp = requests.get(endpoint, params=params, timeout=timeout,
                            headers={"User-Agent": "sunday-brief/0.1"})
        resp.raise_for_status()
        payload = resp.json()
    except (requests.RequestException, ValueError):
        return []

    events: list[Event] = []
    for item in payload.get("events", []):
        try:
            start = to_local(datetime.fromisoformat(item["start_date"]))
            end = to_local(datetime.fromisoformat(item["end_date"])) if item.get("end_date") else None
        except (KeyError, ValueError):
            continue
        events.append(
            Event(
                title=(item.get("title") or "(untitled)").strip(),
                start=start,
                end=end,
                all_day=bool(item.get("all_day")),
                source=source["name"],
                category=source["category"],
                location=((item.get("venue") or {}).get("venue") or ""),
                url=item.get("url", ""),
            )
        )
    return events


def ingest_libnet_events(source: dict, window_start: datetime, window_end: datetime, timeout: int = 30) -> list[Event]:
    """LibNet/EventKeeper library-calendar JSON (the `eeventcaldata` endpoint
    the site's own event list calls) — no auth needed, unlike the platform's
    documented Communico REST API (which needs OAuth credentials only the
    library can issue). Reverse-engineered from the site's own Network tab
    since it exposes no public iCal/RSS feed; may break if the platform
    changes its internal API, in which case this needs a fresh look.

    Point `url` at the site root (e.g. https://maplewoodlibrary.libnet.info).
    """
    base = source["url"].rstrip("/")
    req = json.dumps({
        "private": False,
        "date": window_start.strftime("%Y-%m-%d"),
        "days": (window_end.date() - window_start.date()).days,
        "locations": [],
        "ages": [],
        "types": [],
    })
    try:
        resp = requests.get(
            f"{base}/eeventcaldata", params={"event_type": 0, "req": req}, timeout=timeout,
            headers={"User-Agent": "sunday-brief/0.1 (personal calendar aggregator)"},
        )
        resp.raise_for_status()
        payload = resp.json()
    except (requests.RequestException, ValueError):
        return []
    if not isinstance(payload, list):
        return []

    events: list[Event] = []
    for item in payload:
        try:
            start = to_local(datetime.fromisoformat(item["raw_start_time"]))
            end = to_local(datetime.fromisoformat(item["raw_end_time"])) if item.get("raw_end_time") else None
        except (KeyError, ValueError):
            continue
        events.append(
            Event(
                title=(item.get("title") or "(untitled)").strip(),
                start=start,
                end=end,
                all_day=False,
                source=source["name"],
                category=source["category"],
                location=(item.get("location") or "").strip(),
                url=item.get("url", ""),
                age_group=(item.get("ages") or "").strip(),
            )
        )
    return events


_MEC_TIME_RE = re.compile(r"(\d{1,2}):(\d{2})\s*(am|pm)", re.IGNORECASE)


def _mec_start_time(text: str) -> tuple[int, int] | None:
    """First "H:MMam/pm" in a time range like "1:00 pm - 8:00 pm" -> 24h (hour, minute)."""
    m = _MEC_TIME_RE.search(text)
    if not m:
        return None
    hour, minute, ampm = int(m.group(1)), int(m.group(2)), m.group(3).lower()
    if ampm == "pm" and hour != 12:
        hour += 12
    if ampm == "am" and hour == 12:
        hour = 0
    return hour, minute


def ingest_mec_events(source: dict, window_start: datetime, window_end: datetime, timeout: int = 30) -> list[Event]:
    """"Modern Events Calendar" WordPress plugin — renders event listings
    server-side (unlike libnet-events), so a plain HTML fetch + BeautifulSoup
    works, no API or JS rendering needed. Each day's events sit in a
    <div class="mec-calendar-events-sec" data-mec-cell="YYYYMMDD"> block.

    Point `url` at the actual calendar page (e.g.
    https://palletbrewing.com/eventscal/), not just the site root — MEC only
    renders whichever month(s) that specific page view covers, so a window
    reaching much further out than "the next month or two" may come back
    thin; this doesn't paginate into future months.
    """
    from bs4 import BeautifulSoup

    try:
        resp = requests.get(
            source["url"], timeout=timeout,
            headers={"User-Agent": "sunday-brief/0.1 (personal calendar aggregator)"},
        )
        resp.raise_for_status()
    except requests.RequestException:
        return []

    soup = BeautifulSoup(resp.text, "html.parser")
    events: list[Event] = []
    for day_sec in soup.select(".mec-calendar-events-sec[data-mec-cell]"):
        try:
            day = datetime.strptime(day_sec["data-mec-cell"], "%Y%m%d")
        except ValueError:
            continue
        if not (window_start.date() <= day.date() <= window_end.date()):
            continue
        for article in day_sec.select(".mec-event-article"):
            title_el = article.select_one(".mec-event-title a")
            if not title_el:
                continue
            time_el = article.select_one(".mec-event-time")
            hm = _mec_start_time(time_el.get_text(" ", strip=True)) if time_el else None
            start = day.replace(hour=hm[0], minute=hm[1]) if hm else day
            loc_el = article.select_one(".mec-event-loc-place")
            events.append(
                Event(
                    title=title_el.get_text(strip=True),
                    start=to_local(start),
                    end=None,
                    all_day=hm is None,
                    source=source["name"],
                    category=source["category"],
                    location=loc_el.get_text(strip=True) if loc_el else "",
                    url=title_el.get("href", ""),
                )
            )
    return events


def ingest_headless(source: dict, window_start: datetime, window_end: datetime, **_) -> list[Event]:
    """TODO: render JS-only calendars (e.g. Village Green's Tockify embed) with
    Playwright and scrape the DOM. Only reach for this once you've confirmed the
    site exposes no iCal, RSS, or wp-events endpoint. Left unimplemented so a
    misconfigured source fails loud rather than silently pretending to work.
    """
    raise NotImplementedError(
        f"Headless scraping for {source['name']!r} isn't implemented yet. "
        f"First check for an iCal/RSS/wp-json feed on that site."
    )
