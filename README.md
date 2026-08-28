# sunday-brief

A weekly family calendar digest for a home Mac Mini. It pulls each parent's
Google calendars and the kids' daycare calendars, folds in local Maplewood /
South Orange / Millburn events, finds the moments that actually matter (daycare
closures, half-days, open weekend windows), and pushes a short brief to the
family via Pushover every Sunday morning.

It's designed as a **black box**: the Mini never logs into any real account. It
holds only read-only iCal capability URLs, a read-only key to a throwaway inbox,
and a send-only Pushover token. Compromising it leaks read access to specific
calendars — nothing more — and every key is independently revocable.

## How it works

```
ingest (iCal / RSS / wp-events)  ->  dedupe  ->  detect gaps  ->  summarize  ->  Pushover
```

Everything normalizes into one `Event` model, so the summarizer never cares
whether a thing came from a Google calendar or a library feed.

## Develop on your MacBook, run on the Mini

The repo is machine-agnostic — no paths are baked in. The flow:

**On your MacBook (development):**
```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -e .
cp .env.example .env && chmod 600 .env      # add secrets
python -m sundaybrief.run --dry-run          # iterate; sends nothing
```

**Ship it to the Mini** (pick one):
- Git: commit on the laptop, `git pull` on the Mini. `sources.yaml` travels with
  the repo; only `.env` is per-machine.
- rsync: `scripts/deploy.sh you@mini.local:~/sunday-brief` (skips `.env`, `.venv`, `.git`).

**On the Mini (first time only):**
```bash
python3 -m venv .venv && source .venv/bin/activate && pip install -e .
cp .env.example .env && chmod 600 .env       # the Mini keeps its own secrets
./scripts/install-launchd.sh                  # schedule the weekly run
```

Each machine builds its own `.venv` and keeps its own `.env` — never copy those
between machines.

## Start small: your two calendars first

`config/sources.yaml` ships with just your work + personal calendars enabled.
Get those two flowing, confirm the brief looks right, then uncomment your
spouse's two entries (and add their URLs to `.env`). Daycare and local sources
come after — see `config/sources.example.yaml` for the full menu.

## Wiring up each source

**Your Google calendars.** In Google Calendar: Settings → pick the calendar →
*Integrate calendar* → copy the **Secret address in iCal format**. Paste it into
`.env` (`ICAL_ME_WORK`, `ICAL_ME_PERSONAL`). No OAuth, no login. To revoke
access later, click *Reset* on that secret address. If a **work** Workspace
account has this disabled, share that calendar into a personal one you own and
use the personal calendar's secret URL.

> Never paste a bearer iCal URL directly into `sources.yaml` — keep it in `.env`
> and reference it with `secret:`. That's what makes `sources.yaml` safe to commit.

**Brightwheel (one kid).** Only exposes a feed if your center enabled
"add to personal calendar" syncing. If it's there, paste that iCal URL as
`ICAL_BRIGHTWHEEL` and enable the source; if not, ask the center to toggle it on.

**Goddard Family Hub (other kid).** No feed. Generate a term `.ics` once from the
school-year PDF, drop it at `data/goddard-term.ics`, enable that source. A
once-a-term chore since closures are known months ahead.

**Local events.** Prefer real feeds — grab the iCal "subscribe" link off each
LibCal / LibNet page. For WordPress calendars, try `type: wp-events` against the
site root, fall back to `/feed/` as `type: rss`.

**Closure-email drop-box.** A separate pipeline (`sundaybrief.closures`) reads
school closure emails and extracts structured facts into `data/closures.jsonl`
— see `docs/closure-ledger.md` for the design. To point it at a real inbox
instead of the local `.eml` test fixtures:

1. Create a dedicated Gmail account (or reuse an existing throwaway one) and
   forward/CC school emails to it. Read-only usage — the code only ever fetches.
2. Turn on 2-Step Verification for that account, then generate an App Password
   at `myaccount.google.com/apppasswords`.
3. Add `DROPBOX_IMAP_USER` (the address) and `DROPBOX_IMAP_PASSWORD` (the
   16-char App Password, not the real account password) to `.env`.
4. Test it: `python -m sundaybrief.closures.run_extract --source imap --dry-run`

This pipeline isn't reconciled into the weekly brief yet — `run.py` doesn't
read `data/closures.jsonl` — that's the next piece to build.

## Summary style

In `config/sources.yaml`:
- `style: templated` — deterministic, compact, fits Pushover's 1024-char limit.
  No API key needed. Default.
- `style: narrative` — hands the week's facts to Claude for a warm prose brief.
  Needs `ANTHROPIC_API_KEY`. Falls back to templated on any failure.

## Scheduling (launchd)

`./scripts/install-launchd.sh` generates the plist with the correct paths for
wherever the repo lives and loads it (Sundays at 6am). It runs as a LaunchAgent
under your logged-in user — the right choice for an auto-login Mini, and required
if you use the Keychain option.

```bash
launchctl start com.sunday.brief    # run once now to test
tail -f brief.log
```

## Secrets: .env vs Keychain

Default is a `chmod 600 .env` file — it survives a launchd run even when no GUI
session has unlocked the login Keychain. To harden, store the same names in the
Keychain and set `SUNDAYBRIEF_USE_KEYCHAIN=1`:

```bash
security add-generic-password -s sunday-brief -a PUSHOVER_TOKEN -w 'your-token'
```

Keychain reads only work when the login Keychain is unlocked (your user logged
in) — hence the LaunchAgent, not a LaunchDaemon.

## What's stubbed / next

- `ingest.headless` (Playwright) for JS-only calendars — raises `NotImplementedError`
  by design; check for a feed first.
- `wp-events` is best-effort; plugin versions vary.
- The closure ledger (`data/closures.jsonl`) isn't read by the weekly brief yet —
  it's populated by `sundaybrief.closures.run_extract` but not reconciled against
  calendar events. See "Out of scope here (later)" in `docs/closure-ledger.md`.

## Test

```bash
python tests/smoke_test.py     # offline, no secrets — exercises the whole pipeline
```
