"""
Shared utilities: SDLC home resolution and symlink management.

State directory layout:
  project-repo/
    .sdlc/                  ← actual state (gitignored)
      spec.yaml
      memory/project.md
      workflow/state.json
      workflow/artifacts/
      workflow/logs/
      feedback/
    CLAUDE.md               ← generated, gitignored
    .claude/settings.json   ← hooks, gitignored

  ~/.sdlc/projects/<slug>   ← symlink → project-repo/.sdlc/
"""
from __future__ import annotations

import os
import re
from pathlib import Path


def project_slug(project_dir: Path) -> str:
    """Stable slug derived from the project directory name."""
    name = project_dir.resolve().name
    return re.sub(r"[^a-z0-9-]", "-", name.lower()).strip("-")


def sdlc_home(project_dir: Path) -> Path:
    """
    Return the SDLC state directory for a project.

    Preference order:
      1. ~/.sdlc/projects/<slug>  (follows symlink)
      2. project_dir/.sdlc/       (direct fallback)
    """
    slug = project_slug(project_dir)
    link = Path.home() / ".sdlc" / "projects" / slug
    if link.exists():
        return link.resolve()
    return project_dir / ".sdlc"


def create_symlink(project_dir: Path) -> Path:
    """
    Create ~/.sdlc/projects/<slug>  →  project_dir/.sdlc/
    Returns the symlink path.
    """
    sdlc_dir = project_dir / ".sdlc"
    sdlc_dir.mkdir(parents=True, exist_ok=True)

    slug = project_slug(project_dir)
    link = Path.home() / ".sdlc" / "projects" / slug
    link.parent.mkdir(parents=True, exist_ok=True)

    if link.is_symlink():
        link.unlink()
    elif link.exists():
        # Real directory already there — leave it
        return link

    link.symlink_to(sdlc_dir.resolve())
    return link


def find_project_dir(start: Path | None = None) -> Path | None:
    """Walk up from start (default: cwd) looking for a project root (.sdlc/ marker)."""
    p = (start or Path.cwd()).resolve()
    for candidate in [p, *p.parents]:
        if (candidate / ".sdlc").is_dir():
            return candidate
    return None


def update_gitignore(project_dir: Path) -> None:
    """Add SDLC entries to .gitignore (idempotent)."""
    entries = [".sdlc/", "CLAUDE.md", ".claude/"]
    gi = project_dir / ".gitignore"
    existing = gi.read_text() if gi.exists() else ""
    to_add = [e for e in entries if e not in existing]
    if to_add:
        with gi.open("a") as f:
            f.write("\n# SDLC orchestrator\n")
            for e in to_add:
                f.write(e + "\n")
