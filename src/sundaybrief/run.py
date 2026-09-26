"""Entry point. Run weekly by launchd, or by hand with --dry-run.

    python -m sundaybrief.run --dry-run                       # print, send nothing
    python -m sundaybrief.run                                  # build and push for real
    python -m sundaybrief.run --days 14                        # two-week window
    python -m sundaybrief.run --start 2026-08-03 --end 2026-08-09 --dry-run   # specific range
    python -m sundaybrief.run --brief friday --dry-run         # the Friday brief: coming Sat–Sun
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime, timedelta
from pathlib import Path

from dotenv import load_dotenv

from .briefs import KINDS
from .closures.ledger import load_ledger, localize_links
from .config import ConfigError, load_config
from .dedupe import dedupe
from .deliver import send_pushover, split_message
from .gaps import analyze
from .ingest import INGESTERS
from .models import LOCAL_TZ
from .secrets import get_secret
from .summarize import PUSHOVER_LIMIT, build_narrative, build_templated, build_weekend_picks

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


def resolve_window(args, config: dict, kind) -> tuple[datetime, datetime]:
    """Figure out the [start, end) window from flags, falling back to the brief
    kind's own default (e.g. Friday's Sat–Sun), then to config.

    --start DATE   window start (default: today, local midnight)
    --end DATE     inclusive end day; overrides --days
    --days N       length from start when --end is not given
    """
    if not (args.start or args.end or args.days):
        default = kind.default_window(datetime.now(LOCAL_TZ))
        if default:
            return default
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


def _platform(name: str) -> str:
    value = (get_secret(name, required=False) or "android").strip().lower()
    if value not in ("android", "ios"):
        raise SystemExit(f"error: {name} must be 'android' or 'ios', got {value!r}")
    return value


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build and send the weekly family brief.")
    parser.add_argument("--config", default=str(REPO_ROOT / "config" / "sources.yaml"))
    parser.add_argument("--env", default=str(REPO_ROOT / ".env"))
    parser.add_argument("--days", type=int, default=None, help="window length (default: from config)")
    parser.add_argument("--start", metavar="YYYY-MM-DD", help="window start date (default: today)")
    parser.add_argument("--end", metavar="YYYY-MM-DD",
                        help="inclusive window end date (overrides --days)")
    parser.add_argument("--brief", choices=sorted(KINDS), default="sunday",
                        help="which brief to build (default: sunday)")
    parser.add_argument("--dry-run", action="store_true", help="print the brief; don't send")
    parser.add_argument("--to", choices=["both", "me", "spouse"], default="both",
                        help="who to send to (default: both); anything else is marked [Test]")
    parser.add_argument("--platform", choices=["android", "ios"], default="android",
                        help="with --dry-run: preview the source-email links as that phone gets them")
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

    kind = KINDS[args.brief]
    start, end = resolve_window(args, config, kind)
    span = (end - start).days
    print(f"Window: {start:%a %b %-d %Y} → {end:%a %b %-d %Y} ({span} days)", file=sys.stderr)

    api_key = get_secret("ANTHROPIC_API_KEY", required=False)
    events = dedupe(gather(config, start, end), model=config["summary"]["model"], api_key=api_key)
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
    title, message = build_templated(signals, kind)
    links_message = None
    if style == "narrative":
        if api_key:
            narrative = build_narrative(
                signals, config["summary"]["model"], api_key,
                names=config["summary"]["names"], children=config["summary"]["children"],
                kind=kind,
            )
            if narrative:
                message, links_message = narrative
            else:
                print("narrative unavailable — using templated brief", file=sys.stderr)
        else:
            print("no ANTHROPIC_API_KEY — using templated brief", file=sys.stderr)

    # Weekend/extracurricular picks are independent of style (needs an LLM
    # call either way, to judge which local candidates are kid-appropriate).
    picks_result = build_weekend_picks(
        signals, config["summary"]["model"], api_key, children=config["summary"]["children"],
    ) if api_key else None
    weekend_picks, picks_label = picks_result if picks_result else (None, None)

    full_url = config["summary"].get("full_brief_url", "")

    if args.dry_run:
        message = localize_links(message, args.platform, ledger_rows)
        if links_message:
            links_message = localize_links(links_message, args.platform, ledger_rows)
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
        if weekend_picks:
            print("\n" + "=" * 48)
            print(f"{title} · {picks_label}")
            print("-" * 48)
            print(weekend_picks)
            print("=" * 48)
            print(f"\n[dry run] {len(weekend_picks)} chars — would POST as a follow-up")
        return 0

    token = get_secret("PUSHOVER_TOKEN")
    # PUSHOVER_USER_SPOUSE is optional — the weekly brief goes to both parents
    # once it's set, but only ever the weekly brief; ledger spotchecks
    # (run_extract.py) are a separate, single-recipient stream by design.
    # Each recipient also has an optional PUSHOVER_USER[_SPOUSE]_PLATFORM
    # ("android", the default, or "ios"): the source-email links open a
    # different companion per platform (see closures.ledger.localize_links).
    recipients = []
    if args.to in ("both", "me"):
        recipients.append((get_secret("PUSHOVER_USER"), _platform("PUSHOVER_USER_PLATFORM")))
    spouse_user = get_secret("PUSHOVER_USER_SPOUSE", required=False)
    if spouse_user and args.to in ("both", "spouse"):
        recipients.append((spouse_user, _platform("PUSHOVER_USER_SPOUSE_PLATFORM")))
    if not recipients:
        raise SystemExit("error: --to spouse, but PUSHOVER_USER_SPOUSE isn't set")
    if args.to != "both":
        title = f"[Test] {title}"

    for user, platform in recipients:
        user_message = localize_links(message, platform, ledger_rows)
        result = send_pushover(token, user, title, user_message, url=full_url)
        print(f"Sent. Pushover status={result.get('status')}", file=sys.stderr)
        if links_message:
            # Localized links can be longer than the Android ones the message
            # was budgeted with, so split rather than let Pushover truncate.
            chunks = split_message(localize_links(links_message, platform, ledger_rows), PUSHOVER_LIMIT)
            for i, chunk in enumerate(chunks, 1):
                suffix = f" ({i}/{len(chunks)})" if len(chunks) > 1 else ""
                links_result = send_pushover(token, user, f"{title} · links{suffix}", chunk)
                print(f"Sent follow-up links{suffix}. Pushover status={links_result.get('status')}", file=sys.stderr)
        if weekend_picks:
            send_result = send_pushover(token, user, f"{title} · {picks_label}", weekend_picks)
            print(f"Sent weekend picks. Pushover status={send_result.get('status')}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())