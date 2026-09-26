"""The kinds of brief we send. Each is the same pipeline with a different
window and different wording — see run.py's --brief flag.

  sunday: Sunday morning, the coming week (window comes from config).
  friday: Friday morning, just the coming Saturday–Sunday.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from .models import LOCAL_TZ


@dataclass(frozen=True)
class BriefKind:
    name: str
    title: str          # push title prefix: "<title> · Sep 26–27"
    greeting: str       # first line of the narrative
    heads_up: str       # "a warm, practical <heads_up> for a family..."
    this_period: str    # "this week" / "this weekend", for prose
    fixed_weekend: bool = False   # window is always the coming Sat–Sun

    def default_window(self, now: datetime) -> tuple[datetime, datetime] | None:
        """(start, end) — end exclusive — or None to use the caller's usual
        default (today + config window_days)."""
        if not self.fixed_weekend:
            return None
        today = now.astimezone(LOCAL_TZ).replace(hour=0, minute=0, second=0, microsecond=0)
        start = today + timedelta(days=(5 - today.weekday()) % 7)   # this/next Saturday
        return start, start + timedelta(days=2)


SUNDAY = BriefKind(
    name="sunday", title="Sunday Brief", greeting="Hi, it's Sunday!",
    heads_up="weekly heads-up", this_period="this week",
)
FRIDAY = BriefKind(
    name="friday", title="Friday Brief", greeting="Hi, it's Friday!",
    heads_up="heads-up for the coming weekend", this_period="this weekend",
    fixed_weekend=True,
)
KINDS = {k.name: k for k in (SUNDAY, FRIDAY)}
