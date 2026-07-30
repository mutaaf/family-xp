#!/bin/bash
# CD for the family portal: pull green main from GitHub and restart the server.
# Run by launchd (com.familyxp.deploy) every 5 minutes. Fast-forward only, so
# local experiments are never clobbered — commit or stash them to resume CD.
set -euo pipefail
export PATH="/opt/homebrew/bin:/usr/local/bin:$PATH"   # launchd PATH lacks gh/git helpers
REPO="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO"
git fetch origin main --quiet
LOCAL=$(git rev-parse HEAD)
REMOTE=$(git rev-parse origin/main)
[ "$LOCAL" = "$REMOTE" ] && exit 0
echo "$(date '+%F %T') deploying $LOCAL -> $REMOTE"
git pull --ff-only --quiet origin main
launchctl kickstart -k "gui/$(id -u)/com.familyxp.portal"
echo "$(date '+%F %T') deployed + portal restarted"
