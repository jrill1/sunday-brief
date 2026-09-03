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
import re
from datetime import date, timedelta

from dateutil.relativedelta import relativedelta

from .gaps import WeekSignals, days_covered

PUSHOVER_LIMIT = 1024


def _child_ages_phrase(children: list[dict] | None) -> str:
    """"ChildA (4y) and ChildB (1y 7mo)" — computed fresh from each
    child's age-in-months as of a reference date (config's `summary.children`:
    [{"name","age_months","as_of"}]), rather than a hardcoded age string, so
    it never needs manual updating and can't silently drift out of date —
    every brief just recomputes "now" from that one fixed reference point.
    Falls back to a generic phrase if no children are configured.
    """
    if not children:
        return "two young kids"
    parts = []
    today = date.today()
    for c in children:
        as_of = date.fromisoformat(c["as_of"])
        elapsed = relativedelta(today, as_of)
        total_months = int(c["age_months"]) + elapsed.years * 12 + elapsed.months
        years, months = divmod(max(total_months, 0), 12)
        age = f"{years}y" + (f" {months}mo" if months else "")
        parts.append(f"{c['name']} ({age})")
    if len(parts) == 1:
        return parts[0]
    return ", ".join(parts[:-1]) + f" and {parts[-1]}"


def _fmt_day(d) -> str:
    return d.strftime("%a")  # Mon, Tue, ...


def _line(event) -> str:
    when = event.timelabel()
    title = html.escape(event.title)
    return f"· {_fmt_day(event.day)} {when} — {title}"


def _day_range(event) -> str:
    """A single day label ("Mon"), or a range ("Mon–Fri") for a collapsed
    multi-day closure/note (see gaps._collapse_events). Every event reaching
    WeekSignals.closures/half_days/notes has already passed through that
    collapse step, so `.end` is always inclusive here, whatever the source."""
    if event.end and event.end.date() != event.day:
        return f"{_fmt_day(event.day)}–{_fmt_day(event.end.date())}"
    return _fmt_day(event.day)


def _flag_line(event) -> str:
    """A closure/half-day/note line, linked to its source when there is one
    (a ledger-derived item; a calendar-only one has no url)."""
    title = html.escape(event.title)
    if event.url:
        title = f'<a href="{html.escape(event.url)}">{title}</a>'
    return f"{_day_range(event)}: {title}"


def build_templated(sig: WeekSignals) -> tuple[str, str]:
    """Return (title, html_message), guaranteed <= PUSHOVER_LIMIT chars."""
    start = sig.window_start.strftime("%b %-d")
    end = (sig.window_end).strftime("%b %-d")
    title = f"Sunday Brief · {start}–{end}"

    parts: list[str] = []

    if sig.closures or sig.half_days:
        flags = [_flag_line(e) for e in sig.closures] + [_flag_line(e) for e in sig.half_days]
        parts.append('<b><font color="#a32d2d">Daycare</font></b>\n' + "\n".join(flags))

    if sig.focus_days:
        opens = ", ".join(_fmt_day(d) for d in sig.focus_days)
        parts.append(f"<b>Open / focus days</b>\n{opens}")

    if sig.picks:
        picks = "\n".join(_line(e) for e in sig.picks[:5])
        parts.append(f"<b>Local picks</b>\n{picks}")

    if sig.notes:
        notes = "\n".join(_flag_line(e) for e in sig.notes[:5])
        parts.append(f"<b>Notes</b>\n{notes}")

    if not parts:
        parts.append("Nothing flagged this week — calendars look clear.")

    message = "\n\n".join(parts)
    if len(message) > PUSHOVER_LIMIT:
        message = message[: PUSHOVER_LIMIT - 1].rstrip() + "…"
    return title, message


def _brief_facts(sig: WeekSignals, names: dict | None = None) -> str:
    """Day-by-day digest handed to Claude as grounding.

    Grouped by calendar day rather than one flat list per category, so a
    closure and that same day's parent-calendar load sit next to each other —
    the model can see a coverage conflict directly instead of having to
    correlate two separate unordered lists itself. Deliberately *not*
    truncated: judging whether a day is genuinely packed needs the whole
    day's events, not a sample of the first 12.

    `names` maps person tags ("me"/"spouse") to real names, so the digest
    already reads "[Justin]"/"[Maria]" — the model can use them directly
    rather than needing to translate a generic tag into a name itself.
    """
    names = names or {}
    by_day: dict = {}

    def add(d, text, url, context="", ages=""):
        if context:
            text += f" (CONTEXT: {context})"
        if ages:
            text += f" (AGES: {ages})"
        if url:
            text += f" (LINK: {url})"
        by_day.setdefault(d, []).append(text)

    for e in sig.closures:
        for d in days_covered(e):
            add(d, f"CLOSURE: {e.title}", e.url, e.context)
    for e in sig.half_days:
        for d in days_covered(e):
            add(d, f"HALF-DAY: {e.title}", e.url, e.context)
    for e in sig.notes:
        for d in days_covered(e):
            add(d, f"NOTE: {e.title}", e.url, e.context)
    for e in sig.parent_events:
        who = names.get(e.person, e.person) or e.category
        add(e.day, f"{e.timelabel()} [{who}] {e.title}", e.url)
    for e in sig.picks:
        if re.match(r"(?i)^closed\b", e.title.strip()):
            continue  # a venue-closure notice, not something to suggest
        if e.age_group.strip().lower() == "adults":
            continue  # an explicit, unambiguous exclude — not a judgment call
        add(e.day, f"LOCAL OPTION: {e.timelabel()} {e.title}", e.url, ages=e.age_group)

    if not by_day:
        return "No notable events this week."

    lines = []
    d = sig.window_start.date()
    while d <= sig.window_end.date():
        if d in by_day:
            lines.append(f"{d:%a %b %-d}: " + "; ".join(by_day[d]))
        d += timedelta(days=1)
    return "\n".join(lines)


_LINKS_DELIM = "---LINKS---"


def _provenance(url: str) -> str:
    """Generic fallback source tag for a citation url, for the links message.
    Calendar links get a more specific tag from _gcal_provenance when possible
    (who owns the calendar it's on) — this is only the fallback for urls that
    function doesn't recognize (e.g. a local-pick page url)."""
    if url.startswith("sundaybrief://"):
        return "Gmail"
    if "calendar.google.com" in url:
        return "Gcal"
    return ""


def _gcal_provenance(sig: WeekSignals, names: dict) -> dict[str, str]:
    """Map each parent-calendar event's url to a specific provenance tag.

    Unlike a forwarded school email (visible to both parents once it hits the
    shared drop-box), a calendar event is only visible to whoever's calendar
    it's actually on. dedupe.py merges an event across sources when the same
    title+day shows up on more than one feed — so an event whose `.sources`
    spans both "My ..." and "Spouse ..." feeds is a shared invite both parents
    can open ("Gcal"); one that only ever showed up on a single parent's own
    feed gets that parent's name (and "work" if it's a work-calendar event),
    since the other parent likely can't click into it at all.

    Relies on sources.yaml's current "My ..." / "Spouse ..." naming
    convention for calendar sources — update this if those names change.
    """
    mapping: dict[str, str] = {}
    for e in sig.parent_events:
        if not e.url:
            continue
        persons = set()
        for src in e.sources:
            if src.startswith("Spouse"):
                persons.add("spouse")
            elif src.startswith("My "):
                persons.add("me")
        if len(persons) >= 2:
            mapping[e.url] = "Gcal"
        else:
            name = names.get(e.person, e.person) or "?"
            mapping[e.url] = f"{name} work Gcal" if e.category == "work" else f"{name} Gcal"
    return mapping


def _parse_narrative_response(
    raw: str, gcal_labels: dict[str, str] | None = None,
) -> tuple[str, str | None] | None:
    """Split the model's citation-style response into (prose, links_message).

    Expects prose using [1]/[2]/... markers, then a "---LINKS---" line, then
    one "[n] label | url" per line. A missing/empty links section just means
    no citations that week — not a failure, so it returns (prose, None). Only
    a missing/empty prose half is treated as a real failure (None), so the
    caller falls back to templated rather than sending something broken.

    The model sometimes cites a number in the prose but drops it from its own
    links list — any prose marker with no matching link entry gets stripped
    out, so the sent message never has a dangling "[4]" pointing at nothing.

    The provenance tag ("Gmail"/"Gcal") is derived from the url ourselves
    rather than asked of the model, so it's always correct even if the model
    mislabels or omits it.
    """
    prose, _, rest = raw.partition(_LINKS_DELIM)
    prose = prose.strip()
    if not prose:
        return None
    triples = re.findall(r"\[(\d+)\]\s*(.+?)\s*\|\s*(\S+)", rest)
    have = {n for n, _, _ in triples}
    prose = re.sub(r"\[(\d+)\]", lambda m: m.group(0) if m.group(1) in have else "", prose)
    prose = re.sub(r"\s+([.,;:])", r"\1", prose)
    prose = re.sub(r"[ \t]{2,}", " ", prose).strip()
    if not triples:
        return prose, None
    gcal_labels = gcal_labels or {}
    lines = []
    for n, label, url in triples:
        tag = gcal_labels.get(url) or _provenance(url)
        text = f"{label} ({tag})" if tag else label
        lines.append(f'[{n}] <a href="{html.escape(url)}">{html.escape(text)}</a>')
    return prose, "\n".join(lines)


def build_narrative(
    sig: WeekSignals, model: str, api_key: str, names: dict | None = None,
    children: list[dict] | None = None,
) -> tuple[str, str | None] | None:
    """Ask Claude for a warm <900-char brief, cited like footnotes, split into
    (message, links_message). `links_message` is a second, separate Pushover
    push listing the citations as real links — a full URL inline would blow
    the first message's budget, but a "[1]" marker costs nothing, and the
    follow-up message gets its own independent 1024-char budget for the
    links themselves. Returns None on any failure so the caller falls back to
    the templated version; `links_message` alone can be None even on success
    if the model didn't cite anything that week.

    `names` (optional) maps {"me": "...", "spouse": "..."} to real names —
    when given, the brief is written in third person about both of them by
    name, since it's meant to be read by both parents, not addressed to one.
    """
    try:
        from anthropic import Anthropic
    except ImportError:
        return None

    names = names or {}
    facts = _brief_facts(sig, names)
    me_name, spouse_name = names.get("me"), names.get("spouse")
    people_line = (
        f'The two parents are {me_name} and {spouse_name}, and this brief is '
        f'read by both of them together. Refer to each by name, always in '
        f'third person — never "you," "I," "your spouse," or "me."\n\n'
        if me_name and spouse_name else ""
    )
    kids_phrase = _child_ages_phrase(children)
    prompt = (
        "You are writing a warm, practical weekly heads-up for a family with "
        f"two young kids ({kids_phrase}) in Maplewood, NJ — like a friend "
        "summarizing the week for them, not writing a report. "
        f"{people_line}"
        "Below is a day-by-day digest of this week for you to read and "
        "summarize: CLOSURE/HALF-DAY/NOTE facts from school sources, each "
        "parent's own calendar events tagged by name, and LOCAL OPTION facts "
        "for nearby events. Some facts carry a (LINK: url) — a link back to "
        "that fact's source (the calendar event or the school email). Some "
        "also carry a (CONTEXT: ...) — extra detail the school's email itself "
        "stated (a reschedule, unusual urgency or repetition). When present, "
        "weave it naturally into the sentence rather than bolting it on — it's "
        "often the most useful part of the fact. A LOCAL OPTION may carry a "
        "(AGES: ...) — the source's own age-group tag; trust it over guessing "
        "from the title (e.g. AGES: Adults means it does NOT fit young kids, "
        "even if the title sounds otherwise), and only cite a LOCAL OPTION as "
        "the pick for kids if it's genuinely age-appropriate.\n\n"
        "This message will be split into two separate text messages, so use "
        "footnote-style citations instead of inline links: when you mention a "
        "fact that has a (LINK: url), put a bracketed number right after it "
        "in your sentence, like \"...closed for the week [1]...\", numbered in "
        "the order they first appear. Never cite a fact with no (LINK: url), "
        "and never invent a url. Then, after your prose, on their own lines, "
        "list every number you used with a short (2-5 word) label for what "
        "it links to — no trailing punctuation — then the url, separated by "
        "\" | \":\n"
        f"{_LINKS_DELIM}\n"
        "[1] <short label> | <the url for citation 1>\n"
        "[2] <short label> | <the url for citation 2>\n"
        "(etc. — omit this whole section if you didn't cite anything)\n\n"
        "HARD LIMIT for the prose part: under 850 characters, no exceptions "
        "(the links list after the delimiter doesn't count against this). "
        "Plain prose only — NO markdown, NO headers, NO bullet points, NO "
        "bold/asterisks, NO emoji. 3-5 short sentences, like a text message. "
        "Cite at most 5 facts total — pick the ones worth following up on, "
        "not every fact that happens to have a link. Put a blank line (two "
        "newlines) between each of the numbered topics below when more than "
        "one applies — closures/coverage-conflict, school milestones, and "
        "personal highlights each get their own short paragraph rather than "
        "running together in one block.\n\n"
        "You are summarizing the two or three things that actually matter — "
        "you are NOT reproducing the digest day by day. Most days won't get a "
        "mention at all. Cover, in order of importance, only what applies:\n"
        "1. Any CLOSURE or HALF-DAY. Check that same day's parent events: if "
        "one parent has several back-to-back meetings, mention that as a "
        "coverage conflict worth planning around. If the day is actually "
        "light, don't invent a conflict.\n"
        "2. Any other school-related event worth knowing about even though "
        "it's not a closure — a classroom change, a milestone, a schedule "
        "shift. Skip routine same-day appointments (doctor visits, pickups) "
        "unless something about them stands out.\n"
        "3. One or two genuinely personal highlights — birthdays, social "
        "plans, personal appointments. Never mention internal work meetings, "
        "standups, or syncs, even ones on a parent's own calendar, if they're "
        "clearly work business, not family business.\n"
        "4. If there are LOCAL OPTION facts, one pick that fits young kids.\n\n"
        "Use only what's in the facts below — never invent an event or a "
        "conflict that isn't there.\n\n"
        f"FACTS (day-by-day):\n{facts}"
    )
    try:
        client = Anthropic(api_key=api_key)
        resp = client.messages.create(
            model=model,
            max_tokens=2000,
            thinking={"type": "disabled"},
            messages=[{"role": "user", "content": prompt}],
        )
        raw = "".join(block.text for block in resp.content if block.type == "text").strip()
    except Exception:
        return None

    parsed = _parse_narrative_response(raw, gcal_labels=_gcal_provenance(sig, names))
    if parsed is None:
        return None
    message, links_message = parsed
    if len(message) > PUSHOVER_LIMIT:
        return None
    if links_message and len(links_message) > PUSHOVER_LIMIT:
        links_message = None  # drop the follow-up rather than failing the whole brief
    return message, links_message


_MAX_PICKS = 8


def build_weekend_picks(
    sig: WeekSignals, model: str, api_key: str, children: list[dict] | None = None,
) -> list[str] | None:
    """Ask Claude to pick up to 8 genuinely kid-friendly events from this
    week's weekend/closure-day local candidates (sig.picks — already scoped
    to focus days by gaps.analyze()), for a third "Weekend/extracurricular
    picks" Pushover push. Returns a list of message chunks (each within
    PUSHOVER_LIMIT — usually just one), or None if there's nothing to send
    (no candidates, none qualify, or the call fails).

    The model only ever SELECTS by number from the candidate list — it never
    rewrites a title or invents a link. All display text (day, time, title,
    url) comes straight from the actual Event objects, so there's no chance
    of a hallucinated detail landing in a real notification.
    """
    # Two deterministic filters, rather than trusting the model to catch
    # these every time (it doesn't always, even with an explicit rule):
    # a venue-closure notice ("CLOSED - Labor Day Weekend") isn't an event to
    # suggest, and a source-tagged "ages: Adults" is an explicit, unambiguous
    # signal — not a judgment call the way an untagged title is.
    candidates_pool = [
        e for e in sig.picks
        if not re.match(r"(?i)^closed\b", e.title.strip())
        and e.age_group.strip().lower() != "adults"
    ]
    if not candidates_pool:
        return None
    try:
        from anthropic import Anthropic
    except ImportError:
        return None

    numbered = list(enumerate(candidates_pool, 1))
    candidates = "\n".join(
        f"{n}. {e.day:%a %b %-d} {e.timelabel()} — {e.title} @ {e.source}"
        + (f" (ages: {e.age_group})" if e.age_group else "")
        for n, e in numbered
    )
    kids_phrase = _child_ages_phrase(children)
    prompt = (
        "You are picking WEEKEND / EXTRACURRICULAR event suggestions for a "
        f"family with two young kids in Maplewood, NJ: {kids_phrase}. Below "
        "is this week's full raw candidate list — every local event "
        "happening on a weekend day or a daycare-closure day.\n\n"
        "PRIORITIZE genuinely kid- or family-friendly ones first — "
        "storytimes, craft/art activities, family movies, festivals, "
        "toddler/preschool programs, anything that would actually work for "
        "either kid, or both together, at their current ages. But don't "
        "RESTRICT to only those — a casual, family-viable outing that isn't "
        "specifically a kids' program still counts (a brewery's trivia "
        "night or an open-turntables session, for instance, is fair game as "
        "a \"stop by for 45 minutes\" family outing), just rank it below "
        "anything that's actually built for kids. Only exclude something a "
        "family genuinely couldn't attend or get anything out of — an "
        "explicitly adults-only/21+ event, a business/committee meeting, a "
        "structured program whose own (ages: ...) tag says it's for a "
        "different audience (adults, teens, seniors), a routine closure "
        "notice, or anything that just isn't an event at all.\n\n"
        f"Pick up to {_MAX_PICKS}, best first — kid-specific picks ranked "
        "above general family-viable ones. Fewer is fine — never pad with a "
        "weak pick just to hit the count, and output nothing at all if "
        "nothing on the list is even family-viable. The list below is the "
        "COMPLETE candidate list for this week, however long or short it "
        "is — some weeks have just one or two candidates, or none at all; "
        "that's normal, not a truncated or partial list.\n\n"
        "Each candidate is a day/time/title @ venue, and SOME also carry a "
        "real (ages: ...) tag straight from the source — trust that tag when "
        "it's there (e.g. \"ages: Adults\" means it's a program built "
        "specifically for adults, not just adult-leaning — exclude it; "
        "\"ages: Toddlers, Pre-K\" is a clear kid-specific include). When "
        "there's no (ages: ...) tag, judge from the title AND venue "
        "together — a brewery/bar venue makes an otherwise-neutral title "
        "(\"Open Turntables\", \"Trivia Night\") adult-leaning rather than "
        "kid-specific, which is fine, just lower-priority, not an automatic "
        "exclude. Never ask for more information or comment on the list or "
        "on missing detail — just make your best call from what's given and "
        "output your answer.\n\n"
        "Output ONLY the candidate numbers, one per line, best first, "
        "nothing else — no titles, no explanation, no extra text.\n\n"
        f"CANDIDATES:\n{candidates}"
    )
    try:
        client = Anthropic(api_key=api_key)
        resp = client.messages.create(
            model=model,
            max_tokens=500,
            thinking={"type": "disabled"},
            messages=[{"role": "user", "content": prompt}],
        )
        raw = "".join(block.text for block in resp.content if block.type == "text").strip()
    except Exception:
        return None

    by_num = dict(numbered)
    picked = []
    for line in raw.splitlines():
        # Usually just a bare number, but the model sometimes adds its own
        # "1. 4" rank prefix despite being told not to — take the LAST
        # number on the line either way, which handles both correctly.
        nums = re.findall(r"\d+", line)
        if not nums:
            continue
        n = int(nums[-1])
        if n in by_num and by_num[n] not in picked:
            picked.append(by_num[n])
    picked = picked[:_MAX_PICKS]
    if not picked:
        return None

    lines = []
    for e in picked:
        when = f"{e.day:%a %-m/%-d} {e.timelabel()}"
        title = html.escape(e.title.title())
        venue = f" @ {html.escape(e.source)}" if e.source else ""
        if e.url:
            lines.append(f'<b>{when}</b> <a href="{html.escape(e.url)}">{title}</a>{venue}')
        else:
            lines.append(f"<b>{when}</b> {title}{venue}")

    messages, current = [], ""
    for line in lines:
        candidate = f"{current}\n{line}" if current else line
        if len(candidate) > PUSHOVER_LIMIT and current:
            messages.append(current)
            current = line
        else:
            current = candidate
    if current:
        messages.append(current)
    return messages or None
    return message, links_message
