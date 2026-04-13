# SDLC Orchestrator

A **Claude Code extension** that turns Claude into an autonomous software development agent. It drives the full SDLC — requirements, design, planning, implementation, testing, and review — with human approval gates at each critical milestone.

> **What kind of extension is this?**
> SDLC Orchestrator is a **Claude Code extension**. It bundles three things that extend Claude Code's capabilities:
> - **Skills** — custom slash commands (e.g. `/sdlc-orchestrate`) that give Claude new behaviors
> - **Hooks** — event-driven automation that reacts to what Claude does (e.g. detecting test failures)
> - **CLI** (`sdlc`) — a state management layer Claude calls to track workflow progress

---

## How It Works

You install the extension, point it at a project, and run `/sdlc-setup` once inside Claude Code. After that, a single command — `/loop 10m /sdlc-orchestrate` — hands Claude the wheel.

Each phase produces a GitHub PR. The artifact (requirements, design, task plan) lives on a dedicated branch and is readable from anywhere. You review on GitHub, leave comments as feedback, and approve the PR. Claude detects the approval on the next tick and continues — no terminal commands needed.

```
You                          Claude (autonomous)
────                         ───────────────────
sdlc init                →   Scaffolds .sdlc/ directory
/sdlc-setup              →   Interviews you, writes spec.yaml
/loop 10m /sdlc-orchestrate
                         →   Drafts clarifying questions
                             Opens PR: sdlc/requirements
⏸  Review + approve PR   →   Reads your answers from PR comments
                             Builds requirements.md, updates PR
⏸  Approve PR            →   Produces design.md
                             Opens PR: sdlc/design
⏸  Approve PR            →   Produces plan.md
                             Opens PR: sdlc/plan
⏸  Approve PR            →   Implements, tests, commits
                             Opens PR: sdlc/implementation
⏸  Review + approve PR   →   Incorporates feedback, marks done
```

---

## Requirements

- Python 3.11+
- [Claude Code](https://claude.ai/code)
- Git
- `gh` CLI (optional, for GitHub integration)

---

## Installation

```bash
git clone https://github.com/RoshanSharma1/agentic-sdlc.git
cd sdlc-orchestrator
pip install -e .
```

This registers the `sdlc` CLI command and installs all skills into Claude Code's skill path automatically.

---

## Usage Guide

### Step 1 — Initialize your project

Choose the source that matches your situation:

```bash
# Brand new project (interactive prompts)
sdlc init

# Existing local directory
sdlc init /path/to/project
sdlc init .

# Clone and attach a GitHub repo
sdlc init owner/repo
sdlc init https://github.com/owner/repo.git
```

`init` creates a `.sdlc/` directory inside your project with state tracking, memory files, and artifact storage. It also writes a `CLAUDE.md` (Claude's context file) and registers the test-failure hook in `.claude/settings.json`.

---

### Step 2 — Open Claude Code and run setup

```bash
cd my-project
claude
```

Inside Claude Code, run the setup skill:

```
/sdlc-setup
```

Claude will:
1. Analyze your codebase (if one exists) to understand the stack and conventions
2. Interview you with 2–3 questions at a time about goals, users, constraints, and tech choices
3. Write `.sdlc/spec.yaml` with your answers
4. Draft an initial `requirements.md` and set the state to `requirement_ready_for_approval`

At the end, Claude will tell you to review `requirements.md` and start autonomous mode when ready.

If GitHub is configured, also run (in terminal):

```bash
sdlc github setup
```

This creates labels, the Projects v2 board, workflow automations, and one issue per SDLC phase — all in one command.

---

### Step 3 — Start autonomous orchestration

```
/loop 10m /sdlc-orchestrate
```

This runs `/sdlc-orchestrate` every 10 minutes. Each tick, Claude:
- Checks the current state
- Executes the phase (or picks up where it left off)
- Commits all changes
- Advances state (Slack notification fires automatically at gates)
- Releases the tick lock and stops — the next tick continues

You can also run it manually without the loop:
```
/sdlc-orchestrate
```

---

### Step 4 — Handle approval gates via GitHub PRs

At each gate, Claude opens a PR and prints a message like:

```
⏸  Requirements complete. Waiting for PR approval.

What was produced:
  • 10 clarifying questions for you to answer
  • docs/sdlc/requirements.md on branch sdlc/requirements

Review PR: https://github.com/owner/repo/pull/3

To approve: review and approve the PR on GitHub.
  I'll automatically detect approval on the next tick and continue.
```

If Slack is configured, you'll also get a webhook notification with the PR link.

**To answer requirement questions:**
Open the PR on GitHub. Either:
- Edit `docs/sdlc/requirements.md` directly on the branch and fill in each `**Answer:**` field, or
- Leave review comments on the PR

Then **approve the PR**. Claude picks up your answers from PR comments on the next tick — no terminal command needed.

**To provide design or plan feedback:**
Leave review comments on the relevant PR (`sdlc/design` or `sdlc/plan`). Claude ingests them when it detects PR approval and applies the feedback before advancing state.

**No GitHub configured?** Fall back to the terminal:
```bash
sdlc state approve
```

---

### Step 5 — Monitor progress

```bash
sdlc status
```

Shows the current state, recent transitions, and a summary of completed artifacts:

```
Project:  my-app
State:    implementation_in_progress
Phase:    4 / 6

History:
  ✓ draft_requirement           2 min ago
  ✓ awaiting_requirement_answer 1 hr ago  (human gate)
  ✓ requirement_in_progress     45 min ago
  ✓ requirement_ready_for_approval  30 min ago  (human gate — approved)
  ✓ design_in_progress          20 min ago
  ✓ awaiting_design_approval    10 min ago  (human gate — approved)
  ✓ task_plan_in_progress       5 min ago
  → implementation_in_progress  (active)
```

---

### Step 6 — Keep Claude on standby (optional but recommended)

By default Claude only runs when you trigger it (via `/loop` or `/sdlc-orchestrate`). To make it resume automatically the moment you approve a PR, use one of two modes:

**Polling mode — `sdlc watch` (no infrastructure needed)**

Run this in a separate terminal alongside Claude Code:

```bash
sdlc watch
```

It polls GitHub every 30 seconds. The moment a `sdlc/<phase>` PR is approved or merged, it triggers `claude -p /sdlc-orchestrate` automatically — you never need to touch the terminal again.

```bash
sdlc watch --interval 60   # poll every 60s instead
```

**Webhook mode — real-time, zero polling**

For instant response, run the built-in webhook server and point GitHub at it:

```bash
# 1. Expose a public URL (example using ngrok)
ngrok http 8080

# 2. Start the receiver
sdlc webhook --port 8080 --secret your-webhook-secret

# 3. In GitHub repo settings → Webhooks, add:
#    URL: https://<ngrok-url>/webhook
#    Content type: application/json
#    Secret: your-webhook-secret
#    Events: Pull request reviews, Pull requests
```

When GitHub fires a PR approved or merged event for any `sdlc/*` branch, the receiver triggers Claude immediately.

---

### Step 7 — Review the implementation PR

When implementation and testing are done, Claude pushes the `sdlc/implementation` branch and opens a PR. Review it normally in GitHub — leave comments on specific lines or as general review comments.

When you approve the PR, Claude detects it on the next tick, ingests your comments as feedback, and either marks the project done or loops back to the appropriate phase (design, plan, or implementation) depending on what needs to change.

---

## Workflow Scenarios

### New greenfield project

```bash
sdlc init                         # Interactive: name, stack, GitHub, Slack
cd my-project
claude
/sdlc-setup                       # Claude interviews you
# Review .sdlc/workflow/artifacts/requirements.md
/loop 10m /sdlc-orchestrate       # Hand Claude the wheel
```

### Attaching to an existing codebase

```bash
sdlc init .                       # Run from inside the repo
claude
/sdlc-setup                       # Claude analyzes existing code first, then interviews you
/loop 10m /sdlc-orchestrate
```

### Running a single phase manually

Skip the loop and run one phase at a time:

```
/sdlc-requirement     # Just requirements
/sdlc-design          # Just design
/sdlc-plan            # Just planning
/sdlc-implement       # Just implementation
/sdlc-validate        # Just testing
/sdlc-review          # Just review
```

Useful when you want tighter control or are picking up mid-workflow.

### Resuming after interruption

The orchestrator is designed to resume safely. If Claude is interrupted mid-phase, the next tick picks up from the last committed artifact — it never re-does completed work. Completed tasks in `plan.md` are marked `[x] done` and skipped.

Just re-run:
```
/sdlc-orchestrate
```
or wait for the next `/loop` tick.

---

## Project Configuration

Each project is configured via `.sdlc/spec.yaml`, generated by `/sdlc-setup`:

```yaml
project_name: My App
tech_stack: Node.js
repo: owner/repo                 # GitHub repo (optional)
slack_webhook: https://...       # Slack webhook for gate notifications (optional)
description: What the project does
executor: claude-code

phases:                          # Which phases to run
  - requirement
  - design
  - planning
  - implementation
  - testing
  - review

phase_approvals:                 # Which gates require human approval
  requirement: true
  design: true
  planning: false                # Claude advances automatically
  implementation: false
  testing: true
  review: false
```

Set `phase_approvals.<phase>: false` for phases you trust Claude to advance through without review.

---

## Customizing Claude's Behavior

### Global engineering rules (`global.md`)

`.sdlc/memory/global.md` defines organization-wide standards that apply to every project — code quality rules, testing requirements, security policies, commit message format, documentation standards. Edit this file to encode your team's conventions. It is merged into `CLAUDE.md` on every project.

### Project context (`project.md`)

`.sdlc/memory/project.md` documents the project-specific context: stack details, architecture decisions, domain terminology, folder conventions, deployment targets, known constraints. Claude reads this before every phase. Keep it up to date as the project evolves.

---

## State Machine

The orchestrator manages 14 states across the workflow:

| State | Type | Description |
|-------|------|-------------|
| `draft_requirement` | Auto | Generate clarifying questions |
| `awaiting_requirement_answer` | **Human gate** | Wait for your answers |
| `requirement_in_progress` | Auto | Build structured requirements |
| `requirement_ready_for_approval` | **Human gate** | Review requirements |
| `design_in_progress` | Auto | Create system architecture |
| `awaiting_design_approval` | **Human gate** | Review design |
| `task_plan_in_progress` | Auto | Break design into tasks |
| `task_plan_ready` | **Human gate** | Review task plan |
| `implementation_in_progress` | Auto | Implement features |
| `test_failure_loop` | Auto | Fix failing tests (max 3 retries) |
| `awaiting_review` | **Human gate** | Wait for PR review |
| `feedback_incorporation` | Auto | Apply review feedback |
| `blocked` | **Human gate** | Needs manual intervention |
| `done` | Terminal | Complete |

---

## Directory Structure

```
my-project/
├── docs/sdlc/                    # Phase artifacts — committed, visible on GitHub
│   ├── requirements.md           # on branch: sdlc/requirements
│   ├── design.md                 # on branch: sdlc/design
│   ├── plan.md                   # on branch: sdlc/plan
│   └── test_report.md            # on branch: sdlc/implementation
├── .sdlc/                        # Orchestration state (gitignored)
│   ├── spec.yaml                 # Project specification
│   ├── memory/
│   │   ├── global.md             # Organization-wide engineering rules
│   │   └── project.md            # Project-specific context
│   ├── workflow/
│   │   ├── state.json            # Current state and history
│   │   └── feedback/             # Ingested PR review comments per phase
├── CLAUDE.md                     # Generated context for Claude (gitignored)
└── .claude/
    └── settings.json             # Hooks configuration (gitignored)
```

Each phase's artifact lives on its own branch and is accessible via the GitHub PR.
A symlink is also created at `~/.sdlc/projects/<slug>` pointing to `.sdlc/`.

---

## Integrations

### Slack
Set `slack_webhook` in `spec.yaml`. Claude automatically sends a notification whenever it reaches an approval gate, including a summary of what was produced and the exact command to approve.

### GitHub

With `gh` CLI authenticated and `repo` set in `spec.yaml`, run once after `sdlc init`:

```bash
sdlc github setup
```

This creates:

**Labels** — colour-coded per phase:

| Label | Phase |
|-------|-------|
| `sdlc:requirement` | Requirements |
| `sdlc:design` | Design |
| `sdlc:plan` | Task planning |
| `sdlc:implementation` | Implementation & tasks |
| `sdlc:testing` | Testing |
| `sdlc:review` | Code review |
| `awaiting-review` | Any gate |
| `blocked` | Blocked state |

**GitHub Projects v2 board** with a Status field:

```
Backlog → In Progress → Awaiting Review → Blocked → Done
```

**Workflow automations** (enabled automatically):
- Item closed → Done
- PR merged → Done
- Item reopened → In Progress

**Phase issues** — one issue per SDLC phase added to Backlog at setup. As Claude advances through the workflow, each issue moves across the board automatically via `sdlc github sync-board`.

**Task issues** — when the task plan is approved, Claude parses `docs/sdlc/plan.md` and creates one GitHub issue per `TASK-NNN`, each labelled `sdlc:implementation` and added to the board.

**PR → issue linking** — every phase PR includes `Closes #N` so merging the PR automatically closes the phase issue and triggers the workflow automation to move it to Done.

The result:

```
GitHub Projects Board
├── #1  [sdlc:requirement] Requirements        Done ✓
├── #2  [sdlc:design]      System Design       Done ✓
├── #3  [sdlc:plan]        Task Plan           Done ✓
├── #4  [sdlc:impl]        TASK-001: Auth      In Progress
├── #5  [sdlc:impl]        TASK-002: API       Backlog
├── #6  [sdlc:impl]        TASK-003: UI        Backlog
└── #7  [sdlc:review]      Code Review         Backlog
```

---

## Skills Reference

| Skill | When to use |
|-------|-------------|
| `/sdlc-setup` | Once per project — initial interview and spec generation |
| `/sdlc-orchestrate` | Every tick — the main autonomous loop (use with `/loop`) |
| `/sdlc-requirement` | Standalone requirement gathering |
| `/sdlc-design` | Standalone system design |
| `/sdlc-plan` | Standalone task planning |
| `/sdlc-implement` | Standalone implementation |
| `/sdlc-validate` | Standalone testing and validation |
| `/sdlc-review` | Standalone code review |
| `/sdlc-analyze-repo` | Analyze an existing codebase before setup |
| `/sdlc-feedback` | Apply feedback from review |

---

## CLI Reference

### Human-facing commands

| Command | Description |
|---------|-------------|
| `sdlc init [source]` | Scaffold a project (new, GitHub repo, or local path) |
| `sdlc status` | Show current workflow state and history |
| `sdlc watch [--interval N]` | Poll GitHub PRs and trigger Claude on approval |
| `sdlc webhook [--port N] [--secret S]` | Webhook receiver for real-time GitHub events |
| `sdlc state approve` | Advance past a gate manually (fallback when no GitHub) |
| `sdlc relink [--all]` | Rebuild `~/.sdlc/projects/<slug>` symlink |

### Claude-facing commands (called during orchestration)

| Command | Description |
|---------|-------------|
| `sdlc state get` | Get current state (machine-readable) |
| `sdlc state set <state>` | Transition to a new state |
| `sdlc state history` | View state transition history |
| `sdlc artifact read <name>` | Read a phase artifact |
| `sdlc artifact list` | List available artifacts |
| `sdlc notify <phase> <event>` | Send a Slack notification |
| `sdlc github setup` | Labels + board + workflows + phase issues (run once) |
| `sdlc github sync-board` | Move active phase issue to correct board column |
| `sdlc github create-task-issues [plan_file]` | Create one issue per TASK-NNN in plan.md |
| `sdlc github pr-status <branch>` | Check if a phase PR is approved or merged |
| `sdlc github ingest-feedback <branch> <phase>` | Pull PR review comments as feedback |
| `sdlc github create-pr <branch> <phase>` | Open a PR for a phase branch |
| `sdlc github create-issue <title> <body-file>` | Create a GitHub issue |
| `sdlc tick acquire/release` | Prevent concurrent orchestration runs |

---

## License

MIT
