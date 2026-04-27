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
def list_agents(project_dir: Path):
    """List all registered agents and their status."""
    registry = AgentRegistry(project_dir)

    table = Table(title="AI Agent Registry", show_header=True)
    table.add_column("Priority", style="cyan", width=8)
    table.add_column("Agent", style="bold")
    table.add_column("Status", width=12)
    table.add_column("Success", justify="right", width=8)
    table.add_column("Failures", justify="right", width=8)
    table.add_column("Last Used", width=20)

    for agent in registry.list_agents():
        # Status with color
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

        table.add_row(
            str(agent.priority),
            agent.name,
            status_text,
            str(agent.success_count),
            str(agent.failure_count),
            last_used,
        )

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
