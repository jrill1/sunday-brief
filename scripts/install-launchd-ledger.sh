#!/usr/bin/env bash
# Install the daily ledger-extraction launchd job on THIS machine (run it on
# the Mini). Pulls new forwarded school emails from the drop-box and appends
# any closures/notes to data/closures.jsonl — keeps the ledger fresh so the
# weekly brief (see install-launchd.sh) always has the latest facts.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON="$REPO_ROOT/.venv/bin/python"
LOG="$REPO_ROOT/ledger.log"
TEMPLATE="$REPO_ROOT/scripts/com.sunday.brief.ledger.plist.template"
DEST="$HOME/Library/LaunchAgents/com.sunday.brief.ledger.plist"

if [[ ! -x "$PYTHON" ]]; then
    echo "error: no venv python at $PYTHON" >&2
    echo "  create it first:  python3 -m venv .venv && source .venv/bin/activate && pip install -e ." >&2
    exit 1
fi

mkdir -p "$HOME/Library/LaunchAgents"
sed -e "s|__PYTHON__|$PYTHON|g" \
    -e "s|__WORKDIR__|$REPO_ROOT|g" \
    -e "s|__LOG__|$LOG|g" \
    "$TEMPLATE" > "$DEST"

launchctl unload "$DEST" 2>/dev/null || true
launchctl load "$DEST"

echo "Installed $DEST"
echo "  python:  $PYTHON"
echo "  workdir: $REPO_ROOT"
echo
echo "Run it once now to test:   launchctl start com.sunday.brief.ledger"
echo "Watch the log:              tail -f $LOG"
echo "Uninstall:                  launchctl unload $DEST && rm $DEST"
