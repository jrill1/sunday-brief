"""Populate the ledger from the drop-box (or a local .eml folder).

Only new messages (by Message-ID) are ever sent to the model, so re-runs are
cheap and idempotent.

    python -m sundaybrief.closures.run_extract --source local:./tests/fixtures/emails --dry-run
    python -m sundaybrief.closures.run_extract --source imap
    python -m sundaybrief.closures.run_extract --source web:https://example.com/academic-calendar
"""
from __future__ import annotations

import argparse
import html
import sys
from pathlib import Path

from . import reader
from .extract import AnthropicExtractor
from .ledger import append_rows, expand_rows, is_duplicate_row, load_ledger, load_processed, mark_processed, source_link
from ..deliver import send_pushover
from ..secrets import get_secret

REPO_ROOT = Path(__file__).resolve().parents[3]
LEDGER = REPO_ROOT / "data" / "closures.jsonl"
PROCESSED = REPO_ROOT / "data" / "processed.json"
PROMPT = REPO_ROOT / "prompts" / "extract_closures.md"
SPOTCHECK_LIMIT = 1024  # Pushover's per-message body limit


def get_docs(source: str) -> list[reader.EmailDoc]:
    if source.startswith("local:"):
        return reader.read_local_dir(source.split(":", 1)[1])
    if source.startswith("web:"):
        return [reader.read_web_page(source.split(":", 1)[1])]
    if source == "imap":
        return reader.read_imap(
            host=get_secret("DROPBOX_IMAP_HOST", default="imap.gmail.com"),
            user=get_secret("DROPBOX_IMAP_USER"),
            app_password=get_secret("DROPBOX_IMAP_PASSWORD"),
        )
    raise SystemExit(f"unknown source: {source!r}")


def _spotcheck_block(row: dict) -> str:
    """One newly-added row, rendered as a condensed pretty-JSON-look block for
    a Pushover push — "source" is a real <a href> (the sundaybrief:// opener
    link), not a literal string, so it stays tappable even though the rest of
    the block just reads as JSON."""
    link = source_link(row)
    source = f'<a href="{html.escape(link)}">tap to open</a>' if link else "(no source link)"
    context_line = f'  "context": "{html.escape(row["context"])}",\n' if row.get("context") else ""
    return (
        "{\n"
        f'  "date": "{html.escape(row["date"])}",\n'
        f'  "title": "{html.escape(row["title"])}",\n'
        f'{context_line}'
        f'  "source": {source}\n'
        "}"
    )


def _chunk_spotchecks(rows: list[dict]) -> list[str]:
    """Pack newly-added rows into as few <=1024-char Pushover bodies as
    possible, so a big batch (e.g. a first-time backfill) sends a handful of
    messages instead of one push per row."""
    messages, current = [], ""
    for row in rows:
        block = _spotcheck_block(row)
        candidate = f"{current}\n\n{block}" if current else block
        if len(candidate) > SPOTCHECK_LIMIT and current:
            messages.append(current)
            current = block
        else:
            current = candidate
    if current:
        messages.append(current)
    return messages


def send_spotchecks(rows: list[dict]) -> None:
    if not rows:
        return
    # its own Application (thread) when set, so spotchecks don't mix into the
    # weekly brief's — falls back to the main token if not configured.
    token = get_secret("PUSHOVER_TOKEN_LEDGER", required=False) or get_secret("PUSHOVER_TOKEN", required=False)
    user = get_secret("PUSHOVER_USER", required=False)
    if not (token and user):
        print("  no Pushover creds configured — skipping spotcheck notifications", file=sys.stderr)
        return
    chunks = _chunk_spotchecks(rows)
    for i, body in enumerate(chunks, 1):
        title = "Ledger spotcheck" if len(chunks) == 1 else f"Ledger spotcheck {i}/{len(chunks)}"
        send_pushover(token, user, title, body)
    print(f"  sent {len(rows)} new row(s) as {len(chunks)} spotcheck push(es)", file=sys.stderr)


def run(source: str, extract_fn, model_tag: str, dry_run: bool, spotcheck: bool = True) -> int:
    docs = get_docs(source)
    processed = load_processed(PROCESSED)
    existing = load_ledger(LEDGER)  # for cross-email dedup, see is_duplicate_row
    print(f"{len(docs)} messages; {len(processed)} already processed", file=sys.stderr)

    new_rows = 0
    dup_rows = 0
    all_new_rows = []
    for doc in docs:
        if doc.message_id in processed:
            continue
        items = extract_fn(doc)
        # attribution: which attachment (if any) closures came from
        att = doc.attachments[0][0] if doc.attachments else None
        rows = []
        for item in items:
            for row in expand_rows(item, doc, att, model_tag):
                if is_duplicate_row(row, existing):
                    dup_rows += 1
                    continue
                rows.append(row)
                existing.append(row)  # dedupe later docs in this same run too
        print(f"  {doc.source_subject[:48]:<50} -> {len(items)} fact(s), {len(rows)} row(s)",
              file=sys.stderr)
        if dry_run:
            for item in items:
                span = item["start_date"] if item["start_date"] == item["end_date"] \
                    else f"{item['start_date']}..{item['end_date']}"
                flag = "" if int(item.get("active", 1)) == 1 else " [CANCELED]"
                print(f"      [{item['type']}] {span} — {item['reason']}{flag}", file=sys.stderr)
        if not dry_run:
            append_rows(rows, LEDGER)
            mark_processed(doc.message_id, PROCESSED)
            all_new_rows += rows
        new_rows += len(rows)

    print(f"{'[dry-run] would add' if dry_run else 'added'} {new_rows} ledger rows "
          f"({dup_rows} duplicate(s) skipped)", file=sys.stderr)

    if not dry_run and spotcheck:
        send_spotchecks(all_new_rows)

    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", default="imap",
                     help="'imap', 'local:/path/to/eml/dir', or 'web:<url>'")
    ap.add_argument("--model", default="claude-sonnet-5")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--no-spotcheck", action="store_true",
                     help="don't push newly-added rows to Pushover for review "
                          "(handy for a big backfill, to avoid a flood of pushes)")
    args = ap.parse_args(argv)

    from dotenv import load_dotenv
    load_dotenv(REPO_ROOT / ".env")

    from anthropic import Anthropic
    extractor = AnthropicExtractor(Anthropic(), args.model, PROMPT)
    tag = f"{args.model} / prompt-v1"
    return run(args.source, extractor, tag, args.dry_run, spotcheck=not args.no_spotcheck)


if __name__ == "__main__":
    raise SystemExit(main())
