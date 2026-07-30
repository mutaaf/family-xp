#!/bin/bash
# Self-healing watchdog with circuit breaker. Runs via launchd every 2 min.
# Heals: portal (:80), mDNS ($(python3 -c "import json,os;print(json.load(open(os.path.expanduser('~/.config/azizfamily-portal.json'))).get('hostname','family.local'))")), stale app pidfiles.
# Circuit: 3 consecutive failed heals -> stop retrying for 1h (avoid flapping),
# log CIRCUIT OPEN so it's visible in azizfamily_watchdog.log.
export PATH="/opt/homebrew/bin:/usr/local/bin:$PATH"
STATE_DIR="$HOME/Library/Logs/azizfamily/health"; mkdir -p "$STATE_DIR"
now=$(date +%s)

breaker() {  # $1 component; returns 1 if circuit open
  local f="$STATE_DIR/$1"; local fails=0 until=0
  [ -f "$f" ] && read -r fails until < "$f"
  [ "$now" -lt "${until:-0}" ] && return 1
  return 0
}
record() {  # $1 component $2 ok|fail
  local f="$STATE_DIR/$1"; local fails=0 until=0
  [ -f "$f" ] && read -r fails until < "$f"
  if [ "$2" = ok ]; then rm -f "$f"; return; fi
  fails=$((fails + 1))
  if [ "$fails" -ge 3 ]; then
    until=$((now + 3600)); fails=0
    echo "$(date '+%F %T') CIRCUIT OPEN: $1 — 3 heals failed, backing off 1h"
  fi
  echo "$fails $until" > "$f"
}

# portal
if breaker portal; then
  if ! curl -sf -m 5 -o /dev/null http://localhost/api/me; then
    echo "$(date '+%F %T') portal down — restarting"
    launchctl kickstart -k "gui/$(id -u)/com.familyxp.portal"; sleep 5
    curl -sf -m 5 -o /dev/null http://localhost/api/me \
      && { record portal ok; echo "$(date '+%F %T') portal healed"; } \
      || record portal fail
  else record portal ok; fi
fi

# mDNS name
if breaker mdns; then
  if ! dscacheutil -q host -a name $(python3 -c "import json,os;print(json.load(open(os.path.expanduser('~/.config/azizfamily-portal.json'))).get('hostname','family.local'))") | grep -q ip_address; then
    echo "$(date '+%F %T') $(python3 -c "import json,os;print(json.load(open(os.path.expanduser('~/.config/azizfamily-portal.json'))).get('hostname','family.local'))") not resolving — restarting mdns"
    launchctl kickstart -k "gui/$(id -u)/com.familyxp.mdns"; sleep 8
    dscacheutil -q host -a name $(python3 -c "import json,os;print(json.load(open(os.path.expanduser('~/.config/azizfamily-portal.json'))).get('hostname','family.local'))") | grep -q ip_address \
      && { record mdns ok; echo "$(date '+%F %T') mdns healed"; } \
      || record mdns fail
  else record mdns ok; fi
fi

# stale app pidfiles -> truthful status LEDs in My Projects
for pf in "$HOME/Library/Logs/azizfamily/"*.pid; do
  [ -f "$pf" ] || continue
  kill -0 "$(cat "$pf")" 2>/dev/null || {
    echo "$(date '+%F %T') $(basename "$pf" .pid) died — clearing pidfile"
    rm -f "$pf"; }
done
