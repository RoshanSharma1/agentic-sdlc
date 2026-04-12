#!/usr/bin/env bash
# scripts/notify.sh — Send Slack (or log) notification
#
# Usage: ./scripts/notify.sh <phase> <event>
# Events: awaiting_approval | phase_started | phase_done | error

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(dirname "$SCRIPT_DIR")"
SPEC="$ROOT/spec.yaml"

PHASE="${1:-unknown}"
EVENT="${2:-update}"

spec_get() {
  python3 -c "
for line in open('$SPEC'):
    if line.startswith('$1:'):
        print(line.split(':',1)[1].strip())
        break
" 2>/dev/null || echo ""
}

PROJECT_NAME="$(spec_get project_name)"
SLACK_WEBHOOK="$(spec_get slack_webhook)"

case "$EVENT" in
  awaiting_approval)
    ICON=":warning:"
    TEXT="*Human approval required* for phase \`$PHASE\`.\nRun \`./scripts/approve.sh $PHASE\` to advance."
    ;;
  phase_started)
    ICON=":rocket:"
    TEXT="Phase \`$PHASE\` started."
    ;;
  phase_done)
    ICON=":white_check_mark:"
    TEXT="Phase \`$PHASE\` complete — auto-advancing."
    ;;
  error)
    ICON=":x:"
    TEXT="*Error* in phase \`$PHASE\`. Check \`workflow/logs/${PHASE}_error.log\`."
    ;;
  *)
    ICON=":robot_face:"
    TEXT="Event: \`$EVENT\` on phase \`$PHASE\`."
    ;;
esac

TIMESTAMP="$(date '+%Y-%m-%d %H:%M:%S')"
FULL_MSG="$ICON *SDLC Orchestrator* | *$PROJECT_NAME*\n$TEXT\n_$TIMESTAMP_"

if [[ -n "$SLACK_WEBHOOK" && "$SLACK_WEBHOOK" != '""' ]]; then
  PAYLOAD=$(python3 -c "import json; print(json.dumps({'text': '$FULL_MSG'}))")
  if curl -sf -X POST "$SLACK_WEBHOOK" \
       -H 'Content-type: application/json' \
       -d "$PAYLOAD" > /dev/null; then
    echo "Slack notification sent: [$PHASE/$EVENT]"
  else
    echo "Slack notification failed — check slack_webhook in spec.yaml"
  fi
else
  echo "NOTIFY [$PHASE/$EVENT]: $TEXT"
  echo "(Set slack_webhook in spec.yaml to enable Slack notifications)"
fi
