#!/usr/bin/env bash
# scripts/approve.sh — Approve current phase and advance to the next one
#
# Usage:
#   ./scripts/approve.sh               # approve current phase
#   ./scripts/approve.sh <phase>       # approve specific phase (must match current)
#   ./scripts/approve.sh <phase> "feedback text"  # approve with feedback for next iteration

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(dirname "$SCRIPT_DIR")"
STATE="$ROOT/workflow/state.json"
FEEDBACK_DIR="$ROOT/feedback"

PHASE="${1:-}"
FEEDBACK="${2:-}"

PHASES=(requirement design planning implementation testing review)

jget() { python3 -c "import json; d=json.load(open('$STATE')); print(d.get('$1',''))"; }

jset() {
  local key="$1" val="$2"
  python3 - <<PYEOF
import json, datetime
with open('$STATE') as f:
    d = json.load(f)
d['$key'] = '$val'
d['last_updated'] = datetime.datetime.utcnow().isoformat() + 'Z'
with open('$STATE', 'w') as f:
    json.dump(d, f, indent=2)
PYEOF
}

jset_bool() {
  python3 - <<PYEOF
import json, datetime
with open('$STATE') as f:
    d = json.load(f)
d['$1'] = $2
d['last_updated'] = datetime.datetime.utcnow().isoformat() + 'Z'
with open('$STATE', 'w') as f:
    json.dump(d, f, indent=2)
PYEOF
}

jpush_history() {
  python3 - <<PYEOF
import json, datetime
with open('$STATE') as f:
    d = json.load(f)
d.setdefault('history', []).append({
    'phase': '$1',
    'state': '$2',
    'timestamp': datetime.datetime.utcnow().isoformat() + 'Z'
})
with open('$STATE', 'w') as f:
    json.dump(d, f, indent=2)
PYEOF
}

next_phase() {
  local current="$1" found=false
  for p in "${PHASES[@]}"; do
    $found && { echo "$p"; return; }
    [[ "$p" == "$current" ]] && found=true
  done
  echo "done"
}

current_phase="$(jget phase)"
current_state="$(jget phase_state)"

# Validate phase argument
if [[ -n "$PHASE" && "$PHASE" != "$current_phase" ]]; then
  echo "Error: current phase is '$current_phase', not '$PHASE'"
  exit 1
fi

if [[ "$current_state" != "awaiting_approval" ]]; then
  echo "Phase '$current_phase' is in state '$current_state' — not awaiting approval."
  echo "Nothing to approve."
  exit 0
fi

# Save feedback if provided
if [[ -n "$FEEDBACK" ]]; then
  mkdir -p "$FEEDBACK_DIR"
  {
    echo "### Feedback on $current_phase ($(date '+%Y-%m-%d %H:%M:%S'))"
    echo "$FEEDBACK"
    echo ""
  } >> "$FEEDBACK_DIR/${current_phase}.md"
  echo "Feedback saved to feedback/${current_phase}.md"
fi

# Merge PR if gh available and PR exists
if command -v gh &>/dev/null; then
  gh pr merge "sdlc/$current_phase" --squash --delete-branch 2>/dev/null \
    && echo "PR merged for phase: $current_phase" \
    || true
  # Return to main
  git -C "$ROOT" checkout main 2>/dev/null || true
fi

# Advance state
next="$(next_phase "$current_phase")"
jpush_history "$current_phase" "approved"
jset_bool "approval_needed" "false"

if [[ "$next" == "done" ]]; then
  jset "phase"       "done"
  jset "phase_state" "done"
  echo "✓ Phase '$current_phase' approved."
  echo "  All phases complete — SDLC finished!"
else
  jset "phase"       "$next"
  jset "phase_state" "draft"
  echo "✓ Phase '$current_phase' approved."
  echo "  Next phase: $next"
  echo "  Run:  ./scripts/run.sh"
fi
