#!/usr/bin/env bash
# Reset demo state and seed fictional data so every run starts identical.
set -euo pipefail
cd "$(dirname "$0")/.."
BIN=.venv/bin

rm -rf data/audit data/memory.sqlite data/internal_dncl.txt
mkdir -p data

echo "== importing fictional contacts and suppression list =="
$BIN/callpilot import contacts examples/contacts.csv || true   # exit 1 is expected: 2 rows rejected on purpose
$BIN/callpilot import suppression examples/suppression.txt

echo "== seeding a returning prospect with history =="
$BIN/python - <<'EOF'
from callpilot.call.memory import ProspectMemory
from callpilot.config import settings
m = ProspectMemory(settings.data_dir)
m.add_interaction("+14165550123", "seed-0001",
    "- Outcome: interested, asked for info\n- Needs: reduce no-shows at dental clinic\n"
    "- Objections: already uses Outlook calendar, worried about double entry\n- Next step: rep to call back in Q4\n"
    "- Consent/opt-out: none", "follow_up", 1095)
print("seeded history for +14165550123 (Priya Shah, Lakeshore Dental)")
EOF

echo "== sanity =="
$BIN/callpilot knowledge | head -3
echo "ready."
