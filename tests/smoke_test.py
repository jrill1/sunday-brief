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

from sundaybrief.briefs import FRIDAY          # noqa: E402
from sundaybrief.closures.ledger import localize_links, source_link  # noqa: E402
from sundaybrief.deliver import split_message  # noqa: E402
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

    _check_friday_window()
    _check_ios_links()
    _check_split_message()

    print("OK — all assertions passed")
    print(f"\nTitle: {title}\nLength: {len(message)} chars (limit {PUSHOVER_LIMIT})\n")
    print(message)
    return 0


def _check_friday_window() -> None:
    # Mon–Sat resolve to the coming (or current) Sat–Sun; Sunday, to the next one.
    for day, expect in ((21, (9, 26)), (25, (9, 26)), (26, (9, 26)), (27, (10, 3))):
        start, end = FRIDAY.default_window(datetime(2026, 9, day, 9, 0, tzinfo=LOCAL_TZ))
        assert (start.month, start.day) == expect, (day, start)
        assert start.weekday() == 5 and (end - start).days == 2
    sig = analyze([], datetime(2026, 9, 26, tzinfo=LOCAL_TZ), datetime(2026, 9, 28, tzinfo=LOCAL_TZ))
    title, _ = build_templated(sig, FRIDAY)
    assert title == "Friday Brief · Sep 26–Sep 27", title


def _check_ios_links() -> None:
    row = {
        "source_message_id": "<CAabc123@mail.gmail.com>",
        "source_from": "Front Office <office@sunnybrookacademy.example.com>",
        "source_subject": "Reminder: Wed. 8/19: Field Trip to the Aquarium",
    }
    android = source_link(row)
    assert android.startswith("sundaybrief://open?q=rfc822msgid")
    html_text = f'<a href="{android}">Field trip (Gmail)</a>'
    assert localize_links(html_text, "android", [row]) == html_text   # untouched
    ios = localize_links(html_text, "ios", [row])
    assert "shortcuts://run-shortcut?name=BriefOpen&amp;input=text&amp;text=" in ios, ios
    assert "sunnybrookacademy.example.com" in ios and "sundaybrief://" not in ios
    # A link with no matching ledger row falls back to its Message-ID search.
    assert "rfc822msgid" in localize_links(html_text, "ios", [])


def _check_split_message() -> None:
    lines = [f"line {i} " + "x" * 90 for i in range(30)]
    chunks = split_message("\n".join(lines), 1024)
    assert len(chunks) > 1 and all(len(c) <= 1024 for c in chunks)
    assert "\n".join(chunks) == "\n".join(lines)          # nothing lost or reordered
    assert split_message("short") == ["short"]


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
