#!/bin/bash
# Publishes <hostname> on the LAN via a Bonjour proxy record (no Mac rename).
# Usage: mdns.sh [hostname]   (default read from portal config, else family.local)
HOSTNAME_ARG="${1:-$(python3 -c "import json,os;print(json.load(open(os.path.expanduser('~/.config/azizfamily-portal.json'))).get('hostname','family.local'))" 2>/dev/null || echo family.local)}"
PORT=80
CHILD=""
current_ip() {
  for iface in en0 en1; do
    ip=$(ipconfig getifaddr "$iface" 2>/dev/null)
    [ -n "$ip" ] && { echo "$ip"; return; }
  done
  echo ""
}
cleanup() { [ -n "$CHILD" ] && kill "$CHILD" 2>/dev/null; exit 0; }
trap cleanup INT TERM
LAST_IP=""
while true; do
  IP=$(current_ip)
  if [ -n "$IP" ] && [ "$IP" != "$LAST_IP" ]; then
    [ -n "$CHILD" ] && kill "$CHILD" 2>/dev/null
    dns-sd -P "Family Portal" _http._tcp local "$PORT" "$HOSTNAME_ARG" "$IP" &
    CHILD=$!
    LAST_IP="$IP"
    echo "$(date '+%F %T') publishing $HOSTNAME_ARG -> $IP"
  elif [ -n "$CHILD" ] && ! kill -0 "$CHILD" 2>/dev/null; then
    LAST_IP=""
  fi
  sleep 30
done
