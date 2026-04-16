"""sdlc CLI — entry point. All commands live in sdlc_orchestrator/commands/."""
import click

from sdlc_orchestrator.commands.state import state
from sdlc_orchestrator.commands.artifact import artifact
from sdlc_orchestrator.commands.story import story
from sdlc_orchestrator.commands.github import github
from sdlc_orchestrator.commands.init import init
from sdlc_orchestrator.commands.project import project
from sdlc_orchestrator.commands.ops import status, notify, watch, webhook, tick, relink


@click.group()
def cli():
    """Autonomous SDLC orchestrator — Claude-driven."""
    pass


cli.add_command(init)
cli.add_command(project)
cli.add_command(state)
cli.add_command(artifact)
cli.add_command(story)
cli.add_command(github)
cli.add_command(status)
cli.add_command(notify)
cli.add_command(watch)
cli.add_command(webhook)
cli.add_command(tick)
cli.add_command(relink)
