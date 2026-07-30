#!/bin/bash
# Family XP one-command installer (macOS).
# Sets up the portal, Bonjour name, self-healing watchdog and nightly backups.
set -euo pipefail
REPO="$(cd "$(dirname "$0")" && pwd)"
command -v /usr/bin/python3 >/dev/null || { echo "needs macOS python3 (xcode-select --install)"; exit 1; }

# non-interactive: FAMILY_NAME env or first argument (for AI agents / scripts)
FAM="${FAMILY_NAME:-${1:-}}"
if [ -z "$FAM" ]; then
  read -rp "Your family name (e.g. Rivera): " FAM
fi
FAM=${FAM:-Our}
SLUG=$(echo "$FAM" | tr '[:upper:]' '[:lower:]' | tr -cd 'a-z0-9')
HOST="${SLUG:-our}family.local"
PASS=$(/usr/bin/python3 -c "import secrets;print(secrets.token_hex(3))")

CFG="$HOME/.config/azizfamily-portal.json"
if [ ! -f "$CFG" ]; then
  mkdir -p "$HOME/.config"
  /usr/bin/python3 - "$FAM" "$HOST" "$PASS" <<'PYEOF'
import json, os, secrets, sys
fam, host, pw = sys.argv[1:4]
cfg = {"secret": secrets.token_hex(32), "family_name": fam, "hostname": host,
       "users": [
         {"id": "family", "name": fam + " Family", "passcode": pw,
          "role": "parent", "avatar": "avatar", "theme": "day", "onboarded": True},
         {"id": "kid1", "name": "Kid One", "passcode": "", "role": "kid",
          "avatar": "astro", "theme": "sunset", "onboarded": False},
         {"id": "kid2", "name": "Kid Two", "passcode": "", "role": "kid",
          "avatar": "robo", "theme": "spring", "onboarded": False}]}
json.dump(cfg, open(os.path.expanduser("~/.config/azizfamily-portal.json"), "w"), indent=2)
PYEOF
  echo "config created."
else
  echo "existing config found — keeping it."
  PASS="(your existing parent passcode)"
fi

LOGD="$HOME/Library/Logs/azizfamily"; mkdir -p "$LOGD"
if [ "${SKIP_AGENTS:-}" = "1" ]; then   # CI / dry-run: config only
  echo "SETUP_COMPLETE url=http://$HOST/ passcode=$PASS (agents skipped)"
  exit 0
fi
agent() {  # name, program-args-xml
  local name=$1 args=$2
  cat > "$HOME/Library/LaunchAgents/com.familyxp.$name.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>com.familyxp.$name</string>
  <key>ProgramArguments</key><array>$args</array>
  <key>RunAtLoad</key><true/>$3
  <key>StandardOutPath</key><string>$LOGD/$name.log</string>
  <key>StandardErrorPath</key><string>$LOGD/$name.log</string>
</dict></plist>
PLIST
  launchctl bootout "gui/$(id -u)/com.familyxp.$name" 2>/dev/null || true
  launchctl bootstrap "gui/$(id -u)" "$HOME/Library/LaunchAgents/com.familyxp.$name.plist"
}
agent portal "<string>/usr/bin/python3</string><string>$REPO/server/family_portal.py</string>" "<key>KeepAlive</key><true/>"
agent mdns "<string>/bin/bash</string><string>$REPO/bin/mdns.sh</string><string>$HOST</string>" "<key>KeepAlive</key><true/>"
agent backup "<string>/bin/bash</string><string>$REPO/bin/backup.sh</string>" "<key>StartCalendarInterval</key><dict><key>Hour</key><integer>3</integer><key>Minute</key><integer>30</integer></dict>"
sleep 2
echo
echo "🎉 Family XP is up!"
echo "   Open:            http://$HOST/"
echo "   Parent passcode: $PASS"
echo "   Kids tap their card and run the setup wizard themselves."
echo "   Rename/add kids anytime in Control Panel (parent desktop)."
echo "SETUP_COMPLETE url=http://$HOST/ passcode=$PASS"
