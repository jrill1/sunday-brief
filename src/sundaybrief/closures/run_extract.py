"""Populate the ledger from the drop-box (or a local .eml folder).

Only new messages (by Message-ID) are ever sent to the model, so re-runs are
cheap and idempotent.

    python -m sundaybrief.closures.run_extract --source local:./inbox --dry-run
    python -m sundaybrief.closures.run_extract --source imap
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import reader
from .extract import AnthropicExtractor
from .ledger import append_rows, expand_rows, load_processed, mark_processed

REPO_ROOT = Path(__file__).resolve().parents[3]
LEDGER = REPO_ROOT / "data" / "closures.jsonl"
PROCESSED = REPO_ROOT / "data" / "processed.json"
PROMPT = REPO_ROOT / "prompts" / "extract_closures.md"


def get_docs(source: str) -> list[reader.EmailDoc]:
    if source.startswith("local:"):
        return reader.read_local_dir(source.split(":", 1)[1])
    if source == "imap":
        import os
        return reader.read_imap(
            host="imap.gmail.com",
            user=os.environ["DROPBOX_IMAP_USER"],
            app_password=os.environ["DROPBOX_IMAP_PASSWORD"],
        )
    raise SystemExit(f"unknown source: {source!r}")


def run(source: str, extract_fn, model_tag: str, dry_run: bool) -> int:
    docs = get_docs(source)
    processed = load_processed(PROCESSED)
    print(f"{len(docs)} messages; {len(processed)} already processed", file=sys.stderr)

    new_rows = 0
    for doc in docs:
        if doc.message_id in processed:
            continue
        items = extract_fn(doc)
        # attribution: which attachment (if any) closures came from
        att = doc.attachments[0][0] if doc.attachments else None
        rows = []
        for item in items:
            rows += expand_rows(item, doc, att, model_tag)
        print(f"  {doc.source_subject[:48]:<50} -> {len(items)} fact(s), {len(rows)} row(s)",
              file=sys.stderr)
        if not dry_run:
            append_rows(rows, LEDGER)
            mark_processed(doc.message_id, PROCESSED)
        new_rows += len(rows)

    print(f"{'[dry-run] would add' if dry_run else 'added'} {new_rows} ledger rows", file=sys.stderr)
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", default="imap", help="'imap' or 'local:/path/to/eml/dir'")
    ap.add_argument("--model", default="claude-sonnet-5")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)

    from dotenv import load_dotenv
    load_dotenv(REPO_ROOT / ".env")

    from anthropic import Anthropic
    extractor = AnthropicExtractor(Anthropic(), args.model, PROMPT)
    tag = f"{args.model} / prompt-v1"
    return run(args.source, extractor, tag, args.dry_run)


if __name__ == "__main__":
    raise SystemExit(main())
