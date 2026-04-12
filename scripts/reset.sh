#!/usr/bin/env bash
# scripts/reset.sh — Reset orchestrator back to the start (or a specific phase)
#
# Usage:
#   ./scripts/reset.sh                    # reset to requirement phase
#   ./scripts/reset.sh <phase>            # reset to specific phase

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(dirname "$SCRIPT_DIR")"
STATE="$ROOT/workflow/state.json"

TARGET="${1:-requirement}"
VALID=(requirement design planning implementation testing review)

valid=false
for p in "${VALID[@]}"; do [[ "$p" == "$TARGET" ]] && valid=true; done
$valid || { echo "Invalid phase: $TARGET. Valid: ${VALID[*]}"; exit 1; }

python3 - <<PYEOF
import json, datetime
with open('$STATE') as f:
    d = json.load(f)
d['phase']          = '$TARGET'
d['phase_state']    = 'draft'
d['approval_needed'] = False
d['current_branch'] = 'main'
d['last_updated']   = datetime.datetime.utcnow().isoformat() + 'Z'
with open('$STATE', 'w') as f:
    json.dump(d, f, indent=2)
PYEOF

echo "State reset to phase: $TARGET"
