"""Non-iCal local sources: RSS feeds, WordPress "The Events Calendar" JSON,
a LibNet/EventKeeper library-calendar JSON endpoint, and (stubbed) headless
rendering.

Order of preference — always try the cheapest that works:
  1. iCal feed    -> use ingest.ical instead (best)
  2. RSS          -> ingest_rss (coarse: uses publish date, good for newsletters)
  3. wp-events    -> ingest_wp_events (The Events Calendar REST API, real dates)
  4. libnet-events -> ingest_libnet_events (LibNet/EventKeeper's own JSON, no auth)
  5. headless     -> render JS with Playwright (last resort; not implemented yet)
"""
from __future__ import annotations

import json
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
