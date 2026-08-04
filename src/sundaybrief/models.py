"""Common event model shared across every ingestion source.

Everything the pipeline touches — a parent's Google calendar, a daycare
closure, a library story time scraped off a feed — gets normalized into an
`Event` so the rest of the code never has to care where it came from.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import date, datetime
from zoneinfo import ZoneInfo

# Maplewood, NJ. Everything is normalized to this zone so day boundaries
# ("is this a Saturday?", "does this land on the closure day?") are correct.
LOCAL_TZ = ZoneInfo("America/New_York")


def to_local(value: datetime | date) -> datetime:
    """Coerce an icalendar date or datetime into a tz-aware local datetime.

    All-day events arrive as `date`; we anchor them to local midnight so they
    sort and compare cleanly against timed events.
    """
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=LOCAL_TZ)
        return value.astimezone(LOCAL_TZ)
    return datetime(value.year, value.month, value.day, tzinfo=LOCAL_TZ)


@dataclass
class Event:
    title: str
    start: datetime
    end: datetime | None
    all_day: bool
    source: str          # human name of the source, e.g. "Alex — work"
    category: str        # one of: work, personal, daycare, local
    location: str = ""
    url: str = ""
    person: str = ""     # which parent, for calendar categories
    child: str = ""      # which kid, for daycare categories
    sources: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        if not self.sources:
            self.sources = [self.source]

    @property
    def day(self) -> date:
        return self.start.date()

    def dedupe_key(self) -> str:
        """Same title on the same day = the same real-world event.

        Town events routinely show up in three feeds at once; this collapses
        them. Deliberately coarse (title + date, not time) because feeds
        disagree on exact start times all the time.
        """
        base = f"{self.title.strip().lower()}|{self.day.isoformat()}"
        return hashlib.sha1(base.encode("utf-8")).hexdigest()

    def timelabel(self) -> str:
        if self.all_day:
            return "all day"
        return self.start.strftime("%-I:%M %p").lower()
