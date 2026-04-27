#!/bin/bash
# Reinstall SDLC orchestrator in ContentAutomationPlatform nenv
# Run this after making changes to SDLC code

cd /Users/rsharma/projects/ContentAutomationPlatform
source nenv/bin/activate
pip uninstall -y sdlc-orchestrator
pip install -e /Users/rsharma/projects/agentic-sdlc

echo ""
echo "✓ SDLC reinstalled in ContentAutomationPlatform/nenv"
echo "Now restart the UI server:"
echo "  pkill -f 'sdlc ui' && cd /Users/rsharma/projects/ContentAutomationPlatform && nohup nenv/bin/sdlc ui --ngrok > /tmp/sdlc-ui.log 2>&1 &"
