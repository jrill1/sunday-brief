# Sunday Brief Opener (iPhone)

The iOS counterpart of `../android-opener/`. Gmail's iPhone app has no reliable
way to be deep-linked to a search or a message, so — same trick as Android —
tapping a source link in the brief copies a Gmail search to the clipboard and
opens Gmail; you paste it into the search bar.

On iOS the "app" is just an iOS Shortcut named **`BriefOpen`**. For a recipient
whose `PUSHOVER_USER[_SPOUSE]_PLATFORM` is `ios`, `localize_links()` in
`../src/sundaybrief/closures/ledger.py` rewrites each source link to:

```
shortcuts://run-shortcut?name=BriefOpen&input=text&text=<url-encoded Gmail search>
```

The search is `from:<school domain> subject:"<first words of subject>"`, not a
Message-ID — the ledger's Message-IDs belong to the forward sent to the
drop-box, which isn't in the other parent's mailbox. The school's original
email is, so this finds it in either parent's own account.

## One-time setup (on the iPhone)

1. Open **Shortcuts** → **+** (new shortcut).
2. Name it exactly `BriefOpen` (case-sensitive; it must match `IOS_SHORTCUT_NAME`
   in `ledger.py`).
3. Add the action **Copy to Clipboard**. Tap its input and choose
   **Shortcut Input**.
4. Add the action **Open App** and choose **Gmail**.
5. Optional: add **Show Notification** — "Search copied — paste into Gmail search".
6. Done.

## Using it

Tap a source link in the brief → Shortcuts flashes → Gmail opens → tap the
search bar and paste. Gmail searches whichever account is currently selected
(top-right avatar), so it should be the personal account.

iOS asks "Allow Paste?" the first time Gmail reads the clipboard; choose
**Allow Paste** (or Settings → Gmail → Paste from Other Apps → Allow).

## Sending

Set `PUSHOVER_USER_SPOUSE_PLATFORM=ios` in the `.env` of the machine that sends
the brief (see `../.env.example`). Preview locally with
`python -m sundaybrief.run --dry-run --platform ios`.
