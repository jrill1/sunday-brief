# Closure ledger — design spec

The contract between the **email extractor** (LLM reads a daycare email → closure
facts) and the **briefing layer** (weekly, decides what to surface). The ledger
is the durable memory in between: extract each email *once*, persist, and let the
weekly brief read clean records instead of re-interpreting months of mail.

This doc specs three things: what the LLM emits, how code turns that into ledger
rows, and how the ledger is read (the "fold"). It does **not** cover the briefing
layer or the extraction prompt itself (next step).

---

## 1. LLM extraction output contract

**Input:** one email's body **plus any attachment text** (the real calendar often
lives in a PDF, not the body — so attachments are extracted to text and included).

**Output:** a strict JSON array — no prose, no markdown — of closure facts, or `[]`
if the email announces none. Each item:

```json
{
  "school": "Goddard",
  "type": "closure",
  "active": 1,
  "start_date": "2026-08-27",
  "end_date": "2026-08-28",
  "reason": "Faculty In-Service"
}
```

Rules:

- `school` — inferred from content (sender / letterhead), e.g. `Goddard`, `Le Parc`.
- `type` — `closure` (full day, no care) | `partial_closure` (half-day, early
  dismissal, or delayed open) | `note` (a date-bound logistics fact that does
  *not* change care availability — pack an item, a fee/form due, a dress-up
  day, a deadline). Describes the *kind* of fact.
- `active` — `1` = in effect (the default) | `0` = a previously announced
  closure/partial/note is now **canceled** or no longer applies. `type` and
  `active` are orthogonal: `type` is *what kind*, `active` is *whether it's in
  effect*. A cancellation is just a row with `active: 0` — never an edit of a
  prior row. This applies to `note` rows too (e.g. a canceled trip retracts its
  swimsuit reminder) — same fold, no special-casing.
- `start_date` / `end_date` — **as written**, ISO. Single day → the two are equal.
  A range stays a range here; **the LLM never enumerates the days** (it crosses
  year boundaries and models miscount — code expands it, see §2).
- `reason` — short human label. For `closure`/`partial_closure`: the cause
  ("Labor Day", "Winter Recess", "Faculty In-Service"). For `note`: a concrete,
  actionable summary ("Bring a swimsuit for Wednesday's field trip", "$25 field
  trip fee due").
- **Scope filter (the noise rule):** emit full closures, partial closures, and
  cancellations of those, plus `note`s for concrete date-bound action items
  (packing, fees/forms, deadlines, schedule/classroom transitions). **Exclude**
  anything with no specific date, and pure marketing/social content with no
  required action (newsletters, "register now" event promos, photo galleries).

Everything else (titles, child, provenance, day-expansion) is added by code so the
model has less to get right.

---

## 2. Persist / expand step (deterministic code)

For each extracted item, on the way into the ledger:

1. **Expand the range to one row per calendar day** — *every* day, including
   weekends. (A weekend closure is harmless noise the ledger records faithfully;
   the brief decides relevance later. We do not bake the school's operating days
   into storage.)
2. **Carry the announced span** on every resulting row (`span_start`/`span_end` =
   the original `start_date`/`end_date`), so the brief can re-collapse consecutive
   days back into "Dec 24–Jan 1 — Winter Recess" instead of nine lines.
3. **Carry `active`** onto each row unchanged (default `1`; `0` for cancellations).
4. **Compose the title** deterministically from type + active + reason
   (e.g. `"Goddard closed — Faculty In-Service"`; a canceled row →
   `"Goddard open — PD Day canceled"`). Gap-detector-friendly.
5. **Derive `child` from `school`** via config (Goddard→ChildB, Le Parc→ChildA).
   The LLM does not guess the child.
6. **Attach provenance** pulled from the forwarded message headers (see §3).
7. **Assign `record_id`** (unique per row) and stamp `date_added`.

Append the rows. **Never edit or delete** — the ledger is append-only. A
cancellation adds new `active: 0` rows; it does not touch the original closure rows.

---

## 3. Ledger row schema

One row **per day**. Append-only.

```json
{
  "record_id": "led_8f2c9a",
  "school": "Goddard",
  "child": "ChildB",
  "date": "2026-08-27",
  "type": "closure",
  "active": 1,
  "reason": "Faculty In-Service",
  "title": "Goddard closed — Faculty In-Service",
  "span_start": "2026-08-27",
  "span_end": "2026-08-28",
  "source_from": "MillburnNJ@goddardschools.com",
  "source_subject": "Reminder: School Closures for August",
  "source_date": "2026-08-03",
  "source_message_id": "<...@mail.gmail.com>",
  "source_attachment": null,
  "date_added": "2026-08-27T22:30:00Z",
  "extractor": "claude-sonnet-5 / prompt-v1"
}
```

Field roles:

- `date` — the **query + fold key**. Everything downstream asks "is this day closed?"
- `type` — `closure` | `partial_closure` | `note`: the kind of fact (for display).
- `active` — `1` in effect | `0` canceled. Read at fold time to decide the day.
- `span_start` / `span_end` — **display grouping** only (regroup consecutive days).
- `source_date` — when the **school announced it**. This is the **precedence key**
  for the fold (§4) — *not* `date_added`.
- `date_added` — when it entered the ledger. **Audit only.** (You forwarded these
  all on one day; that must not decide truth.)
- `source_message_id` — **idempotency**: the reader records processed message-ids
  and never re-appends the same email. This is also what keeps it incremental —
  only new mail is ever sent to the model, so API spend stays near zero.
- `source_attachment` — filename if the fact came from an attachment, else null.
- `extractor` — model + prompt version. Future-proofs **re-extraction**: if the
  prompt improves and we re-run the drop-box, rows are tagged so old and new
  extractions are distinguishable and re-foldable.

**Storage:** an append-only log — `data/closures.jsonl` (one row per line) is the
simple default; SQLite if it ever grows. It's derived from the drop-box (so
regenerable in a pinch) but persisted so extraction isn't re-paid, and it's
non-secret so it can travel to the Mini with the repo.

---

## 4. The fold (deriving effective state)

The ledger is raw history; **effective** state is computed on read:

1. Group all rows by `(school, date)`.
2. Within a group, the row with the **latest `source_date`** wins.
   Tiebreak on equal `source_date`: latest `date_added`. If *still* tied
   (pathological), **fail safe — treat the day as closed** and flag for review
   (missing a closure and having no coverage is worse than an unneeded backup plan).
3. The winner decides the day:
   - `active: 1` → closed (full) or reduced (partial), per `type` — or, for
     `note` rows, a standing logistics fact that doesn't affect care.
   - `active: 0` → **open** (a canceled closure/partial), or a retracted note.
   - **No rows for that date at all** → open, no note.

`effective_closures()` and `effective_notes()` both read the same fold but
filter on `type`, so a coverage-gap check never has to sift `note` rows out
itself.

Because the fold is keyed on `date`, everything composes without special-casing:

- **Confirmed closure** — Aug 27 has two `closure`/`active:1` rows (May calendar
  PDF + Aug reminder). Folds to closed, with two confirmations in the audit trail.
- **Cancellation** — July 30 has one `closure`/`active:0` row (announced Jul 15).
  Folds to open. Nothing was overwritten; the fold just reads the latest word.
- **Partial cancellation** — a 3-day span Aug 27–29, then a later `active:0` row on
  Aug 28: only that day flips → closed, open, closed. The broken run then displays
  as two items instead of one span. No special-casing.
- **Noise email** — tuition statement → `[]` → no rows.

Note this "multiple rows per date, fold to one" is intentional (confirmations +
audit) and is a *different* thing from the calendar cross-source dedup elsewhere.

---

## 5. Out of scope here (later)

- **The four Google calendars** (parents' work/personal) — and local event feeds —
  are **not** in this ledger. They're a **live snapshot** source, not an
  append-only announcement stream: each pull reflects current truth, a deleted
  event just vanishes next run, and there's no `source_date` precedence or
  cancellation lifecycle to fold. They stay as the live `Event` model and meet the
  **folded** closure state at the **briefing layer** — that's where a hand-entered
  "ChildB school closed" calendar event gets reconciled against the Goddard
  ledger's Aug 27 closure (same day, two sources → one line).
- **Briefing layer:** reading the fold for the target week, re-collapsing spans,
  deciding relevance (e.g. not flagging weekend closures as coverage gaps),
  reconciling against the calendars, writing the brief.
- **Extraction prompt:** the actual instructions sent to the model (next step).
- **Le Parc variant:** same contract; its emails skew toward same-day early-close
  notices (`partial_closure`), lower-value for a weekly brief but fit the schema.
