"""Turn raw emails into a common EmailDoc the extractor can consume.

Handles the wrinkle that these are *forwarded* messages: the envelope From is
you (justinrill), but the school's real sender/date/subject live inside the
"---------- Forwarded message ---------" block in the body. We pull those out so
provenance (source_from, source_date) reflects the school, not the forward.

Two sources, same output: a local directory of .eml files (dev) and the drop-box
over IMAP (production).
"""
from __future__ import annotations

import email
import glob
import re
from dataclasses import dataclass, field
from datetime import date
from email import policy
from pathlib import Path

from dateutil import parser as dateparser


@dataclass
class EmailDoc:
    message_id: str
    source_from: str            # original school sender (from forwarded header)
    source_subject: str
    source_date: date | None    # when the school sent it — precedence key
    body: str
    attachments: list[tuple[str, str]] = field(default_factory=list)  # (filename, text)


_FWD_FROM = re.compile(r"^From:\s*(.+)$", re.MULTILINE)
_FWD_DATE = re.compile(r"^Date:\s*(.+)$", re.MULTILINE)
_FWD_SUBJ = re.compile(r"^Subject:\s*(.+)$", re.MULTILINE)


def _parse_forwarded(body: str) -> tuple[str | None, str | None, date | None]:
    """Pull original From/Subject/Date out of a forwarded-message block."""
    idx = body.find("Forwarded message")
    if idx == -1:
        return None, None, None
    header = body[idx: idx + 600]  # the header lines sit just after the marker
    frm = _FWD_FROM.search(header)
    subj = _FWD_SUBJ.search(header)
    dat = _FWD_DATE.search(header)
    d = None
    if dat:
        raw = dat.group(1).strip().replace(" at ", " ")
        try:
            d = dateparser.parse(raw).date()
        except (ValueError, OverflowError):
            d = None
    return (
        frm.group(1).strip().strip("<>") if frm else None,
        subj.group(1).strip() if subj else None,
        d,
    )


def _extract_pdf_text(data: bytes) -> str:
    import io
    import pdfplumber
    out = []
    with pdfplumber.open(io.BytesIO(data)) as pdf:
        for page in pdf.pages:
            out.append(page.extract_text() or "")
    return "\n".join(out).strip()


def _doc_from_message(msg) -> EmailDoc:
    # plain-text body
    body = ""
    if msg.is_multipart():
        for part in msg.walk():
            if part.get_content_type() == "text/plain":
                body = part.get_content()
                break
    else:
        body = msg.get_content()
    body = (body or "").strip()

    # attachments (PDF text)
    attachments = []
    if msg.is_multipart():
        for part in msg.walk():
            if part.get_content_type() == "application/pdf":
                name = part.get_filename() or "attachment.pdf"
                try:
                    attachments.append((name, _extract_pdf_text(part.get_payload(decode=True))))
                except Exception as exc:  # a bad PDF shouldn't sink the email
                    attachments.append((name, f"[PDF extraction failed: {exc}]"))

    # provenance: prefer the forwarded header, fall back to the envelope
    f_from, f_subj, f_date = _parse_forwarded(body)
    env_date = None
    if msg.get("Date"):
        try:
            env_date = email.utils.parsedate_to_datetime(msg.get("Date")).date()
        except (TypeError, ValueError):
            env_date = None

    subject = f_subj or (msg.get("Subject") or "").removeprefix("Fwd: ").strip()
    mid = (msg.get("Message-ID") or "").strip()
    if not mid:  # some saved .eml lack a Message-ID; synthesize a stable one
        import hashlib
        seed = f"{subject}|{f_date or env_date}|{f_from}"
        mid = "syn-" + hashlib.sha1(seed.encode()).hexdigest()[:16]

    return EmailDoc(
        message_id=mid,
        source_from=f_from or (msg.get("From") or ""),
        source_subject=subject,
        source_date=f_date or env_date,
        body=body,
        attachments=attachments,
    )


def read_local_dir(path: str | Path) -> list[EmailDoc]:
    docs = []
    for p in sorted(glob.glob(str(Path(path) / "*.eml"))):
        with open(p, "rb") as f:
            docs.append(_doc_from_message(email.message_from_binary_file(f, policy=policy.default)))
    return docs


def read_imap(host: str, user: str, app_password: str, mailbox: str = "INBOX") -> list[EmailDoc]:
    """Production source: the drop-box over IMAP (read-only usage).

    Not exercised in the offline test (no network to Gmail from the sandbox),
    but this is the code the Mini runs. Uses only fetch/search — never delete.
    """
    import imaplib
    docs = []
    conn = imaplib.IMAP4_SSL(host)
    try:
        conn.login(user, app_password)
        conn.select(mailbox, readonly=True)
        _, ids = conn.search(None, "ALL")
        for mid in ids[0].split():
            _, data = conn.fetch(mid, "(RFC822)")
            raw = data[0][1]
            docs.append(_doc_from_message(email.message_from_bytes(raw, policy=policy.default)))
    finally:
        conn.logout()
    return docs
