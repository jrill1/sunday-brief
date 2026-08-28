"""Pull the original email behind one closures-ledger row.

Given a `record_id` or `source_message_id` from data/closures.jsonl (or the
CSV export), fetch the matching email from the drop-box over IMAP and save it
as a local .eml so you can open it directly.

    python scripts/find_source_email.py led_ce5984c8ed
    python scripts/find_source_email.py "<CACtDR0onLbJntG3n64jFaUpjzj3ApXKX88ScH10RK_p8KMJN_w@mail.gmail.com>"
"""
from __future__ import annotations

import argparse
import email
import imaplib
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

from dotenv import load_dotenv  # noqa: E402

from sundaybrief.secrets import get_secret  # noqa: E402


def resolve_message_id(token: str) -> str:
    """Accept either a record_id or a raw Message-ID and return the Message-ID."""
    if not token.startswith("led_"):
        return token
    ledger = REPO_ROOT / "data" / "closures.jsonl"
    for line in ledger.read_text().splitlines():
        row = json.loads(line)
        if row["record_id"] == token:
            return row["source_message_id"]
    raise SystemExit(f"record_id {token!r} not found in {ledger}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("id", help="a record_id (led_...) or a source_message_id")
    ap.add_argument("--out", default=None, help="output .eml path (default: data/found-email.eml)")
    args = ap.parse_args()

    load_dotenv(REPO_ROOT / ".env")
    message_id = resolve_message_id(args.id)

    host = get_secret("DROPBOX_IMAP_HOST", default="imap.gmail.com")
    user = get_secret("DROPBOX_IMAP_USER")
    app_password = get_secret("DROPBOX_IMAP_PASSWORD")

    conn = imaplib.IMAP4_SSL(host)
    try:
        conn.login(user, app_password)
        conn.select("INBOX", readonly=True)
        typ, data = conn.search(None, "HEADER", "Message-ID", message_id)
        ids = data[0].split()
        if not ids:
            raise SystemExit(f"no message found with Message-ID {message_id!r}")
        typ, msg_data = conn.fetch(ids[-1], "(RFC822)")
        raw = msg_data[0][1]
    finally:
        conn.logout()

    out_path = Path(args.out) if args.out else REPO_ROOT / "data" / "found-email.eml"
    out_path.write_bytes(raw)

    msg = email.message_from_bytes(raw)
    print(f"Subject: {msg.get('Subject')}")
    print(f"From:    {msg.get('From')}")
    print(f"Date:    {msg.get('Date')}")
    print(f"Saved to {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
