"""
GitHub integration via `gh` CLI and GraphQL API.

Owns: Labels, project board (Projects v2), workflow automation,
      epic/phase/task issues, PR per phase, feedback ingestion.
"""
from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path
from typing import Optional


# ── helpers ───────────────────────────────────────────────────────────────────

def _gh(*args: str, cwd: Optional[Path] = None, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["gh", *args],
        capture_output=True, text=True,
        cwd=str(cwd) if cwd else None,
        check=check,
    )


def _graphql(query: str, **variables) -> dict:
    """Run a GitHub GraphQL query/mutation. Returns parsed JSON data dict."""
    cmd = ["api", "graphql", "-f", f"query={query}"]
    for k, v in variables.items():
        cmd += ["-f", f"{k}={v}"]
    r = _gh(*cmd, check=False)
    if r.returncode != 0:
        return {}
    try:
        return json.loads(r.stdout).get("data", {})
    except json.JSONDecodeError:
        return {}


def is_available() -> bool:
    try:
        _gh("auth", "status", check=False)
        return True
    except FileNotFoundError:
        return False


# ── Labels ────────────────────────────────────────────────────────────────────

SDLC_LABELS: dict[str, str] = {
    "sdlc:requirement":    "0075ca",  # blue
    "sdlc:design":         "e4e669",  # yellow
    "sdlc:plan":           "f9d0c4",  # pink
    "sdlc:implementation": "0e8a16",  # green
    "sdlc:testing":        "bfd4f2",  # light blue
    "sdlc:review":         "5319e7",  # purple
    "awaiting-review":     "fbca04",  # gold
    "blocked":             "b60205",  # red
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
    """Create SDLC labels in the repo. Returns list of created label names."""
    created = []
    for name, color in SDLC_LABELS.items():
        r = _gh(
            "label", "create", name,
            "--repo", repo,
            "--color", color,
            "--force",   # update if exists
            check=False,
        )
        if r.returncode == 0:
            created.append(name)
    return created


# ── Project board (Projects v2) ───────────────────────────────────────────────

BOARD_STATUSES = ["Backlog", "In Progress", "Awaiting Review", "Blocked", "Done"]


def create_project_board(project_name: str, repo: str) -> Optional[dict]:
    """
    Create a GitHub Projects v2 board.
    Returns project info dict: {number, node_id, status_field_id, status_options}
    or None on failure.
    """
    owner = repo.split("/")[0]
    try:
        r = _gh(
            "project", "create",
            "--owner", owner,
            "--title", project_name,
            "--format", "json",
        )
        data = json.loads(r.stdout)
        number = data.get("number") or data.get("id")
        node_id = data.get("id") or data.get("node_id") or ""
    except (subprocess.CalledProcessError, json.JSONDecodeError):
        return None

    if not number:
        return None

    # Fetch node_id if not returned directly
    if not node_id or not node_id.startswith("PVT"):
        node_id = _get_project_node_id(owner, int(number))

    if not node_id:
        return None

    fields = _get_project_fields(node_id)
    return {
        "number":          int(number),
        "node_id":         node_id,
        "status_field_id": fields.get("field_id", ""),
        "status_options":  fields.get("options", {}),
    }


def _get_project_node_id(owner: str, project_number: int) -> str:
    data = _graphql(
        """
        query($owner: String!, $number: Int!) {
          user(login: $owner) {
            projectV2(number: $number) { id }
          }
          organization(login: $owner) {
            projectV2(number: $number) { id }
          }
        }
        """,
        owner=owner,
        number=str(project_number),
    )
    return (
        (data.get("user") or {}).get("projectV2", {}).get("id")
        or (data.get("organization") or {}).get("projectV2", {}).get("id")
        or ""
    )


def _get_project_fields(project_node_id: str) -> dict:
    """Return {field_id, options: {name: option_id}} for the Status field."""
    data = _graphql(
        """
        query($projectId: ID!) {
          node(id: $projectId) {
            ... on ProjectV2 {
              fields(first: 20) {
                nodes {
                  ... on ProjectV2SingleSelectField {
                    id
                    name
                    options { id name }
                  }
                }
              }
            }
          }
        }
        """,
        projectId=project_node_id,
    )
    for field in (data.get("node") or {}).get("fields", {}).get("nodes", []):
        if field.get("name") == "Status":
            return {
                "field_id": field["id"],
                "options":  {opt["name"]: opt["id"] for opt in field.get("options", [])},
            }
    return {}


# ── Workflow automation ───────────────────────────────────────────────────────

# Built-in workflow names we want enabled
_WANTED_WORKFLOWS = {
    "Item closed",        # → Done
    "Pull request merged", # → Done
    "Item reopened",      # → In Progress
    "Auto-archive items",
}


def enable_project_workflows(project_node_id: str) -> list[str]:
    """
    Enable built-in GitHub Projects v2 workflow automations.
    Returns list of workflow names that were successfully enabled.
    """
    # Query available workflows
    data = _graphql(
        """
        query($projectId: ID!) {
          node(id: $projectId) {
            ... on ProjectV2 {
              workflows(first: 20) {
                nodes { id name enabled }
              }
            }
          }
        }
        """,
        projectId=project_node_id,
    )
    workflows = (data.get("node") or {}).get("workflows", {}).get("nodes", [])
    enabled = []
    for wf in workflows:
        if wf.get("name") in _WANTED_WORKFLOWS and not wf.get("enabled"):
            result = _graphql(
                """
                mutation($workflowId: ID!) {
                  updateProjectV2Workflow(input: {workflowId: $workflowId, enabled: true}) {
                    workflow { id name enabled }
                  }
                }
                """,
                workflowId=wf["id"],
            )
            wf_result = (result.get("updateProjectV2Workflow") or {}).get("workflow", {})
            if wf_result.get("enabled"):
                enabled.append(wf["name"])
    return enabled


# ── Board item management ─────────────────────────────────────────────────────

def _get_issue_node_id(repo: str, issue_number: int) -> str:
    owner, name = repo.split("/", 1)
    data = _graphql(
        """
        query($owner: String!, $name: String!, $number: Int!) {
          repository(owner: $owner, name: $name) {
            issue(number: $number) { id }
          }
        }
        """,
        owner=owner, name=name, number=str(issue_number),
    )
    return (data.get("repository") or {}).get("issue", {}).get("id", "")


def add_to_project(project_node_id: str, content_node_id: str) -> str:
    """Add an issue or PR to the project board. Returns project item ID."""
    data = _graphql(
        """
        mutation($projectId: ID!, $contentId: ID!) {
          addProjectV2ItemById(input: {projectId: $projectId, contentId: $contentId}) {
            item { id }
          }
        }
        """,
        projectId=project_node_id,
        contentId=content_node_id,
    )
    return (data.get("addProjectV2ItemById") or {}).get("item", {}).get("id", "")


def set_item_status(
    project_node_id: str,
    item_id: str,
    status_field_id: str,
    option_id: str,
) -> bool:
    """Move a project item to a status column. Returns True on success."""
    data = _graphql(
        """
        mutation($projectId: ID!, $itemId: ID!, $fieldId: ID!, $optionId: String!) {
          updateProjectV2ItemFieldValue(input: {
            projectId: $projectId
            itemId: $itemId
            fieldId: $fieldId
            value: { singleSelectOptionId: $optionId }
          }) {
            projectV2Item { id }
          }
        }
        """,
        projectId=project_node_id,
        itemId=item_id,
        fieldId=status_field_id,
        optionId=option_id,
    )
    return bool((data.get("updateProjectV2ItemFieldValue") or {}).get("projectV2Item"))


def move_phase_issue(project_info: dict, item_id: str, status: str) -> bool:
    """Move a phase issue to a named status column (e.g. 'In Progress')."""
    option_id = project_info.get("status_options", {}).get(status, "")
    if not option_id or not item_id:
        return False
    return set_item_status(
        project_info["node_id"],
        item_id,
        project_info["status_field_id"],
        option_id,
    )


# ── Issues ────────────────────────────────────────────────────────────────────

def create_phase_issue(
    repo: str,
    phase: str,
    title: str,
    body: str,
    project_info: Optional[dict] = None,
) -> dict:
    """
    Create a labeled issue for a phase and add it to the project board.
    Returns {number, node_id, item_id}.
    """
    label = PHASE_LABEL.get(phase, "sdlc:requirement")
    cmd = ["issue", "create", "--repo", repo, "--title", title, "--body", body,
           "--label", label]
    try:
        r = _gh(*cmd)
        url = r.stdout.strip()
        match = re.search(r"/issues/(\d+)", url)
        issue_number = int(match.group(1)) if match else 0
    except subprocess.CalledProcessError:
        return {}

    result: dict = {"number": issue_number, "node_id": "", "item_id": ""}

    if issue_number and project_info and project_info.get("node_id"):
        node_id = _get_issue_node_id(repo, issue_number)
        result["node_id"] = node_id
        if node_id:
            item_id = add_to_project(project_info["node_id"], node_id)
            result["item_id"] = item_id
            # Start in Backlog
            move_phase_issue(project_info, item_id, "Backlog")

    return result


def create_epic(repo: str, project_name: str, body: str) -> Optional[int]:
    """Create the Epic issue. Returns issue number."""
    try:
        r = _gh("issue", "create", "--repo", repo,
                "--title", f"[EPIC] {project_name}",
                "--body", body, "--label", "sdlc:requirement")
        match = re.search(r"/issues/(\d+)", r.stdout)
        return int(match.group(1)) if match else None
    except subprocess.CalledProcessError:
        return None


def create_child_issue(repo: str, title: str, body: str,
                       parent_issue: Optional[int] = None) -> Optional[int]:
    """Create a child issue. Returns issue number."""
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


# ── Story and task issues from plan.md ───────────────────────────────────────

def parse_plan_stories(plan_text: str) -> list[dict]:
    """
    Parse plan.md into story dicts: {id, title, task_ids}.
    Expects: # STORY-NNN: Title  with ## TASK-NNN: ... entries beneath each story.
    """
    stories = []
    current: Optional[dict] = None
    for line in plan_text.splitlines():
        story_m = re.match(r"^#\s+(STORY-\d+):\s+(.+)", line)
        task_m = re.match(r"^##\s+(TASK-\d+):", line)
        if story_m:
            if current:
                stories.append(current)
            current = {"id": story_m.group(1), "title": story_m.group(2).strip(), "task_ids": []}
        elif task_m and current:
            current["task_ids"].append(task_m.group(1))
    if current:
        stories.append(current)
    return stories


def create_story_issues(
    repo: str,
    stories: list[dict],
    project_info: Optional[dict] = None,
    epic_issue: Optional[int] = None,
) -> dict:
    """
    Create a GitHub issue per story and add to the project board.
    Returns {STORY-001: {number, item_id}, ...}
    """
    result = {}
    for story in stories:
        task_list = "\n".join(f"- [ ] {tid}" for tid in story["task_ids"]) or "(no tasks)"
        body = f"## Tasks\n{task_list}"
        if epic_issue:
            body += f"\n\nPart of #{epic_issue}"

        info = create_phase_issue(
            repo=repo,
            phase="implementation",
            title=f"{story['id']}: {story['title']}",
            body=body,
            project_info=project_info,
        )
        if info.get("number"):
            result[story["id"]] = {"number": info["number"], "item_id": info.get("item_id", "")}
    return result


def parse_plan_tasks(plan_text: str) -> list[dict]:
    """
    Parse plan.md into a list of task dicts: {id, title, description, size}.
    Expects markdown headers like: ## TASK-001: Title
    """
    tasks = []
    current: Optional[dict] = None
    for line in plan_text.splitlines():
        m = re.match(r"^##\s+(TASK-\d+):\s+(.+)", line)
        if m:
            if current:
                tasks.append(current)
            current = {"id": m.group(1), "title": m.group(2).strip(),
                       "description": "", "size": "M"}
        elif current:
            size_m = re.match(r"-\s*Size:\s*(\w+)", line)
            if size_m:
                current["size"] = size_m.group(1)
            else:
                current["description"] += line + "\n"
    if current:
        tasks.append(current)
    return tasks


def create_task_issues(
    repo: str,
    tasks: list[dict],
    project_info: Optional[dict] = None,
    epic_issue: Optional[int] = None,
) -> dict:
    """
    Create a GitHub issue for each task and add to the project board.
    Returns {TASK-001: {number, item_id}, ...}
    """
    result = {}
    for task in tasks:
        body = task["description"].strip()
        if epic_issue:
            body += f"\n\nPart of #{epic_issue}"
        body += f"\n\n**Size:** {task['size']}"

        info = create_phase_issue(
            repo=repo,
            phase="implementation",
            title=f"{task['id']}: {task['title']}",
            body=body,
            project_info=project_info,
        )
        if info.get("number"):
            result[task["id"]] = {"number": info["number"], "item_id": info.get("item_id", "")}
    return result


# ── Pull Requests ─────────────────────────────────────────────────────────────

def create_pr(
    repo: str,
    phase: str,
    branch: str,
    body: str,
    base: str = "main",
    closes_issue: Optional[int] = None,
) -> Optional[str]:
    """Create a PR for a phase. Returns PR URL or None."""
    if closes_issue:
        body += f"\n\nCloses #{closes_issue}"
    try:
        r = _gh(
            "pr", "create",
            "--repo", repo,
            "--title", f"sdlc({phase}): {phase} phase",
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
        if data.get("reviewDecision") == "APPROVED":
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
    """Fetch all comments and review bodies from the PR for this branch."""
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
