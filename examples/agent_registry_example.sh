#!/bin/bash
# Example: Setting up and using the Agent Registry

# This example shows how to set up multiple AI agents with automatic fallback
# so you never have to manually switch when one runs out of credits.

echo "=== Agent Registry Setup Example ==="
echo ""

# Step 1: Initialize your project (if not already done)
echo "1. Initialize project with agent fallback enabled"
# sdlc init --executor claude-code

# Step 2: View current agent status
echo ""
echo "2. View registered agents:"
sdlc agent list

# Step 3: Check statistics
echo ""
echo "3. Check registry statistics:"
sdlc agent status

# Step 4: (Optional) Add a custom agent
echo ""
echo "4. Add a custom agent (e.g., cursor):"
# sdlc agent add cursor --priority 4
echo "   (You'll need to configure the CLI command in memory.py first)"

# Step 5: Configure your project spec
echo ""
echo "5. Edit .sdlc/spec.yaml to enable fallback:"
echo "   ---"
echo "   project_name: my-project"
echo "   executor: claude-code        # Preferred agent (tries first)"
echo "   agent_fallback: true          # Enable automatic fallback"
echo "   tech_stack: Python"
echo ""

# Step 6: Run your workflow - fallback happens automatically
echo "6. Run workflow (fallback is automatic):"
echo "   sdlc state transition --auto"
echo ""
echo "   If claude-code fails with credit error:"
echo "   ✗ Credit limit exceeded"
echo "   Falling back to: kiro"
echo "   ✓ Executed with kiro"
echo ""

# Step 7: Monitor agent status over time
echo "7. Monitor agents periodically:"
echo "   sdlc agent list"
echo ""

# Step 8: Reset agents when credits refill
echo "8. When credits refill, reset the agent:"
echo "   sdlc agent reset claude-code"
echo "   # Or reset all:"
echo "   sdlc agent reset --all"
echo ""

# Step 9: Disable an agent temporarily
echo "9. Disable an agent temporarily:"
echo "   sdlc agent disable codex"
echo "   sdlc agent enable codex    # Re-enable later"
echo ""

# Step 10: Check what happened
echo "10. Review execution history:"
echo "    sdlc agent list    # Shows which agent was last used"
echo ""

echo "=== Advanced Usage ==="
echo ""

# Scenario 1: All agents out of credits
echo "Scenario 1: All agents out of credits"
echo "  $ sdlc agent list"
echo "  ... all show 'no_credits' ..."
echo ""
echo "  Wait for quotas to reset (usually daily), then:"
echo "  $ sdlc agent reset --all"
echo "  ✓ All agents reset to available"
echo ""

# Scenario 2: One agent keeps failing
echo "Scenario 2: One agent keeps failing"
echo "  The registry automatically marks it as 'error' after 3 failures"
echo "  To manually disable:"
echo "  $ sdlc agent disable problematic-agent"
echo ""

# Scenario 3: Priority optimization
echo "Scenario 3: Optimize agent priority"
echo "  Put your cheapest/most reliable agent as priority 1:"
echo ""
echo "  1. View current priorities:"
echo "     $ sdlc agent list"
echo ""
echo "  2. Remove and re-add with new priority:"
echo "     $ sdlc agent remove kiro"
echo "     $ sdlc agent add kiro --priority 1"
echo ""

# Scenario 4: Custom agent integration
echo "Scenario 4: Add a custom agent (e.g., Windsurf)"
echo ""
echo "  1. Edit sdlc_orchestrator/memory.py:"
echo ""
cat << 'EOF'
     EXECUTOR_CONFIG = {
         "claude-code": ("CLAUDE.md", ...),
         "windsurf": ("WINDSURF.md", Path.home() / ".windsurf" / "commands", Path(".windsurf")),
     }

     EXECUTOR_CLI = {
         "claude-code": ["claude", "-p", "/{skill}"],
         "windsurf": ["windsurf-cli", "run", "{skill}"],
     }
EOF
echo ""
echo "  2. Register the agent:"
echo "     $ sdlc agent add windsurf --priority 5"
echo ""
echo "  3. Now it's in the fallback chain!"
echo ""

echo "=== Tips ==="
echo ""
echo "✓ Keep at least 2-3 agents configured for reliability"
echo "✓ Reset agents at the start of each day if you have daily quotas"
echo "✓ Monitor 'sdlc agent list' to see which agent is being used"
echo "✓ Lower priority number = tried first"
echo "✓ The registry is stored at .sdlc/agent_registry.json"
echo ""

echo "=== Testing the Setup ==="
echo ""
echo "To test without running actual tasks:"
echo ""
echo "1. Check agent list:"
echo "   sdlc agent list"
echo ""
echo "2. Manually mark an agent as out of credits:"
echo "   # Edit .sdlc/agent_registry.json and change status to 'no_credits'"
echo ""
echo "3. Run a command - it should fallback to next agent"
echo ""
echo "4. Reset when done testing:"
echo "   sdlc agent reset --all"
echo ""
