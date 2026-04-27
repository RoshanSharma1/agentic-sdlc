"""
Agent CLI Configuration - Actual commands for each provider.

Configure the exact CLI commands to get usage, billing, and credit information
from each AI agent provider.
"""

# Configuration for each agent's CLI commands
AGENT_CLI_COMMANDS = {
    "claude-code": {
        "check_installed": ["which", "claude"],
        "usage": None,  # TODO: Add actual Claude usage command
        "credits": None,  # TODO: Add actual Claude credits command
        "billing": None,  # TODO: Add actual Claude billing command
        "account": None,  # TODO: Add actual Claude account command
        # Expected output format and parsing rules
        "parser": {
            "credits_pattern": r'credits?[:\s]+(\d+)',
            "cost_pattern": r'\$(\d+\.?\d*)',
            "tokens_pattern": r'tokens?[:\s]+(\d+)',
            "tier_pattern": r'(pro|team|enterprise|free|plus)',
            "reset_date_pattern": r'reset[:\s]+(\d{4}-\d{2}-\d{2})',
            "billing_cycle_pattern": r'cycle[:\s]+(monthly|weekly|yearly)',
        }
    },

    "kiro": {
        "check_installed": ["which", "kiro-cli"],
        "usage": None,  # TODO: Add actual Kiro usage command (e.g., ["kiro-cli", "usage"])
        "credits": None,  # TODO: Add actual Kiro credits command
        "billing": None,  # TODO: Add actual Kiro billing command
        "account": None,  # TODO: Add actual Kiro account command
        "parser": {
            "credits_pattern": r'credits?[:\s]+(\d+)',
            "cost_pattern": r'\$(\d+\.?\d*)',
            "tokens_pattern": r'tokens?[:\s]+(\d+)',
            "tier_pattern": r'(pro|free|enterprise|plus)',
            "reset_date_pattern": r'reset[:\s]+(\d{4}-\d{2}-\d{2})',
            "billing_cycle_pattern": r'cycle[:\s]+(monthly|weekly|yearly)',
        }
    },

    "codex": {
        "check_installed": ["which", "codex"],
        "usage": None,  # TODO: Add actual Codex/OpenAI usage command
        "credits": None,  # TODO: Add actual Codex credits command
        "billing": None,  # TODO: Add actual Codex billing command
        "account": None,  # TODO: Add actual Codex account command
        "parser": {
            "credits_pattern": r'credits?[:\s]+(\d+)',
            "cost_pattern": r'\$(\d+\.?\d*)',
            "tokens_pattern": r'tokens?[:\s]+(\d+)',
            "tier_pattern": r'(pro|free|enterprise|plus)',
            "reset_date_pattern": r'reset[:\s]+(\d{4}-\d{2}-\d{2})',
            "billing_cycle_pattern": r'cycle[:\s]+(monthly|weekly|yearly)',
        }
    },

    "gemini": {
        "check_installed": ["which", "gemini"],
        "usage": None,
        "credits": None,
        "billing": None,
        "account": None,
        "parser": {
            "credits_pattern": r'credits?[:\s]+(\d+)',
            "cost_pattern": r'\$(\d+\.?\d*)',
            "tokens_pattern": r'tokens?[:\s]+(\d+)',
            "tier_pattern": r'(pro|free|enterprise|plus)',
            "reset_date_pattern": r'reset[:\s]+(\d{4}-\d{2}-\d{2})',
            "billing_cycle_pattern": r'cycle[:\s]+(monthly|weekly|yearly)',
        }
    },
}

# Provider-specific notes and documentation links
PROVIDER_DOCS = {
    "claude-code": {
        "docs_url": "https://docs.anthropic.com/",
        "notes": "Claude CLI may not have built-in usage commands. May need to use Anthropic API directly.",
    },
    "kiro": {
        "docs_url": "https://kiro.ai/docs",  # Update with actual URL
        "notes": "Check Kiro documentation for actual CLI commands.",
    },
    "codex": {
        "docs_url": "https://platform.openai.com/docs/",
        "notes": "OpenAI CLI: Check if 'openai api usage' or similar command exists.",
    },
    "gemini": {
        "docs_url": "https://ai.google.dev/",
        "notes": "Gemini CLI usage is currently tracked locally in the agent registry.",
    },
}


def get_usage_command(agent_name: str) -> list[str] | None:
    """Get the usage command for an agent."""
    config = AGENT_CLI_COMMANDS.get(agent_name)
    if config:
        return config.get("usage")
    return None


def get_credits_command(agent_name: str) -> list[str] | None:
    """Get the credits command for an agent."""
    config = AGENT_CLI_COMMANDS.get(agent_name)
    if config:
        return config.get("credits")
    return None


def get_billing_command(agent_name: str) -> list[str] | None:
    """Get the billing command for an agent."""
    config = AGENT_CLI_COMMANDS.get(agent_name)
    if config:
        return config.get("billing")
    return None


def get_account_command(agent_name: str) -> list[str] | None:
    """Get the account command for an agent."""
    config = AGENT_CLI_COMMANDS.get(agent_name)
    if config:
        return config.get("account")
    return None


def get_parser_config(agent_name: str) -> dict:
    """Get the parser configuration for an agent."""
    config = AGENT_CLI_COMMANDS.get(agent_name, {})
    return config.get("parser", {})
