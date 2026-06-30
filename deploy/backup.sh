#!/usr/bin/env bash
# Dagelijkse SQLite-backup van de PrintGuard-database.
# Houdt 14 dagen aan backups. Draai via cron (zie setup.sh).
set -euo pipefail

DB="/var/lib/printguard/printguard.db"
DEST="/var/backups/printguard"
mkdir -p "$DEST"

STAMP="$(date +%Y%m%d-%H%M%S)"
if [ -f "$DB" ]; then
  sqlite3 "$DB" ".backup '$DEST/printguard-$STAMP.db'"
  gzip -f "$DEST/printguard-$STAMP.db"
fi

# Ouder dan 14 dagen opruimen
find "$DEST" -name 'printguard-*.db.gz' -mtime +14 -delete

# OPTIONEEL: offsite kopie naar een SiteGround-account (vul host/pad in en zet een SSH-key klaar):
# scp -P 18765 -i ~/.ssh/EEN_SITE "$DEST/printguard-$STAMP.db.gz" gebruiker@host:~/backups/printguard/
