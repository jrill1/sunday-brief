#!/usr/bin/env bash
# Push this repo from your MacBook to the headless Mini over SSH.
# Deliberately does NOT copy .env, .venv, .git, or generated data — the Mini
# keeps its own secrets and builds its own venv.
#
# Usage:
#   scripts/deploy.sh you@mini.local:~/sunday-brief
#   SUNDAYBRIEF_MINI=you@mini.local:~/sunday-brief scripts/deploy.sh
#
# Git is the tidier alternative if you have a remote: commit on the laptop,
# `git pull` on the Mini. This script is here for the no-remote case.
set -euo pipefail

TARGET="${1:-${SUNDAYBRIEF_MINI:-}}"
if [[ -z "$TARGET" ]]; then
    echo "usage: $0 user@host:/path/to/sunday-brief" >&2
    exit 1
fi

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

rsync -av --delete \
    --exclude '.env' \
    --exclude '.venv/' \
    --exclude '.git/' \
    --exclude 'data/' \
    --exclude '__pycache__/' \
    --exclude '*.egg-info/' \
    --exclude '*.log' \
    "$REPO_ROOT/" "$TARGET/"

echo
echo "Deployed to $TARGET"
echo "On the Mini (first time only):"
echo "  cd <path> && python3 -m venv .venv && source .venv/bin/activate && pip install -e ."
echo "  cp .env.example .env && chmod 600 .env   # add secrets"
echo "  ./scripts/install-launchd.sh"
