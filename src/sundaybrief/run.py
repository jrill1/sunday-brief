"""Entry point. Run weekly by launchd, or by hand with --dry-run.

    python -m sundaybrief.run --dry-run                       # print, send nothing
    python -m sundaybrief.run                                  # build and push for real
    python -m sundaybrief.run --days 14                        # two-week window
    python -m sundaybrief.run --start 2026-08-03 --end 2026-08-09 --dry-run   # specific range
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime, timedelta
from pathlib import Path

from dotenv import load_dotenv

from .closures.ledger import load_ledger
from .config import ConfigError, load_config
from .dedupe import dedupe
from .deliver import send_pushover
from .gaps import analyze
from .ingest import INGESTERS
from .models import LOCAL_TZ
from .secrets import get_secret
from .summarize import build_narrative, build_templated

REPO_ROOT = Path(__file__).resolve().parents[2]


def _event_when(e) -> str:
    """Debug-dump display for one event: a single day+time, or a date range
    for a multi-day all-day event. Without this, an event that started before
    the window (but still overlaps into it, so recurring_ical_events rightly
    includes it) prints its absolute start date and reads as unrelated to the
    window being shown.
    """
    if e.all_day and e.end and e.end.date() > e.day:
        last_day = e.end.date() - timedelta(days=1)  # iCal's DTEND is exclusive
        if last_day != e.day:
            return f"{e.start:%a %b %-d}–{last_day:%b %-d}"
    return f"{e.start:%a %b %-d} {e.timelabel()}"


def _parse_date(s: str) -> datetime:
    try:
        d = datetime.strptime(s, "%Y-%m-%d")
    except ValueError:
        raise SystemExit(f"error: bad date {s!r} — use YYYY-MM-DD")
    return d.replace(tzinfo=LOCAL_TZ)


def resolve_window(args, config: dict) -> tuple[datetime, datetime]:
    """Figure out the [start, end) window from flags, falling back to config.

    --start DATE   window start (default: today, local midnight)
    --end DATE     inclusive end day; overrides --days
    --days N       length from start when --end is not given
    """
    if args.start:
        start = _parse_date(args.start)
    else:
        start = datetime.now(LOCAL_TZ).replace(hour=0, minute=0, second=0, microsecond=0)

    if args.end:
        end = _parse_date(args.end) + timedelta(days=1)   # include the whole end day
    else:
        days = args.days or config["window_days"]
        end = start + timedelta(days=days)

    if end <= start:
        raise SystemExit(f"error: end ({end:%Y-%m-%d}) must be after start ({start:%Y-%m-%d})")
    return start, end


def gather(config: dict, start: datetime, end: datetime) -> list[dict]:
    events, problems = [], []
    for src in config["sources"]:
        ingest = INGESTERS[src["type"]]
        try:
            found = ingest(src, start, end)
            events.extend(found)
            print(f"  [{src['type']:9}] {src['name']:<28} {len(found):>3} events", file=sys.stderr)
        except Exception as exc:  # one bad feed shouldn't sink the whole brief
            problems.append((src["name"], exc))
            print(f"  [{src['type']:9}] {src['name']:<28} FAILED: {exc}", file=sys.stderr)
    if problems:
        print(f"  ({len(problems)} source(s) failed — brief built from the rest)", file=sys.stderr)
    return events


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build and send the weekly family brief.")
    parser.add_argument("--config", default=str(REPO_ROOT / "config" / "sources.yaml"))
    parser.add_argument("--env", default=str(REPO_ROOT / ".env"))
    parser.add_argument("--days", type=int, default=None, help="window length (default: from config)")
    parser.add_argument("--start", metavar="YYYY-MM-DD", help="window start date (default: today)")
    parser.add_argument("--end", metavar="YYYY-MM-DD",
                        help="inclusive window end date (overrides --days)")
    parser.add_argument("--dry-run", action="store_true", help="print the brief; don't send")
    parser.add_argument("--show-events", action="store_true",
                        help="print every ingested event (great for a first test)")
    parser.add_argument("--ledger", default=str(REPO_ROOT / "data" / "closures.jsonl"),
                        help="closures ledger path (see sundaybrief.closures)")
    parser.add_argument("--style", choices=["templated", "narrative"], default=None,
                        help="override config/sources.yaml's summary.style for this run")
    args = parser.parse_args(argv)

    load_dotenv(args.env)

    try:
        config = load_config(args.config)
    except ConfigError as exc:
        print(f"config error: {exc}", file=sys.stderr)
        return 2

    start, end = resolve_window(args, config)
    span = (end - start).days
    print(f"Window: {start:%a %b %-d %Y} → {end:%a %b %-d %Y} ({span} days)", file=sys.stderr)

    events = dedupe(gather(config, start, end))
    print(f"Total after dedupe: {len(events)} events", file=sys.stderr)

    if args.show_events:
        print(f"\n--- {len(events)} events in window ---")
        for e in events:
            when = _event_when(e)
            who = e.person or e.child
            tag = f"[{e.category}{'/' + who if who else ''}]"
            print(f"  {when:<20} {tag:<18} {e.title}  ({' + '.join(e.sources)})")
        print()

    ledger_rows = load_ledger(args.ledger)
    print(f"Ledger: {len(ledger_rows)} rows ({args.ledger})", file=sys.stderr)

    signals = analyze(events, start, end, ledger_rows=ledger_rows)

    style = args.style or config["summary"]["style"]
    title, message = build_templated(signals)
    links_message = None
    if style == "narrative":
        api_key = get_secret("ANTHROPIC_API_KEY", required=False)
        if api_key:
            narrative = build_narrative(
                signals, config["summary"]["model"], api_key, names=config["summary"]["names"],
            )
            if narrative:
                message, links_message = narrative
            else:
                print("narrative unavailable — using templated brief", file=sys.stderr)
        else:
            print("no ANTHROPIC_API_KEY — using templated brief", file=sys.stderr)

    full_url = config["summary"].get("full_brief_url", "")

    if args.dry_run:
        print("\n" + "=" * 48)
        print(title)
        print("-" * 48)
        print(message)
        print("=" * 48)
        print(f"\n[dry run] {len(message)} chars — would POST to Pushover")
        if links_message:
            print("\n" + "=" * 48)
            print(f"{title} · links")
            print("-" * 48)
            print(links_message)
            print("=" * 48)
            print(f"\n[dry run] {len(links_message)} chars — would POST as a follow-up")
        return 0

    token = get_secret("PUSHOVER_TOKEN")
    user = get_secret("PUSHOVER_USER")
    result = send_pushover(token, user, title, message, url=full_url)
    print(f"Sent. Pushover status={result.get('status')}", file=sys.stderr)
    if links_message:
        links_result = send_pushover(token, user, f"{title} · links", links_message)
        print(f"Sent follow-up links. Pushover status={links_result.get('status')}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())