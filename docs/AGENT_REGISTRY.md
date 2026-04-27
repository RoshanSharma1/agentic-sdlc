# Agent Registry - Multi-Agent Automatic Fallback

The Agent Registry manages multiple AI coding agents (Claude, Kiro, Codex, etc.) with automatic fallback when one runs out of credits or encounters errors.

## Features

- 🔄 **Automatic Fallback** - Switches to the next available agent when one fails
- 💳 **Credit Detection** - Detects credit/quota exhaustion errors automatically
- 📊 **Usage Tracking** - Monitors success/failure rates for each agent
- ⚙️ **Priority System** - Configure which agent to try first
- 🔧 **Easy Management** - CLI commands to view and manage agents

## Quick Start

### 1. Enable Automatic Fallback

Add to your `.sdlc/spec.yaml`:

```yaml
agent_fallback: true  # Enable automatic fallback (default: true)
executor: claude-code  # Preferred agent (will try first)
```

### 2. View Agent Status

```bash
sdlc agent list
```

Output:
```
┏━━━━━━━━━━┳━━━━━━━━━━━━━┳━━━━━━━━━━━━┳━━━━━━━━┳━━━━━━━━━━┳━━━━━━━━━━━━━━━━━━━━┓
┃ Priority ┃ Agent       ┃ Status     ┃ Success┃ Failures ┃ Last Used          ┃
┡━━━━━━━━━━╇━━━━━━━━━━━━━╇━━━━━━━━━━━━╇━━━━━━━━╇━━━━━━━━━━╇━━━━━━━━━━━━━━━━━━━━┩
│ 1        │ claude-code │ available  │ 42     │ 0        │ 2026-04-22 10:30   │
│ 2        │ kiro        │ no_credits │ 15     │ 1        │ 2026-04-22 09:15   │
│ 3        │ codex       │ available  │ 8      │ 0        │ 2026-04-21 14:20   │
└──────────┴─────────────┴────────────┴────────┴──────────┴────────────────────┘
```

### 3. Reset Agent When Credits Refill

```bash
# Reset a specific agent
sdlc agent reset kiro

# Reset all agents
sdlc agent reset --all
```

## How It Works

1. **Execution**: When you run a task, the registry tries your preferred agent first
2. **Error Detection**: If the agent fails with a credit/quota error, it's marked as unavailable
3. **Automatic Fallback**: The next available agent is tried automatically
4. **Success Tracking**: Successful executions reset the failure counter

## Agent Statuses

- `available` ✅ - Agent is ready to use
- `no_credits` 💳 - Out of credits/quota
- `error` ⚠️ - Multiple failures (3+ consecutive)
- `disabled` 🚫 - Manually disabled

## CLI Commands

### List Agents
```bash
sdlc agent list              # Show all agents
sdlc agent status            # Show summary statistics
```

### Manage Agents
```bash
sdlc agent add cursor --priority 4     # Add a new agent
sdlc agent remove cursor               # Remove an agent
sdlc agent disable kiro                # Temporarily disable
sdlc agent enable kiro                 # Re-enable
```

### Reset Status
```bash
sdlc agent reset claude-code   # Reset one agent
sdlc agent reset --all         # Reset all agents
```

## Configuration

### spec.yaml Options

```yaml
# Enable/disable automatic fallback
agent_fallback: true

# Set preferred agent (tries first)
executor: claude-code

# Available agents and their CLI commands are configured in memory.py
```

### Add Custom Agents

Edit `sdlc_orchestrator/memory.py`:

```python
EXECUTOR_CONFIG = {
    "claude-code": ("CLAUDE.md", Path.home() / ".claude" / "commands", Path(".claude")),
    "kiro": ("AGENT.md", Path.home() / ".kiro" / "skills", Path(".kiro")),
    "codex": ("AGENTS.md", Path.home() / ".codex" / "commands", Path(".codex")),
    "cursor": ("CURSOR.md", Path.home() / ".cursor" / "commands", Path(".cursor")),  # Add here
}

EXECUTOR_CLI = {
    "claude-code": ["claude", "-p", "--dangerously-skip-permissions", "/{skill}"],
    "kiro": ["kiro-cli", "chat", "--agent", "{skill}", "--no-interactive", "start"],
    "codex": ["codex", "exec", "--full-auto", "{skill}"],
    "cursor": ["cursor-cli", "run", "{skill}"],  # Add here
}
```

Then register it:
```bash
sdlc agent add cursor --priority 4
```

## Error Detection

The registry automatically detects these error patterns:

- `credit.*exhausted`
- `quota.*exceeded`
- `rate.*limit`
- `insufficient.*credits`
- `billing.*issue`
- `payment.*required`
- `subscription.*expired`
- `usage.*limit`
- `429.*too many requests`
- `overloaded`

When detected, the agent is automatically marked as `no_credits` and the next agent is tried.

## Troubleshooting

### Agent stuck in error state
```bash
sdlc agent reset <agent-name>
```

### Want to disable fallback temporarily
Set in spec.yaml:
```yaml
agent_fallback: false
```

### Check what's happening
```bash
sdlc agent list        # See current status
sdlc agent status      # See statistics
```

### Registry file location
The registry is stored at:
```
.sdlc/agent_registry.json
```

You can view or edit it directly if needed.

## Examples

### Scenario 1: Claude runs out of credits
```bash
$ sdlc state transition --auto
Using agent: claude-code
✗ Credit limit exceeded
Falling back to: kiro
✓ Executed with kiro
```

### Scenario 2: All agents exhausted
```bash
$ sdlc agent list
...all showing no_credits...

# Wait for credits to refill, then:
$ sdlc agent reset --all
✓ All agents reset to available
```

### Scenario 3: Adding a new agent
```bash
$ sdlc agent add windsurf --priority 5
✓ Added agent 'windsurf' with priority 5

# Update memory.py to configure CLI command
# Then it's ready to use!
```

## Best Practices

1. **Set Priority**: Put your most reliable/cheapest agent as priority 1
2. **Monitor Status**: Check `sdlc agent list` periodically
3. **Reset Daily**: If you have daily quotas, reset agents at start of day
4. **Track Costs**: Lower priority agents may have different pricing
5. **Backup Agents**: Keep at least 2-3 agents configured for reliability

## Integration

The registry integrates seamlessly with existing workflows:

- `sdlc state transition` - Uses registry automatically
- Custom skills - Respect fallback configuration
- UI server - Shows which agent executed each phase
- Webhook triggers - Fallback works in automated mode too

## Technical Details

### Registry Storage
Located at `.sdlc/agent_registry.json`:

```json
{
  "agents": {
    "claude-code": {
      "name": "claude-code",
      "status": "available",
      "priority": 1,
      "last_used": "2026-04-22T10:30:00Z",
      "last_error": null,
      "failure_count": 0,
      "success_count": 42
    }
  },
  "last_updated": "2026-04-22T10:30:00Z"
}
```

### Failure Recovery
- First failure: Try next agent
- 3+ failures: Mark as `error` status
- Success: Reset failure count
- Manual reset: Clears error status

## Support

For issues or feature requests, check:
- GitHub issues: https://github.com/your-repo/issues
- Documentation: docs/AGENT_REGISTRY.md
