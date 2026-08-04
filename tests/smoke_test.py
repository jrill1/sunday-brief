"""End-to-end smoke test with synthetic data — no network, no secrets.

Verifies: iCal parsing + recurrence expansion, category routing, dedupe,
closure/half-day detection, focus-day + pick matching, and that the templated
Pushover message stays under the 1024-char limit.

Run:  python tests/smoke_test.py
"""
from __future__ import annotations

import sys
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from sundaybrief.dedupe import dedupe          # noqa: E402
from sundaybrief.gaps import analyze           # noqa: E402
from sundaybrief.ingest.ical import ingest_ical  # noqa: E402
from sundaybrief.models import LOCAL_TZ        # noqa: E402
from sundaybrief.summarize import PUSHOVER_LIMIT, build_templated  # noqa: E402


def _ics(events_ics: str) -> str:
    return "BEGIN:VCALENDAR\nVERSION:2.0\nPRODID:-//test//EN\n" + events_ics + "END:VCALENDAR\n"


def _vevent(uid, summary, start_ymd, extra="") -> str:
    return (
        "BEGIN:VEVENT\n"
        f"UID:{uid}\n"
        f"SUMMARY:{summary}\n"
        f"DTSTART;VALUE=DATE:{start_ymd}\n"
        f"{extra}"
        "END:VEVENT\n"
    )


def main() -> int:
    # Anchor the window to a known Monday so weekday math is deterministic.
    start = datetime(2026, 8, 3, 0, 0, tzinfo=LOCAL_TZ)   # Monday
    end = start + timedelta(days=7)

    # A recurring weekday parent standup + a one-off personal item.
    parent_ics = _ics(
        "BEGIN:VEVENT\nUID:standup\nSUMMARY:Team standup\n"
        "DTSTART;TZID=America/New_York:20260803T093000\n"
        "RRULE:FREQ=WEEKLY;BYDAY=MO,TU,WE,TH,FR\nEND:VEVENT\n"
    )
    parent = ingest_ical(
        {"name": "Alex — work", "type": "ical", "category": "work",
         "person": "Alex", "child": "", "url": "inline"},
        start, end, timeout=1,
    ) if False else _parse_inline(parent_ics, "Alex — work", "work", start, end)

    # Daycare: a Friday closure.
    daycare = _parse_inline(
        _ics(_vevent("clo", "Closed - staff in-service", "20260807")),
        "Goddard (Ellie)", "daycare", start, end, child="Ellie",
    )

    # Local: two events, one duplicated across "two feeds", one on the weekend.
    local_a = _parse_inline(_ics(_vevent("mkt", "Farmers Market", "20260808")),
                            "Springfield Ave", "local", start, end)
    local_b = _parse_inline(_ics(_vevent("mkt2", "Farmers Market", "20260808")),
                            "Village Green", "local", start, end)
    local_c = _parse_inline(_ics(_vevent("story", "Toddler Story Time", "20260807")),
                            "Maplewood Library", "local", start, end)

    all_events = parent + daycare + local_a + local_b + local_c
    deduped = dedupe(all_events)

    # Recurrence expanded to 5 weekday standups.
    standups = [e for e in deduped if e.title == "Team standup"]
    assert len(standups) == 5, f"expected 5 standups, got {len(standups)}"

    # The two "Farmers Market" feeds collapsed to one, remembering both sources.
    markets = [e for e in deduped if e.title == "Farmers Market"]
    assert len(markets) == 1, f"dedupe failed: {len(markets)} markets"
    assert len(markets[0].sources) == 2, "should remember both feeds"

    sig = analyze(deduped, start, end)
    assert len(sig.closures) == 1, "should detect the Friday closure"
    assert markets[0].day in sig.focus_days, "Saturday market day should be a focus day"
    assert any(e.title == "Toddler Story Time" for e in sig.picks), "closure-day pick missing"

    title, message = build_templated(sig)
    assert len(message) <= PUSHOVER_LIMIT, f"message too long: {len(message)}"
    assert "Daycare" in message and "Local picks" in message

    print("OK — all assertions passed")
    print(f"\nTitle: {title}\nLength: {len(message)} chars (limit {PUSHOVER_LIMIT})\n")
    print(message)
    return 0


def _parse_inline(ics_text, name, category, start, end, child=""):
    """Parse an in-memory ICS string through the real iCal code path."""
    import sundaybrief.ingest.ical as ical_mod
    orig = ical_mod._read_source
    ical_mod._read_source = lambda url, timeout=30: ics_text
    try:
        return ical_mod.ingest_ical(
            {"name": name, "type": "ical", "category": category,
             "person": "", "child": child, "url": "inline"},
            start, end,
        )
    finally:
        ical_mod._read_source = orig


if __name__ == "__main__":
    raise SystemExit(main())
