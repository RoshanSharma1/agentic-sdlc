"""Agent registry management commands."""
from __future__ import annotations

from pathlib import Path

import click
from rich.table import Table
from rich.panel import Panel

from sdlc_orchestrator.agent_registry import AgentRegistry, AgentStatus
from sdlc_orchestrator.commands import console


@click.group(name="agent")
def agent():
    """Manage AI agent registry."""
    pass


@agent.command(name="list")
@click.option("--project-dir", type=Path, default=Path.cwd())
@click.option("--no-health", is_flag=True, help="Skip real-time health check")
def list_agents(project_dir: Path, no_health: bool):
    """List all registered agents and their status."""
    from sdlc_orchestrator.agent_status_checker import check_all_agents

    registry = AgentRegistry(project_dir)

    # Get real-time health by default (unless --no-health is specified)
    health = not no_health
    health_status = check_all_agents(project_dir) if health else {}

    table = Table(title="AI Agent Registry", show_header=True)
    table.add_column("Priority", style="cyan", width=8)
    table.add_column("Agent", style="bold", width=12)
    table.add_column("Registry Status", width=14)
    if health:
        table.add_column("Live State", width=12)
        table.add_column("Exhausted", width=10)
    table.add_column("Success", justify="right", width=8)
    table.add_column("Failures", justify="right", width=8)
    table.add_column("Last Used", width=20)

    for agent in registry.list_agents():
        # Registry status with color
        status_color = {
            AgentStatus.AVAILABLE: "green",
            AgentStatus.NO_CREDITS: "yellow",
            AgentStatus.COOLDOWN: "blue",
            AgentStatus.ERROR: "red",
            AgentStatus.DISABLED: "dim",
        }
        status_text = f"[{status_color.get(agent.status, 'white')}]{agent.status.value}[/]"

        # Last used formatting
        last_used = agent.last_used or "Never"
        if agent.last_used:
            # Show just the date/time
            last_used = agent.last_used.split("T")[0] + " " + agent.last_used.split("T")[1][:5]

        # Build row
        row = [
            str(agent.priority),
            agent.name,
            status_text,
        ]

        # Add health info if available
        if health:
            live_status = health_status.get(agent.name)
            if live_status:
                # State color coding
                state_colors = {
                    "ready": "green",
                    "exhausted": "red",
                    "signed_out": "yellow",
                    "not_installed": "dim",
                    "unknown": "blue",
                }
                state_color = state_colors.get(live_status.state, "white")
                state_text = f"[{state_color}]{live_status.state}[/]"

                # Exhausted indicator
                exhausted = "⚠" if live_status.exhausted else ("✓" if live_status.exhausted is False else "?")
                if live_status.exhausted:
                    exhausted = f"[red]{exhausted}[/]"
                elif live_status.exhausted is False:
                    exhausted = f"[green]{exhausted}[/]"

                row.extend([state_text, exhausted])
            else:
                row.extend(["[dim]N/A[/]", "[dim]?[/]"])

        row.extend([
            str(agent.success_count),
            str(agent.failure_count),
            last_used,
        ])

        table.add_row(*row)

    console.print(table)

    # Show last errors if any
    errors = [(a.name, a.last_error) for a in registry.list_agents() if a.last_error]
    if errors:
        console.print("\n[yellow]Recent Errors:[/yellow]")
        for name, error in errors:
            console.print(f"  [red]{name}:[/red] {error[:100]}...")


@agent.command(name="status")
@click.option("--project-dir", type=Path, default=Path.cwd())
def status(project_dir: Path):
    """Show agent registry statistics."""
    registry = AgentRegistry(project_dir)
    stats = registry.get_stats()

    panel = Panel(
        f"""[bold]Total Agents:[/bold] {stats['total_agents']}
[bold]Available:[/bold] {stats['available']}
[bold]Active Agent:[/bold] {stats.get('active_agent') or 'none'}
[bold]Out of Credits:[/bold] {sum(1 for a in stats['agents'].values() if a['status'] == 'no_credits')}
[bold]Cooldown:[/bold] {sum(1 for a in stats['agents'].values() if a['status'] == 'cooldown')}
[bold]Errors:[/bold] {sum(1 for a in stats['agents'].values() if a['status'] == 'error')}
[bold]Disabled:[/bold] {sum(1 for a in stats['agents'].values() if a['status'] == 'disabled')}""",
        title="Agent Registry Status",
        border_style="blue",
    )
    console.print(panel)


@agent.command(name="reset")
@click.argument("agent_name", required=False)
@click.option("--all", is_flag=True, help="Reset all agents")
@click.option("--project-dir", type=Path, default=Path.cwd())
def reset(agent_name: str | None, all: bool, project_dir: Path):
    """Reset agent status to available."""
    registry = AgentRegistry(project_dir)

    if all:
        registry.reset_all()
        console.print("[green]✓[/green] All agents reset to available")
    elif agent_name:
        if registry.get_agent(agent_name):
            registry.reset_agent(agent_name)
            console.print(f"[green]✓[/green] Agent '{agent_name}' reset to available")
        else:
            console.print(f"[red]✗[/red] Agent '{agent_name}' not found")
    else:
        console.print("[yellow]Specify an agent name or use --all[/yellow]")


@agent.command(name="add")
@click.argument("agent_name")
@click.option("--priority", type=int, default=99, help="Priority (lower = higher priority)")
@click.option("--project-dir", type=Path, default=Path.cwd())
def add_agent(agent_name: str, priority: int, project_dir: Path):
    """Add a new agent to the registry."""
    registry = AgentRegistry(project_dir)

    if registry.get_agent(agent_name):
        console.print(f"[yellow]Agent '{agent_name}' already exists[/yellow]")
    else:
        registry.add_agent(agent_name, priority)
        console.print(f"[green]✓[/green] Added agent '{agent_name}' with priority {priority}")


@agent.command(name="remove")
@click.argument("agent_name")
@click.option("--project-dir", type=Path, default=Path.cwd())
def remove_agent(agent_name: str, project_dir: Path):
    """Remove an agent from the registry."""
    registry = AgentRegistry(project_dir)

    if registry.get_agent(agent_name):
        registry.remove_agent(agent_name)
        console.print(f"[green]✓[/green] Removed agent '{agent_name}'")
    else:
        console.print(f"[red]✗[/red] Agent '{agent_name}' not found")


@agent.command(name="disable")
@click.argument("agent_name")
@click.option("--project-dir", type=Path, default=Path.cwd())
def disable_agent(agent_name: str, project_dir: Path):
    """Disable an agent (won't be used for fallback)."""
    registry = AgentRegistry(project_dir)

    if registry.get_agent(agent_name):
        registry.set_agent_status(agent_name, AgentStatus.DISABLED)
        console.print(f"[yellow]Agent '{agent_name}' disabled[/yellow]")
    else:
        console.print(f"[red]✗[/red] Agent '{agent_name}' not found")


@agent.command(name="enable")
@click.argument("agent_name")
@click.option("--project-dir", type=Path, default=Path.cwd())
def enable_agent(agent_name: str, project_dir: Path):
    """Enable a disabled agent."""
    registry = AgentRegistry(project_dir)

    if registry.get_agent(agent_name):
        registry.set_agent_status(agent_name, AgentStatus.AVAILABLE)
        console.print(f"[green]✓[/green] Agent '{agent_name}' enabled")
    else:
        console.print(f"[red]✗[/red] Agent '{agent_name}' not found")


@agent.command(name="health")
@click.option("--project-dir", type=Path, default=Path.cwd())
def health(project_dir: Path):
    """Check real-time health and availability of all agents."""
    from sdlc_orchestrator.agent_status_checker import check_all_agents

    console.print("\n[bold cyan]Checking agent health...[/bold cyan]\n")

    statuses = check_all_agents(project_dir)

    table = Table(title="Agent Health Status", show_header=True)
    table.add_column("Agent", style="bold", width=12)
    table.add_column("State", width=12)
    table.add_column("Installed", width=10)
    table.add_column("Authenticated", width=13)
    table.add_column("Exhausted", width=10)
    table.add_column("Version", width=10)
    table.add_column("Next Reset", width=20)

    for agent_name, status in statuses.items():
        # State color coding
        state_colors = {
            "ready": "green",
            "exhausted": "red",
            "signed_out": "yellow",
            "not_installed": "dim",
            "unknown": "blue",
        }
        state_color = state_colors.get(status.state, "white")
        state_text = f"[{state_color}]{status.state}[/]"

        # Boolean indicators
        installed = "✓" if status.installed else "✗"
        authenticated = "✓" if status.authenticated else ("✗" if status.authenticated is False else "?")
        exhausted = "⚠" if status.exhausted else ("✓" if status.exhausted is False else "?")

        # Color exhausted field
        if status.exhausted:
            exhausted = f"[red]{exhausted}[/]"
        elif status.exhausted is False:
            exhausted = f"[green]{exhausted}[/]"

        table.add_row(
            agent_name,
            state_text,
            installed,
            authenticated,
            exhausted,
            status.version or "N/A",
            status.next_reset_at or "N/A",
        )

    console.print(table)

    # Show usage windows for agents with detailed info
    for agent_name, status in statuses.items():
        if status.usage_windows:
            console.print(f"\n[bold]{agent_name} Usage:[/bold]")
            for window in status.usage_windows:
                if window.used_percentage is not None:
                    bar_width = 30
                    filled = int(bar_width * window.used_percentage / 100)
                    bar = "█" * filled + "░" * (bar_width - filled)

                    color = "green" if window.used_percentage < 80 else "yellow" if window.used_percentage < 100 else "red"
                    console.print(
                        f"  {window.label}: [{color}]{bar}[/] {window.used_percentage}% used"
                    )
                    if window.reset_at:
                        console.print(f"    Resets: {window.reset_at}")

    # Show errors
    errors = [(name, status.error_message) for name, status in statuses.items() if status.error_message]
    if errors:
        console.print("\n[yellow]⚠ Issues:[/yellow]")
        for name, error in errors:
            console.print(f"  [red]{name}:[/red] {error}")

    console.print()  # blank line
