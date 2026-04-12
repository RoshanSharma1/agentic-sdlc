#!/usr/bin/env bash
# scripts/run.sh — Autonomous SDLC Orchestrator
#
# Usage:
#   ./scripts/run.sh           # run one phase then stop
#   ./scripts/run.sh --loop    # run all phases continuously (stops at approval gates)

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(dirname "$SCRIPT_DIR")"
STATE="$ROOT/workflow/state.json"
TASKS="$ROOT/workflow/tasks.json"
SPEC="$ROOT/spec.yaml"
FEEDBACK_DIR="$ROOT/feedback"
LOGS_DIR="$ROOT/workflow/logs"

MODE="${1:---once}"

PHASES=(requirement design planning implementation testing review)

# ── logging ──────────────────────────────────────────────────────────────────
log()  { echo "[$(date '+%H:%M:%S')] $*"; }
err()  { echo "[$(date '+%H:%M:%S')] ERROR: $*" >&2; }
die()  { err "$*"; exit 1; }

# ── JSON helpers (python3) ────────────────────────────────────────────────────
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
  local key="$1" val="$2"  # val: true | false
  python3 - <<PYEOF
import json, datetime
with open('$STATE') as f:
    d = json.load(f)
d['$key'] = $val
d['last_updated'] = datetime.datetime.utcnow().isoformat() + 'Z'
with open('$STATE', 'w') as f:
    json.dump(d, f, indent=2)
PYEOF
}

jpush_history() {
  local phase="$1" state="$2"
  python3 - <<PYEOF
import json, datetime
with open('$STATE') as f:
    d = json.load(f)
d.setdefault('history', []).append({
    'phase': '$phase',
    'state': '$state',
    'timestamp': datetime.datetime.utcnow().isoformat() + 'Z'
})
with open('$STATE', 'w') as f:
    json.dump(d, f, indent=2)
PYEOF
}

# ── spec / tasks helpers ──────────────────────────────────────────────────────
spec_get() {
  python3 -c "
import sys
for line in open('$SPEC'):
    if line.startswith('$1:'):
        print(line.split(':',1)[1].strip())
        break
"
}

task_prompt() {
  python3 -c "import json; print(json.load(open('$TASKS'))['phases']['$1']['prompt'])"
}

task_approval_required() {
  python3 -c "import json; print(json.load(open('$TASKS'))['phases']['$1'].get('approval_required', False))"
}

# ── git helpers ───────────────────────────────────────────────────────────────
git_ensure_branch() {
  local branch="$1"
  cd "$ROOT"
  if git rev-parse --verify "$branch" &>/dev/null; then
    git checkout "$branch"
  else
    git checkout -b "$branch"
  fi
}

git_commit_phase() {
  local phase="$1"
  cd "$ROOT"
  git add -A
  if git diff --cached --quiet; then
    log "Nothing to commit for phase: $phase"
    return 0
  fi
  git commit -m "sdlc($phase): complete $phase phase output"
}

git_open_pr() {
  local phase="$1" branch="$2"
  command -v gh &>/dev/null || { log "gh not installed — open PR manually for branch: $branch"; return 0; }
  # Don't fail if PR already exists
  gh pr create \
    --title "sdlc($phase): complete $phase phase" \
    --base main \
    --head "$branch" \
    --body "$(printf "## SDLC Phase: \`%s\`\n\nAutomated output from the SDLC orchestrator.\n\n### Review checklist\n- [ ] Output meets requirements\n- [ ] Ready to advance to next phase\n\n> To approve: \`./scripts/approve.sh %s\`" "$phase" "$phase")" \
    2>/dev/null && log "PR opened for $phase" \
    || log "PR already exists or remote not configured — skipping"
}

# ── feedback loader ───────────────────────────────────────────────────────────
load_feedback() {
  local fb="$FEEDBACK_DIR/$1.md"
  [[ -f "$fb" ]] || return 0
  echo ""
  echo "## Feedback from previous review"
  cat "$fb"
  echo ""
}

# ── build Claude prompt ───────────────────────────────────────────────────────
build_prompt() {
  local phase="$1"
  cat <<PROMPT
You are an autonomous SDLC agent. You are executing the **$phase** phase.

## Project Specification (spec.yaml)
$(cat "$SPEC")

## Project Rules (CLAUDE.md)
$(cat "$ROOT/CLAUDE.md")

## Your Task
$(task_prompt "$phase")

$(load_feedback "$phase")
PROMPT
}

# ── run one SDLC phase ────────────────────────────────────────────────────────
run_phase() {
  local phase="$1"
  log "═══════════════════════════════"
  log "Phase: $phase"
  log "═══════════════════════════════"

  mkdir -p "$LOGS_DIR"

  # Switch to phase branch
  local branch="sdlc/$phase"
  git_ensure_branch "$branch"
  jset "current_branch" "$branch"
  jset "phase_state" "in_progress"

  # Build and run Claude
  log "Calling claude -p for phase: $phase ..."
  local prompt output
  prompt="$(build_prompt "$phase")"

  output=$(echo "$prompt" | claude -p --dangerously-skip-permissions 2>&1) || {
    err "Claude CLI exited non-zero for phase: $phase"
    jset "phase_state" "error"
    echo "$output" > "$LOGS_DIR/${phase}_error.log"
    return 1
  }

  # Save full output log
  echo "$output" > "$LOGS_DIR/${phase}.log"
  log "Claude completed (${#output} bytes) — log: workflow/logs/${phase}.log"

  # Commit everything Claude produced
  git_commit_phase "$phase" || true

  # Decide whether approval is needed
  local needs_approval
  needs_approval="$(task_approval_required "$phase")"

  if [[ "$needs_approval" == "True" ]]; then
    jset      "phase_state"    "awaiting_approval"
    jset_bool "approval_needed" "true"
    jpush_history "$phase" "awaiting_approval"

    # Push branch + open PR
    git push -u origin "$branch" 2>/dev/null \
      || log "Push skipped (no remote configured)"
    git_open_pr "$phase" "$branch"

    # Notify
    bash "$SCRIPT_DIR/notify.sh" "$phase" "awaiting_approval" 2>/dev/null || true

    log ""
    log "⏸  Phase '$phase' complete — waiting for human approval."
    log "   Review the output, then run:  ./scripts/approve.sh $phase"
    return 2
  else
    jset      "phase_state"    "done"
    jset_bool "approval_needed" "false"
    jpush_history "$phase" "done"
    log "Phase '$phase' done — auto-advancing."
    return 0
  fi
}

# ── phase sequencing ──────────────────────────────────────────────────────────
next_phase() {
  local current="$1" found=false
  for p in "${PHASES[@]}"; do
    $found && { echo "$p"; return; }
    [[ "$p" == "$current" ]] && found=true
  done
  echo "done"
}

advance() {
  local current="$1"
  local next
  next="$(next_phase "$current")"
  if [[ "$next" == "done" ]]; then
    jset "phase"       "done"
    jset "phase_state" "done"
    log "All phases complete! SDLC finished."
  else
    jset "phase"       "$next"
    jset "phase_state" "draft"
    log "Advanced to phase: $next"
  fi
}

# ── main ──────────────────────────────────────────────────────────────────────
main() {
  log "SDLC Orchestrator starting (mode: $MODE)"
  log "Project: $(spec_get project_name)"

  while true; do
    local phase phase_state
    phase="$(jget phase)"
    phase_state="$(jget phase_state)"

    log "State: phase=$phase  state=$phase_state"

    # All done
    [[ "$phase" == "done" ]] && { log "SDLC is complete. Nothing to do."; exit 0; }

    # Blocked on human approval
    if [[ "$phase_state" == "awaiting_approval" ]]; then
      log "Blocked: phase '$phase' is awaiting human approval."
      log "Run:  ./scripts/approve.sh $phase"
      exit 0
    fi

    # Execute the phase
    run_phase "$phase"
    local rc=$?

    if [[ $rc -eq 2 ]]; then
      # Approval gate hit — stop
      exit 0
    elif [[ $rc -ne 0 ]]; then
      die "Phase '$phase' failed. Check workflow/logs/${phase}_error.log"
    fi

    # Advance to next phase
    advance "$phase"

    # In --once mode, stop after advancing
    [[ "$MODE" == "--once" ]] && break

    sleep 1
  done
}

main
