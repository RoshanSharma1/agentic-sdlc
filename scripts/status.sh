#!/usr/bin/env bash
# scripts/status.sh — Show current SDLC orchestrator status

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(dirname "$SCRIPT_DIR")"
STATE="$ROOT/workflow/state.json"

python3 - <<PYEOF
import json

with open('$STATE') as f:
    d = json.load(f)

phase       = d.get('phase', 'unknown')
phase_state = d.get('phase_state', 'unknown')
approval    = d.get('approval_needed', False)
branch      = d.get('current_branch', 'main')
updated     = d.get('last_updated', 'never')

PHASES = ['requirement', 'design', 'planning', 'implementation', 'testing', 'review', 'done']
idx = PHASES.index(phase) if phase in PHASES else -1

print("=" * 50)
print("  SDLC Orchestrator — Status")
print("=" * 50)

# Progress bar
bar = ""
for i, p in enumerate(PHASES[:-1]):
    if i < idx:
        bar += f"[✓ {p}] → "
    elif i == idx:
        bar += f"[► {p}] → "
    else:
        bar += f"[ {p}] → "
bar += "[done]"
# Print phase progress in chunks
for chunk in [PHASES[i:i+3] for i in range(0, len(PHASES)-1, 3)]:
    pass  # just print summary below

print(f"  Phase:    {phase}")
print(f"  State:    {phase_state}")
print(f"  Approval: {'REQUIRED — run ./scripts/approve.sh' if approval else 'not needed'}")
print(f"  Branch:   {branch}")
print(f"  Updated:  {updated}")
print()

history = d.get('history', [])
if history:
    print(f"  Recent history:")
    for h in history[-6:]:
        ts = h['timestamp'][:19].replace('T', ' ')
        print(f"    {ts}  {h['phase']:15}  {h['state']}")
print("=" * 50)
PYEOF
