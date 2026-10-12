"""The durable memory: append-only per-day rows + the fold that reads them.

Never edits or deletes. A cancellation is new active:0 rows; effective state is
computed on read (latest source_date per school+date wins).
"""
from __future__ import annotations

import difflib
import json
import re
import urllib.parse
import uuid
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from .reader import EmailDoc

def child_by_school(children: list[dict]) -> dict[str, str]:
    """school -> real child name, from CHILDREN_JSON's optional `school` key
    (see .env.example) — e.g. {"name": "Max", "school": "Le Parc", ...}.
    Real names only ever live in .env, never in this tracked file, so a
    child with no `school` key set just doesn't appear here; callers treat a
    missing school the same as an unrecognized one (empty-string fallback).
    """
    return {c["school"]: c["name"] for c in children if c.get("school")}


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


def expand_rows(
    item: dict, doc: EmailDoc, attachment: str | None, extractor_tag: str,
    child_by_school_map: dict[str, str] | None = None,
) -> list[dict]:
    """One extracted fact -> one ledger row per calendar day in its span."""
    start = date.fromisoformat(item["start_date"])
    end = date.fromisoformat(item["end_date"])
    now = datetime.now(timezone.utc).isoformat()
    rows = []
    for d in _daterange(start, end):
        rows.append({
            "record_id": "led_" + uuid.uuid4().hex[:10],
            "school": item["school"],
            "child": (child_by_school_map or {}).get(item["school"], ""),
            "date": d.isoformat(),
            "type": item["type"],
            "active": int(item["active"]),
            "reason": item["reason"],
            "context": item.get("context") or "",
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


def _normalize(text: str) -> str:
    """Collapse cosmetic wording differences (hyphens, dashes, case,
    whitespace) so the same real-world fact worded slightly differently by
    two separate emails compares as identical. A trailing parenthetical
    ("(deadline moved up)", "(rain date TBD)") is stripped too — it's almost
    always added context around an otherwise-identical fact, not part of its
    core identity."""
    text = re.sub(r"\s*\([^)]*\)\s*$", "", text)
    text = text.lower().replace("-", " ").replace("—", " ")
    return " ".join(text.split())


def is_duplicate_row(candidate: dict, existing_rows: list[dict], threshold: float = 0.82) -> bool:
    """True if `candidate` just restates a fact already present in
    `existing_rows` — same school/date/type/active, with near-identical
    reason text. Forwards or repeats of the same announcement each carry a
    fresh Message-ID, so this catches what the Message-ID idempotency check
    in run_extract.py can't.

    `active` is part of the match key on purpose: a genuine status change
    (e.g. a later cancellation) has a different `active` value and must
    never be treated as a duplicate — this ledger is append-only, and a
    cancellation is how a change gets recorded (see module docstring).
    """
    cand_norm = _normalize(candidate["reason"])
    for r in existing_rows:
        if (r["school"], r["date"], r["type"], r["active"]) != (
            candidate["school"], candidate["date"], candidate["type"], candidate["active"],
        ):
            continue
        if difflib.SequenceMatcher(None, cand_norm, _normalize(r["reason"])).ratio() >= threshold:
            return True
    return False


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


# The iOS counterpart of the Android opener: an iOS Shortcut with this exact
# name (see ios-opener/README.md) that copies its text input to the clipboard
# and opens Gmail.
IOS_SHORTCUT_NAME = "BriefOpen"
_OPENER_LINK = re.compile(r'sundaybrief://open\?q=([^"\s<]+)')


def _ios_query(row: dict) -> str:
    """Gmail search for the school's *original* email behind a row.

    Not the Message-ID: a ledger row's source_message_id is the ID of the
    parent's manual forward to the drop-box, which only exists in the
    forwarder's Sent folder (and the drop-box) — so it can't be found from
    the other parent's own mailbox. The school's original went to both
    parents, so sender domain + subject finds it in either. The subject is cut
    to a few words (a quoted Gmail phrase still matches as a prefix) to keep
    the link short, since a Pushover message is capped at 1024 chars.
    """
    m = re.search(r"@([\w.-]+)", row.get("source_from") or "")
    subject = re.sub(r'["\']', "", row.get("source_subject") or "").strip()
    if len(subject) > 40:
        subject = subject[:40].rsplit(" ", 1)[0]
    parts = []
    if m:
        parts.append(f"from:{m.group(1)}")
    if subject:
        parts.append(f'subject:"{subject}"')
    if parts:
        return " ".join(parts)
    mid = (row.get("source_message_id") or "").strip().strip("<>")
    return f"rfc822msgid:{mid}"


def localize_links(text: str, platform: str, ledger_rows: list[dict]) -> str:
    """Rewrite the canonical (Android) opener links in `text` for `platform`.

    Links are built once, platform-neutral, as sundaybrief://open?q=rfc822msgid:<id>
    (see source_link); this swaps them per recipient at send time. Android is
    the canonical form, so it's returned untouched. On iOS each link becomes a
    shortcuts://run-shortcut link, using the ledger row behind that Message-ID
    to build the sender+subject search (see _ios_query). A link whose row
    can't be found keeps its Message-ID search rather than breaking.
    """
    if platform != "ios":
        return text
    by_mid = {
        (r.get("source_message_id") or "").strip().strip("<>"): r for r in ledger_rows
    }

    def swap(m: re.Match) -> str:
        query = urllib.parse.unquote(m.group(1))
        row = by_mid.get(query.removeprefix("rfc822msgid:"))
        if row:
            query = _ios_query(row)
        name = urllib.parse.quote(IOS_SHORTCUT_NAME, safe="")
        # "&amp;", not "&": this lands inside an href in Pushover's HTML.
        return (f"shortcuts://run-shortcut?name={name}&amp;input=text&amp;text="
                + urllib.parse.quote(query, safe=""))

    return _OPENER_LINK.sub(swap, text)
