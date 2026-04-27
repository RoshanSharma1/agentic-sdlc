"""GitHub integration via `gh` CLI — PRs, issues, labels, feedback ingestion."""
from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path
from typing import Optional


def _gh(*args: str, cwd: Optional[Path] = None, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["gh", *args],
        capture_output=True, text=True,
        cwd=str(cwd) if cwd else None,
        check=check,
    )


def is_available() -> bool:
    try:
        _gh("auth", "status", check=False)
        return True
    except FileNotFoundError:
        return False


def list_repositories(limit: int = 100) -> list[dict[str, str]]:
    try:
        r = _gh(
            "repo",
            "list",
            "--limit",
            str(limit),
            "--json",
            "name,nameWithOwner,url,isPrivate,description,viewerPermission",
        )
        repos = json.loads(r.stdout)
        return [
            {
                "name": item.get("name", ""),
                "full_name": item.get("nameWithOwner", ""),
                "url": item.get("url", ""),
                "description": item.get("description") or "",
                "visibility": "private" if item.get("isPrivate") else "public",
                "permission": item.get("viewerPermission") or "",
            }
            for item in repos
            if item.get("nameWithOwner")
        ]
    except (subprocess.CalledProcessError, json.JSONDecodeError, FileNotFoundError):
        return []


def create_repository(name: str, description: str = "", private: bool = True) -> Optional[dict[str, str]]:
    visibility = "private" if private else "public"
    args = ["repo", "create", name, f"--{visibility}", "--json", "name,nameWithOwner,url,isPrivate,description"]
    if description:
        args.extend(["--description", description])
    try:
        r = _gh(*args)
        data = json.loads(r.stdout)
        return {
            "name": data.get("name", ""),
            "full_name": data.get("nameWithOwner", ""),
            "url": data.get("url", ""),
            "description": data.get("description") or "",
            "visibility": "private" if data.get("isPrivate") else "public",
        }
    except (subprocess.CalledProcessError, json.JSONDecodeError, FileNotFoundError):
        return None


# ── Labels ────────────────────────────────────────────────────────────────────

SDLC_LABELS: dict[str, str] = {
    "sdlc:requirement":    "0075ca",
    "sdlc:design":         "e4e669",
    "sdlc:plan":           "f9d0c4",
    "sdlc:implementation": "0e8a16",
    "sdlc:testing":        "bfd4f2",
    "sdlc:review":         "5319e7",
    "awaiting-review":     "fbca04",
    "blocked":             "b60205",
}

PHASE_LABEL: dict[str, str] = {
    "requirement":    "sdlc:requirement",
    "design":         "sdlc:design",
    "planning":       "sdlc:plan",
    "implementation": "sdlc:implementation",
    "testing":        "sdlc:testing",
    "review":         "sdlc:review",
}


def setup_labels(repo: str) -> list[str]:
    created = []
    for name, color in SDLC_LABELS.items():
        r = _gh("label", "create", name, "--repo", repo, "--color", color, "--force", check=False)
        if r.returncode == 0:
            created.append(name)
    return created


# ── Issues ────────────────────────────────────────────────────────────────────

def create_child_issue(repo: str, title: str, body: str) -> Optional[int]:
    try:
        r = _gh("issue", "create", "--repo", repo, "--title", title, "--body", body)
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


# ── PRs ───────────────────────────────────────────────────────────────────────

def create_pr(
    repo: str,
    phase: str,
    branch: str,
    body: str,
    base: str = "main",
    closes_issues: Optional[list] = None,
    project_name: str = "",
) -> Optional[str]:
    all_closes = list(closes_issues or [])
    if all_closes:
        body += "\n\n" + "\n".join(f"Closes #{n}" for n in all_closes)
    prefix = f"[{project_name}] " if project_name else ""
    title = f"{prefix}sdlc({phase}): {phase} phase"
    try:
        r = _gh("pr", "create", "--repo", repo, "--title", title,
                "--base", base, "--head", branch, "--body", body)
        return r.stdout.strip()
    except subprocess.CalledProcessError:
        return None


def get_pr_status(repo: str, branch: str) -> Optional[str]:
    try:
        r = _gh("pr", "view", branch, "--repo", repo,
                "--json", "state,reviewDecision,mergedAt", check=False)
        if r.returncode != 0:
            return None
        data = json.loads(r.stdout)
        if data.get("mergedAt"):
            return "merged"
        state = data.get("state", "").upper()
        if state == "CLOSED":
            return "closed"
        if data.get("reviewDecision") == "APPROVED":
            return "approved"
        return "open"
    except (subprocess.CalledProcessError, json.JSONDecodeError):
        return None


def get_pr_number(repo: str, branch: str) -> Optional[int]:
    try:
        r = _gh("pr", "view", branch, "--repo", repo, "--json", "number", check=False)
        if r.returncode != 0:
            return None
        return json.loads(r.stdout).get("number")
    except (subprocess.CalledProcessError, json.JSONDecodeError):
        return None


def get_pr_comments(repo: str, branch: str) -> list[str]:
    collected: list[str] = []
    try:
        r = _gh("pr", "view", branch, "--repo", repo, "--json", "comments,reviews")
        data = json.loads(r.stdout)
        for c in data.get("comments", []):
            if c.get("body", "").strip():
                collected.append(c["body"])
        for review in data.get("reviews", []):
            if review.get("body", "").strip():
                collected.append(review["body"])
    except (subprocess.CalledProcessError, json.JSONDecodeError):
        pass
    return collected


def merge_pr(repo: str, branch: str) -> bool:
    try:
        _gh("pr", "merge", branch, "--repo", repo, "--squash", "--delete-branch")
        return True
    except subprocess.CalledProcessError:
        return False


# ── Feedback ingestion ────────────────────────────────────────────────────────

def pull_pr_feedback(repo: str, branch: str, feedback_dir: Path, phase: str) -> int:
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
