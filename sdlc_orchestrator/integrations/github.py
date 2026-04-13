"""
GitHub integration via `gh` CLI.

Owns: Epic issues, child issues per phase, project board, PR per phase,
      reading PR/issue comments as feedback.
"""
from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path
from typing import Optional


def _gh(*args: str, cwd: Optional[Path] = None, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["gh", *args],
        capture_output=True,
        text=True,
        cwd=str(cwd) if cwd else None,
        check=check,
    )


def is_available() -> bool:
    try:
        _gh("auth", "status", check=False)
        return True
    except FileNotFoundError:
        return False


# ── Project board ─────────────────────────────────────────────────────────────

BOARD_COLUMNS = [
    "Backlog",
    "In Progress",
    "Awaiting Review",
    "Blocked",
    "Done",
]


def create_project_board(project_name: str, repo: str) -> Optional[str]:
    """Create a GitHub project board. Returns project ID or None."""
    try:
        r = _gh(
            "project", "create",
            "--owner", repo.split("/")[0],
            "--title", f"{project_name} SDLC Board",
            "--format", "json",
        )
        data = json.loads(r.stdout)
        return str(data.get("id") or data.get("number"))
    except (subprocess.CalledProcessError, json.JSONDecodeError, KeyError):
        return None


# ── Issues ────────────────────────────────────────────────────────────────────

def create_epic(repo: str, project_name: str, body: str) -> Optional[int]:
    """Create the Epic issue. Returns issue number."""
    try:
        r = _gh(
            "issue", "create",
            "--repo", repo,
            "--title", f"[EPIC] {project_name}",
            "--body", body,
            "--label", "epic",
        )
        # gh prints the issue URL; extract number
        match = re.search(r"/issues/(\d+)", r.stdout)
        return int(match.group(1)) if match else None
    except subprocess.CalledProcessError:
        return None


def create_child_issue(repo: str, title: str, body: str,
                       parent_issue: Optional[int] = None) -> Optional[int]:
    """Create a child issue. Returns issue number."""
    try:
        cmd = ["issue", "create", "--repo", repo, "--title", title, "--body", body]
        r = _gh(*cmd)
        match = re.search(r"/issues/(\d+)", r.stdout)
        return int(match.group(1)) if match else None
    except subprocess.CalledProcessError:
        return None


def close_issue(repo: str, issue_number: int, comment: str = "") -> None:
    try:
        if comment:
            _gh("issue", "comment", str(issue_number), "--repo", repo, "--body", comment)
        _gh("issue", "close", str(issue_number), "--repo", repo)
    except subprocess.CalledProcessError:
        pass


# ── Pull Requests ─────────────────────────────────────────────────────────────

def create_pr(repo: str, phase: str, branch: str,
              body: str, base: str = "main") -> Optional[str]:
    """Create a PR for a phase. Returns PR URL or None."""
    try:
        r = _gh(
            "pr", "create",
            "--repo", repo,
            "--title", f"sdlc({phase}): complete {phase} phase",
            "--base", base,
            "--head", branch,
            "--body", body,
        )
        return r.stdout.strip()
    except subprocess.CalledProcessError:
        return None


def get_pr_status(repo: str, branch: str) -> Optional[str]:
    """
    Return the status of the PR for this branch.
    Returns: "approved", "merged", "closed", "open", or None if no PR found.
    """
    try:
        r = _gh(
            "pr", "view", branch,
            "--repo", repo,
            "--json", "state,reviewDecision,mergedAt",
            check=False,
        )
        if r.returncode != 0:
            return None
        data = json.loads(r.stdout)
        if data.get("mergedAt"):
            return "merged"
        state = data.get("state", "").upper()
        if state == "CLOSED":
            return "closed"
        review = data.get("reviewDecision", "")
        if review == "APPROVED":
            return "approved"
        return "open"
    except (subprocess.CalledProcessError, json.JSONDecodeError):
        return None


def get_pr_number(repo: str, branch: str) -> Optional[int]:
    """Return the PR number for a branch, or None."""
    try:
        r = _gh("pr", "view", branch, "--repo", repo, "--json", "number", check=False)
        if r.returncode != 0:
            return None
        return json.loads(r.stdout).get("number")
    except (subprocess.CalledProcessError, json.JSONDecodeError):
        return None


def get_pr_comments(repo: str, branch: str) -> list[str]:
    """Fetch review comments from the PR for this branch."""
    try:
        r = _gh(
            "pr", "view", branch,
            "--repo", repo,
            "--json", "comments",
        )
        data = json.loads(r.stdout)
        return [c["body"] for c in data.get("comments", [])]
    except (subprocess.CalledProcessError, json.JSONDecodeError):
        return []


def merge_pr(repo: str, branch: str) -> bool:
    try:
        _gh("pr", "merge", branch, "--repo", repo, "--squash", "--delete-branch")
        return True
    except subprocess.CalledProcessError:
        return False


# ── Feedback ingestion ────────────────────────────────────────────────────────

def pull_pr_feedback(repo: str, branch: str, feedback_dir: Path, phase: str) -> int:
    """
    Pull new PR review comments into feedback/<phase>.md.
    Returns number of new comments ingested.
    """
    comments = get_pr_comments(repo, branch)
    if not comments:
        return 0

    feedback_dir.mkdir(parents=True, exist_ok=True)
    fb_file = feedback_dir / f"{phase}.md"

    existing = fb_file.read_text() if fb_file.exists() else ""
    new_comments = [c for c in comments if c not in existing]

    if new_comments:
        with fb_file.open("a") as f:
            for comment in new_comments:
                f.write(f"\n---\n{comment}\n")

    return len(new_comments)
