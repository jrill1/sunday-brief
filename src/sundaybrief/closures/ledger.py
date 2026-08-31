"""The durable memory: append-only per-day rows + the fold that reads them.

Never edits or deletes. A cancellation is new active:0 rows; effective state is
computed on read (latest source_date per school+date wins).
"""
from __future__ import annotations

import json
import urllib.parse
import uuid
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from .reader import EmailDoc

# school -> child. Belongs in config long-term; hardcoded map for now.
CHILD_BY_SCHOOL = {"Goddard": "ChildB", "Le Parc": "ChildA"}


def _daterange(start: date, end: date):
    d = start
    while d <= end:
        yield d
        d += timedelta(days=1)


def _title(school: str, type_: str, active: int, reason: str) -> str:
    if type_ == "note":
        if active == 0:
            return f"{school} note (canceled) — {reason}"
        return f"{school} note — {reason}"
    if active == 0:
        return f"{school} open — {reason}"
    verb = "closed" if type_ == "closure" else "half day"
    return f"{school} {verb} — {reason}"


def expand_rows(item: dict, doc: EmailDoc, attachment: str | None, extractor_tag: str) -> list[dict]:
    """One extracted fact -> one ledger row per calendar day in its span."""
    start = date.fromisoformat(item["start_date"])
    end = date.fromisoformat(item["end_date"])
    now = datetime.now(timezone.utc).isoformat()
    rows = []
    for d in _daterange(start, end):
        rows.append({
            "record_id": "led_" + uuid.uuid4().hex[:10],
            "school": item["school"],
            "child": CHILD_BY_SCHOOL.get(item["school"], ""),
            "date": d.isoformat(),
            "type": item["type"],
            "active": int(item["active"]),
            "reason": item["reason"],
            "title": _title(item["school"], item["type"], int(item["active"]), item["reason"]),
            "span_start": start.isoformat(),
            "span_end": end.isoformat(),
            "source_from": doc.source_from,
            "source_subject": doc.source_subject,
            "source_date": doc.source_date.isoformat() if doc.source_date else None,
            "source_message_id": doc.message_id,
            "source_attachment": attachment,
            "date_added": now,
            "extractor": extractor_tag,
        })
    return rows


def append_rows(rows: list[dict], ledger_path: str | Path) -> None:
    Path(ledger_path).parent.mkdir(parents=True, exist_ok=True)
    with open(ledger_path, "a") as f:
        for r in rows:
            f.write(json.dumps(r) + "\n")


def load_ledger(ledger_path: str | Path) -> list[dict]:
    p = Path(ledger_path)
    if not p.exists():
        return []
    return [json.loads(line) for line in p.read_text().splitlines() if line.strip()]


# ---- processed-message tracking (idempotency) ----------------------------

def load_processed(path: str | Path) -> set[str]:
    p = Path(path)
    return set(json.loads(p.read_text())) if p.exists() else set()


def mark_processed(message_id: str, path: str | Path) -> None:
    ids = load_processed(path)
    ids.add(message_id)
    Path(path).write_text(json.dumps(sorted(ids)))


# ---- the fold ------------------------------------------------------------

def fold(rows: list[dict]) -> dict[tuple[str, str], dict]:
    """(school, date) -> the winning row. Latest source_date wins; tiebreak
    date_added; still-tied fails safe to whichever row is a closure (active:1)."""
    winners: dict[tuple[str, str], dict] = {}
    for r in rows:
        key = (r["school"], r["date"])
        cur = winners.get(key)
        if cur is None or _beats(r, cur):
            winners[key] = r
    return winners


def _beats(a: dict, b: dict) -> bool:
    ka = (a.get("source_date") or "", a.get("date_added") or "", 1 if a["active"] == 1 else 0)
    kb = (b.get("source_date") or "", b.get("date_added") or "", 1 if b["active"] == 1 else 0)
    return ka > kb


def effective_closures(rows: list[dict], start: date, end: date) -> list[dict]:
    """Winning rows that are closed/reduced (active:1) within [start, end].

    Excludes `note` rows — those are logistics, not coverage gaps.
    """
    out = []
    for (school, d), row in fold(rows).items():
        if (row["active"] == 1 and row["type"] in ("closure", "partial_closure")
                and start.isoformat() <= d <= end.isoformat()):
            out.append(row)
    return sorted(out, key=lambda r: (r["date"], r["school"]))


def effective_notes(rows: list[dict], start: date, end: date) -> list[dict]:
    """Winning `note` rows still in effect (active:1) within [start, end]."""
    out = []
    for (school, d), row in fold(rows).items():
        if (row["active"] == 1 and row["type"] == "note"
                and start.isoformat() <= d <= end.isoformat()):
            out.append(row)
    return sorted(out, key=lambda r: (r["date"], r["school"]))


def source_link(row: dict) -> str:
    """Best-effort clickable link back to a row's source document.

    A web-sourced row's `source_message_id` is a content hash (see
    reader.read_web_page) rather than a real Message-ID, so `source_from` —
    the page URL itself — is the link. An email-sourced row has no direct URL,
    so this instead points at the "Sunday Brief Opener" companion Android app
    (see android-opener/) via its own sundaybrief:// scheme: tapping it copies
    a Gmail search for the exact Message-ID to the clipboard and opens Gmail,
    since Gmail's Android app has no reliable way to be deep-linked straight
    to a search result (mail.google.com's #search/ fragment is silently
    ignored by Gmail's mobile web fallback, and Gmail's own internal deep-link
    mechanisms proved unreliable — see project notes). This is mobile-only:
    on a device without the opener app installed, the link does nothing.
    """
    mid = row.get("source_message_id") or ""
    if mid.startswith("web-"):
        return row.get("source_from", "")
    mid = mid.strip().strip("<>")
    if not mid:
        return ""
    query = "rfc822msgid:" + mid
    return "sundaybrief://open?q=" + urllib.parse.quote(query, safe="")
