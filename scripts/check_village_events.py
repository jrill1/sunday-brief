"""Monthly check: has any of Maplewood Village's annual signature-event
pages changed since we last looked?

These 7 pages (Art Walk, Village Night Out, Dickens Village, etc.) each
describe one big annual/seasonal village event, with its exact date usually
buried in a paragraph of prose and filled in incrementally as the event
approaches — e.g. Dickens Village (a December event) just says "happening
again this December" months out, with no specific date, while Art Walk
(happening next month) already has "Sunday, October 18, 2026, 11am-5pm"
spelled out. There's no discoverable calendar feed here, just these 7 known
pages, so this snapshots each page's visible text and flags whichever ones
changed since the last run — useful even for a page we can't parse a date
out of yet, since "this page's content just changed" is itself the signal
worth a look.

    python scripts/check_village_events.py
    python scripts/check_village_events.py --quiet   # only print if something changed
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

import requests
from bs4 import BeautifulSoup

REPO_ROOT = Path(__file__).resolve().parents[1]
SNAPSHOT_PATH = REPO_ROOT / "data" / "village_events_snapshot.json"

PAGES = [
    ("Art Walk and Music Fest", "https://maplewoodvillagenj.com/art-walk"),
    ("Village Night Out", "https://maplewoodvillagenj.com/village-night-out"),
    ("Celebrating Our Black-Owned Businesses",
     "https://maplewoodvillagenj.com/celebrating-our-black-owned-businesses"),
    ("Windows for Women", "https://maplewoodvillagenj.com/windows-for-women"),
    ("Maplewood Village - Late Summer Saturdays",
     "https://maplewoodvillagenj.com/maplewood-village-summer-saturdays"),
    ("Dickens Village", "https://maplewoodvillagenj.com/dickens-village"),
    ("Maplewood Small Wonder Marketplace",
     "https://maplewoodvillagenj.com/maplewood-small-wonder-marketplace"),
]

_DATE_RE = re.compile(
    r"(Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday),\s+"
    r"(January|February|March|April|May|June|July|August|September|October|November|December)"
    r"\s+\d{1,2},\s+(\d{4})[^.\n]*"
)


def fetch_page(url: str, timeout: int = 30) -> str:
    resp = requests.get(
        url, timeout=timeout,
        headers={"User-Agent": "sunday-brief/0.1 (personal calendar aggregator)"},
    )
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")
    for tag in soup(["script", "style"]):
        tag.decompose()
    lines = [l.strip() for l in soup.get_text("\n").split("\n") if l.strip()]
    return "\n".join(lines)


def extract_date(text: str) -> str | None:
    """First date-shaped match whose year is the current year, if any —
    prefers that over an incidental "last year, on August 11, 2024" kind of
    reference sitting earlier in the page's prose. Falls back to the first
    match of any year if nothing matches the current year, rather than
    silently returning nothing when a real (just not current-year) date is
    present — e.g. a page already written for next January before year-end.
    """
    this_year = str(datetime.now().year)
    matches = list(_DATE_RE.finditer(text))
    for m in matches:
        if m.group(3) == this_year:
            return m.group(0).strip()
    return matches[0].group(0).strip() if matches else None


def load_snapshot() -> dict:
    if not SNAPSHOT_PATH.exists():
        return {}
    return json.loads(SNAPSHOT_PATH.read_text())


def save_snapshot(snapshot: dict) -> None:
    SNAPSHOT_PATH.parent.mkdir(parents=True, exist_ok=True)
    SNAPSHOT_PATH.write_text(json.dumps(snapshot, indent=2))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--quiet", action="store_true", help="only print pages that changed")
    args = ap.parse_args(argv)

    snapshot = load_snapshot()
    now = datetime.now(timezone.utc).isoformat()
    any_changed = False

    for name, url in PAGES:
        try:
            text = fetch_page(url)
        except requests.RequestException as exc:
            print(f"  [FAILED] {name}: {exc}", file=sys.stderr)
            continue

        text_hash = hashlib.sha256(text.encode()).hexdigest()[:16]
        date = extract_date(text)
        prev = snapshot.get(url)
        changed = prev is None or prev["hash"] != text_hash

        if changed:
            any_changed = True
            prev_date = prev["date"] if prev else None
            marker = "NEW" if prev is None else "CHANGED"
            print(f"[{marker}] {name}")
            print(f"  date: {date or '(no specific date found)'}"
                  + (f"  (was: {prev_date or 'none'})" if prev and prev_date != date else ""))
            print(f"  {url}")
        elif not args.quiet:
            print(f"[unchanged] {name} — date: {date or '(no specific date found)'}")

        snapshot[url] = {"hash": text_hash, "date": date, "checked_at": now}

    save_snapshot(snapshot)
    if not any_changed:
        print("\nNo changes since last check." if not args.quiet else "", end="" if args.quiet else "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
