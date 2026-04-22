# sdlc-setup

You are setting up a new project for autonomous SDLC orchestration. This is a
one-time, interactive skill. Your job is to understand the project deeply, ask
smart clarifying questions, and produce a complete `.sdlc/spec.yaml`, an
initial `requirements.md`, and initial `test_cases.md` coverage — so that `/sdlc-orchestrate` can run unattended
from here.

---

## Step 1 — Understand the codebase (if it exists)

Check whether this directory has existing source files (anything beyond `.git`,
`.sdlc`, `.claude`, `CLAUDE.md`, `README.md`).

**If code already exists:** invoke the `sdlc-analyze-repo` skill to build a
grounding picture of the stack, architecture, and existing conventions. Read
the resulting `.sdlc/memory/project.md`.

**If no code yet:** skip to Step 2.

---

## Step 1b — Check for existing spec

Read the project spec (spec lives at `.sdlc/spec.yaml` in this worktree):
```bash
cat .sdlc/spec.yaml
```

If fields like `description`, `tech_stack`, or `repo` are already filled in, use them as defaults and only ask for what's missing. Never re-ask for information that's already complete.

> **Worktree model:** each `worktree/<project>` directory is one SDLC project. If you are in the main repo, stop and ask the developer to run `/sdlc-start` or move into the generated worktree first.
> ```bash
> test -f .sdlc/spec.yaml || echo "Not inside an SDLC worktree"
> ```

---

## Step 2 — Interview the developer

Use `AskUserQuestion` to gather the information below. Do NOT ask all questions
at once in a single wall of text. Ask them conversationally — two or three at
a time — and follow up when an answer is ambiguous or reveals something worth
exploring.

Minimum information to collect:

| Field | Question |
|---|---|
| `project_name` | What is this project called? |
| `description` | In one sentence, what problem does it solve? |
| `users` | Who are the primary users? |
| `goals` | What are the 2–3 top goals for this work? |
| `non_goals` | Anything explicitly out of scope? |
| `tech_stack` | What stack / language / framework? (or confirm what you detected) |
| `repo` | GitHub repo as `owner/repo` (or blank to skip) |
| `slack_channel` | Slack webhook URL for gate notifications (or blank to skip) |

Drive the follow-ups with Claude intelligence: if the user says "build a REST
API", ask what the consumers are, what auth model, whether OpenAPI spec is
needed. Don't follow a rigid script.

---

## Step 3 — Write spec.yaml

Write the spec to this worktree:
```bash
# Spec path: .sdlc/spec.yaml
```

Write `.sdlc/spec.yaml` with the collected answers:

```yaml
project_name: <name>
description: <one-sentence description>
users: <who uses it>
goals:
  - <goal 1>
  - <goal 2>
non_goals:
  - <if any>
tech_stack: <stack>
repo: <owner/repo or blank>
slack_webhook: ""  # set SLACK_WEBHOOK env var instead of hardcoding here
executor: claude-code
```

---

## Step 4 — GitHub setup (if repo configured)

If `repo` was provided in spec.yaml, run:
```bash
sdlc github setup
```

This creates labels, the project board, workflow automations, and story issues for the
requirement, design, and planning phases. It is idempotent — safe to re-run.

---

## Step 5 — Draft requirements

Invoke the `sdlc-requirement` skill. It will read `spec.yaml`, generate
clarifying questions in `.sdlc/workflow/artifacts/requirement_questions.md`,
and produce initial `requirements.md` and `test_cases.md`.

Wait for it to finish, then check that `requirements.md` and `test_cases.md` exist and are
non-empty.

---

## Step 6 — Set state and hand off

Run:
```bash
sdlc state set requirement_ready_for_approval --force
```

Then tell the developer:

```
✓ Requirements drafted at .sdlc/workflow/artifacts/requirements.md
✓ Initial test cases drafted at .sdlc/workflow/artifacts/test_cases.md

Review those files. Edit them if anything is wrong or missing.

When you're ready to start autonomous mode, run:

  /loop 10m /sdlc-orchestrate

Claude will drive the rest of the SDLC. You'll get a Slack ping at each
approval gate. To approve a gate: sdlc state approve
```
