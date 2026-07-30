#!/bin/bash
# Nightly data protection: ZIP kids/ + all portal configs, keep 30 days.
set -euo pipefail
DEST="$HOME/Backups/azizfamily"
mkdir -p "$DEST"
STAMP=$(date +%Y%m%d-%H%M)
cd "$(cd "$(dirname "$0")/.." && pwd)"
zip -qr "$DEST/azizfamily-$STAMP.zip" kids \
  -x "kids/*/.tmp" 2>/dev/null || true
for f in "$HOME/.config/azizfamily-"*.json; do
  zip -qj "$DEST/azizfamily-$STAMP.zip" "$f"
done
ls -t "$DEST"/azizfamily-*.zip 2>/dev/null | tail -n +31 | xargs rm -f 2>/dev/null || true
echo "$(date '+%F %T') backup done: azizfamily-$STAMP.zip ($(ls "$DEST" | wc -l | tr -d ' ') kept)"
