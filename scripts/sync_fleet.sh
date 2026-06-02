#!/usr/bin/env bash
# Reconcile fleet.json (SSOT) against live VM ttyd systemd units.
# Exits non-zero if drift detected. See docs/SSOT.md §5.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
FLEET="$ROOT/fleet.json"
HOST=$(python3 -c "import json;d=json.load(open('$FLEET'));print(d['vm']['host'])")
USER=$(python3 -c "import json;d=json.load(open('$FLEET'));print(d['vm'].get('ssh_user','root'))")
declared=$(python3 -c "
import json
d=json.load(open('$FLEET'))
for s in d['sessions']:
    print(s['name'], s.get('ttyd_port'))
" | sort)
live=$(ssh -o BatchMode=yes -o ConnectTimeout=8 "$USER@$HOST" '
for u in $(systemctl list-units "ttyd-*" --no-legend --plain 2>/dev/null | awk "{print \$1}"); do
  s=${u#ttyd-}; s=${s%.service}
  p=$(systemctl cat "$u" 2>/dev/null | grep -oE "ttyd-session.sh [0-9]+" | grep -oE "[0-9]+")
  echo "$s $p"
done' | sort)
echo "── Declared (fleet.json) ──"; echo "$declared"
echo "── Live (VM) ──"; echo "$live"
drift=$(diff <(echo "$declared") <(echo "$live") || true)
if [ -z "$drift" ]; then echo "✓ fleet.json matches live VM."; exit 0
else echo "✗ DRIFT:"; echo "$drift"; exit 1; fi
