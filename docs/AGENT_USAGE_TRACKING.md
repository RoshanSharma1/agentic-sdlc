# Agent Usage Tracking

This document explains the agent usage tracking system implemented in Chorus.

## Overview

The agent usage tracking system monitors and records detailed metrics about AI coding agent usage, including:
- API call counts
- Token usage (input/output)
- Cost estimates
- Credit availability
- Daily usage trends

## Backend Implementation

### AgentInfo Data Structure

Each agent tracks the following metrics:

```python
@dataclass
class AgentInfo:
    # Basic info
    name: str
    status: AgentStatus
    priority: int
    provider: str
    
    # Usage tracking
    total_api_calls: int              # Total number of API calls made
    total_tokens_used: int            # Total tokens (input + output)
    total_input_tokens: int           # Total input tokens
    total_output_tokens: int          # Total output tokens
    estimated_cost_usd: float         # Estimated cost in USD
    credits_remaining: int | None     # Available credits (if supported)
    credits_limit: int | None         # Total credit limit
    daily_usage: dict                 # Daily usage breakdown (last 90 days)
```

### Recording Usage

To record agent usage, call the `record_usage` method:

```python
from sdlc_orchestrator.agent_registry import AgentRegistry

registry = AgentRegistry(project_dir)

# Record usage after an API call
registry.record_usage(
    "claude-code",
    input_tokens=1500,
    output_tokens=800,
    # cost_usd is auto-calculated if not provided
    credits_remaining=98500  # optional
)
```

### Cost Calculation

Costs are automatically calculated based on provider pricing:

```python
PROVIDER_PRICING = {
    "anthropic": {
        "input": 3.00,   # $3 per 1M input tokens
        "output": 15.00,  # $15 per 1M output tokens
    },
    "openai": {
        "input": 2.50,
        "output": 10.00,
    },
    # ... other providers
}
```

You can customize pricing by updating the `PROVIDER_PRICING` dictionary in `agent_registry.py`.

### Daily Usage Tracking

The system automatically tracks daily usage for the last 90 days:

```python
{
    "2026-04-26": {
        "api_calls": 15,
        "input_tokens": 25000,
        "output_tokens": 18000,
        "total_tokens": 43000,
        "cost_usd": 0.34
    }
}
```

## UI Display

The AgentsView displays comprehensive usage metrics for each agent:

### Agent Card Sections

1. **Header**: Agent name, provider, status, and reset button
2. **Stats**: Priority, success count, failure count, headless support
3. **Usage Metrics**: API calls, total tokens, estimated cost, available credits
4. **Token Breakdown**: Visual bar showing input vs output token ratio
5. **Footer**: Last used timestamp

### Color Coding

- **Success** (green): Available agents, successful executions
- **Warning** (yellow): No credits, cooldown
- **Danger** (red): Errors, failures
- **Info** (blue): General stats, costs

## Integration Examples

### Example 1: Tracking Claude Code Usage

```python
# After executing claude-code
result = subprocess.run(["claude", "-p", "..."], ...)

if result.returncode == 0:
    # Parse output for token usage (if available)
    registry.record_usage(
        "claude-code",
        input_tokens=extracted_input_tokens,
        output_tokens=extracted_output_tokens
    )
    registry.mark_agent_used("claude-code", success=True)
```

### Example 2: Tracking OpenAI Codex Usage

```python
# After API call to OpenAI
response = openai.Completion.create(...)

registry.record_usage(
    "codex",
    input_tokens=response.usage.prompt_tokens,
    output_tokens=response.usage.completion_tokens,
    credits_remaining=response.usage.total_tokens_remaining  # if available
)
```

### Example 3: Manual Cost Recording

```python
# If you have the exact cost
registry.record_usage(
    "custom-agent",
    input_tokens=2000,
    output_tokens=1000,
    cost_usd=0.125  # Manually specify cost
)
```

## API Endpoints

### Get Agent Stats

```
GET /api/projects/{project_name}/agents
```

Returns agent registry with all usage metrics.

### Reset Agent

```
POST /api/projects/{project_name}/agents/{agent_name}/reset
```

Resets agent status to available (does not clear usage stats).

## Best Practices

1. **Record usage immediately** after each API call while metrics are fresh
2. **Parse provider responses** to extract accurate token counts
3. **Update credit limits** when available from provider APIs
4. **Monitor daily usage** to detect unusual patterns
5. **Set up alerts** for low credit warnings (implement separately)

## Future Enhancements

Potential additions to the tracking system:

- Real-time credit fetching from provider APIs
- Usage alerts and notifications
- Budget limits per agent
- Usage analytics and trends
- Export usage data to CSV/JSON
- Integration with billing systems
- Multi-project usage aggregation

## Troubleshooting

### Usage not updating

- Ensure `record_usage()` is called after each API call
- Check that the agent name matches exactly
- Verify the registry is saved (`_save()` is called)

### Incorrect costs

- Update `PROVIDER_PRICING` with current rates
- Manually specify `cost_usd` if you have exact costs
- Check token counts are accurate

### Missing daily usage

- Daily usage is automatically tracked when `record_usage()` is called
- Only last 90 days are retained
- Check the date format is ISO 8601 (YYYY-MM-DD)
