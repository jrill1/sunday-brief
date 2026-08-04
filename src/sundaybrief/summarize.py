"""Turn the week's signals into a Pushover-ready message.

Two styles, matching what we mocked up:
  - templated: deterministic, compact, fits Pushover's 1024-char body natively.
  - narrative: hand it to Claude for a warm prose brief; fall back to templated
    if the key is missing, the call fails, or the output blows the limit.

Pushover HTML is a *tiny* subset: <b> <i> <u> <font color> <a href>, plus \\n
for line breaks. No lists, no headings — so we fake structure with bold labels.
"""
from __future__ import annotations

import html

from .gaps import WeekSignals

PUSHOVER_LIMIT = 1024


def _fmt_day(d) -> str:
    return d.strftime("%a")  # Mon, Tue, ...


def _line(event) -> str:
    when = event.timelabel()
    title = html.escape(event.title)
    return f"· {_fmt_day(event.day)} {when} — {title}"


def build_templated(sig: WeekSignals) -> tuple[str, str]:
    """Return (title, html_message), guaranteed <= PUSHOVER_LIMIT chars."""
    start = sig.window_start.strftime("%b %-d")
    end = (sig.window_end).strftime("%b %-d")
    title = f"Family week · {start}–{end}"

    parts: list[str] = []

    if sig.closures or sig.half_days:
        flags = []
        for e in sig.closures:
            flags.append(f"{_fmt_day(e.day)}: {html.escape(e.title)}")
        for e in sig.half_days:
            flags.append(f"{_fmt_day(e.day)}: {html.escape(e.title)}")
        parts.append('<b><font color="#a32d2d">Daycare</font></b>\n' + "\n".join(flags))

    if sig.focus_days:
        opens = ", ".join(_fmt_day(d) for d in sig.focus_days)
        parts.append(f"<b>Open / focus days</b>\n{opens}")

    if sig.picks:
        picks = "\n".join(_line(e) for e in sig.picks[:5])
        parts.append(f"<b>Local picks</b>\n{picks}")

    if not parts:
        parts.append("Nothing flagged this week — calendars look clear.")

    message = "\n\n".join(parts)
    if len(message) > PUSHOVER_LIMIT:
        message = message[: PUSHOVER_LIMIT - 1].rstrip() + "…"
    return title, message


def _brief_facts(sig: WeekSignals) -> str:
    """Compact plain-text digest handed to Claude as grounding."""
    lines = []
    if sig.closures:
        lines.append("Daycare closures: " + "; ".join(f"{_fmt_day(e.day)} {e.title}" for e in sig.closures))
    if sig.half_days:
        lines.append("Daycare half-days: " + "; ".join(f"{_fmt_day(e.day)} {e.title}" for e in sig.half_days))
    if sig.parent_events:
        lines.append("Parent events: " + "; ".join(
            f"{_fmt_day(e.day)} {e.timelabel()} {e.person or e.category} {e.title}" for e in sig.parent_events[:12]))
    if sig.picks:
        lines.append("Local options on open days: " + "; ".join(
            f"{_fmt_day(e.day)} {e.timelabel()} {e.title}" for e in sig.picks[:8]))
    return "\n".join(lines) if lines else "No notable events this week."


def build_narrative(sig: WeekSignals, model: str, api_key: str) -> str | None:
    """Ask Claude for a warm <900-char brief. Returns None on any failure so
    the caller can fall back to the templated version.
    """
    try:
        from anthropic import Anthropic
    except ImportError:
        return None

    facts = _brief_facts(sig)
    prompt = (
        "You are writing a warm, practical weekly brief for a family with two "
        "young kids (ages 4 and 1.5) in Maplewood, NJ. Below are this week's "
        "facts. Write a friendly heads-up in UNDER 850 characters, plain text "
        "(no markdown). Lead with any daycare closure or coverage gap, then "
        "suggest which local options fit the open days and suit young kids. Be "
        "concrete and kind; skip anything not in the facts.\n\n"
        f"FACTS:\n{facts}"
    )
    try:
        client = Anthropic(api_key=api_key)
        resp = client.messages.create(
            model=model,
            max_tokens=500,
            messages=[{"role": "user", "content": prompt}],
        )
        text = "".join(block.text for block in resp.content if block.type == "text").strip()
    except Exception:
        return None

    if not text or len(text) > PUSHOVER_LIMIT:
        return None
    return text
