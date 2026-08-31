# Sunday Brief Opener

A tiny companion Android app, not a Play Store thing — installed directly on
Justin's phone via `adb`. It exists because Gmail's Android app has no
reliable way to be deep-linked straight to a specific email: the obvious
`mail.google.com/#search/...` URL just opens Gmail's mobile *website* (which
ignores the `#search/` fragment), and Gmail's own internal deep-link
mechanisms (`gmail.app.goo.gl`, `ACTION_SEARCH`, etc.) turned out to be
undocumented and unreliable when triggered externally — see the git history
around `ledger.py`'s `source_link()` for how that was diagnosed.

This app owns the `sundaybrief://` URL scheme. `source_link()` in
`../src/sundaybrief/closures/ledger.py` builds links like:

```
sundaybrief://open?q=<url-encoded Gmail search query>
```

Tapping one: copies the search query to the clipboard, opens Gmail, and
closes itself. Paste into Gmail's search bar to land on the source email.

## One-time setup (per machine)

```
brew install --cask temurin            # JDK (needs your password interactively)
brew install --cask android-commandlinetools
sdkmanager --licenses
sdkmanager "build-tools;34.0.0" "platforms;android-34"
```

## Rebuild / install

Phone connected via USB with Developer Options → USB debugging on:

```
./build.sh
```

Installs straight to the connected device. Only needs re-running after
editing `OpenActivity.java`, or to set the app up on a new/reset phone.
