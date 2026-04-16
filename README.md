# SDLC Orchestrator

An **AI agent extension** that turns any supported AI coding agent into an autonomous software development assistant. It drives the full SDLC — requirements, design, planning, implementation, testing, and review — with human approval gates at each critical milestone.

> **What kind of extension is this?**
> SDLC Orchestrator extends your AI coding agent with three components:
> - **Skills** — custom slash commands (e.g. `/sdlc-orchestrate`) that give the agent new behaviors
> - **Hooks** — event-driven automation that reacts to what the agent does (e.g. detecting test failures)
> - **CLI** (`sdlc`) — a state management layer the agent calls to track workflow progress

> **Supported agents:** Claude Code, Codex, Kiro, Cline
> Set `executor` in `spec.yaml` to one of: `claude-code`, `codex`, `kiro`, `cline`

---

## How It Works

You install the extension, point it at a project, and run `/sdlc-start` once inside your agent. That's it — `/sdlc-start` handles setup, interviews you, and hands the agent the wheel.

Each phase produces a GitHub PR. The artifact (requirements, design, task plan) lives on a dedicated branch and is readable from anywhere. You review on GitHub, leave comments as feedback, and approve the PR. The agent detects the approval on the next tick and continues — no terminal commands needed.

```
You                          Agent (autonomous)
────                         ──────────────────
sdlc init                →   Scaffolds .sdlc/ directory
/sdlc-start              →   Interviews you, writes spec.yaml
                             Drafts requirements
                             Opens PR: sdlc/requirements
⏸  Approve PR            →   Produces design.md
                             Opens PR: sdlc/design
⏸  Approve PR            →   Produces plan.md (stories + tasks)
                             Opens PR: sdlc/plan
⏸  Approve PR            →   Implements story by story, opens PR per story
⏸  Approve each story PR →   Moves to next story or marks done
```

---

## Requirements

- Python 3.11+
- A supported AI coding agent (see above)
- Git
- `gh` CLI (optional, for GitHub integration)

---

## Installation

```bash
git clone https://github.com/RoshanSharma1/agentic-sdlc.git
cd agentic-sdlc
pip install -e .
```

This registers the `sdlc` CLI command and installs all skills into your agent's skill path automatically.

---

## Usage Guide

### Step 1 — Initialize your project

```bash
# Brand new project
sdlc init

# Existing local directory
sdlc init .

# Clone a GitHub repo
sdlc init owner/repo
```

`init` scaffolds `.sdlc/`, installs skills into your agent, and writes the agent context file.

---

### Step 2 — Open your agent and run `/sdlc-start`

Open your agent in the project directory and run:

```
/sdlc-start
```

That's it. `/sdlc-start` handles everything in one shot:
1. Detects if setup is needed — interviews you, writes `spec.yaml`, drafts requirements
2. Launches the first orchestration tick
3. Prints the loop command to keep it running

**Agent-specific open commands:**

| Agent | How to open | Skills path |
|-------|-------------|-------------|
| **Claude Code** | `claude` in terminal | `~/.claude/commands/` |
| **Codex** | `codex` in terminal | `~/.codex/commands/` |
| **Kiro** | `kiro-cli chat` in terminal | `~/.kiro/skills/` + `~/.kiro/agents/` |
| **Cline** | Open VS Code with Cline | `~/.cline/commands/` |

---

### Step 3 — Run the loop

After `/sdlc-start` completes, paste the loop command it gives you into a dedicated terminal tab:

```bash
# Claude Code
while true; do claude -p "/sdlc-orchestrate"; sleep 600; done

# Codex
while true; do codex exec --full-auto "/sdlc-orchestrate"; sleep 600; done

# Kiro
while true; do kiro-cli chat --agent sdlc-orchestrate --no-interactive start; sleep 600; done
```

Each iteration is a fresh agent process — no context bleed between ticks. Leave it running and walk away.

---

### Step 4 — Approve gates via GitHub PRs

At each gate the agent opens a PR and pauses:

```
⏸  Requirements complete. Waiting for PR approval.

Review PR: https://github.com/owner/repo/pull/3

Approve the PR on GitHub — I'll detect it on the next tick and continue.
```

Leave review comments for feedback. The agent ingests them on approval.

**No GitHub?**
```bash
sdlc state approve
```

---

### Step 5 — Monitor progress

```bash
sdlc status
```

Shows the current state, approval status, branch, and recent history.

---

### Step 6 — Keep the agent on standby (optional but recommended)

By default the agent only runs when you trigger it. To make it resume automatically the moment you approve a PR, use one of two modes:

**Polling mode — `sdlc watch` (no infrastructure needed)**

Run this in a separate terminal:

```bash
sdlc watch
```

It polls GitHub every 30 seconds. The moment a `sdlc/<phase>` PR is approved or merged, it triggers the agent automatically.

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

When GitHub fires a PR approved or merged event for any `sdlc/*` branch, the receiver triggers the agent immediately.

---

### Step 7 — Review story PRs

Implementation is broken into stories. For each story, the agent opens a PR on a `sdlc/story-NNN` branch. Review it normally in GitHub — leave comments on specific lines or as general review comments.

When you approve the PR, the agent detects it on the next tick, marks the story complete, and moves to the next story (or marks the project done when all stories are finished).

---

## Workflow Scenarios

### New greenfield project

```bash
sdlc init                         # Interactive: name, stack, GitHub, Slack
cd my-project
# open your agent
/sdlc-setup                       # Agent interviews you
/sdlc-start                       # Hand the agent the wheel
```

### Attaching to an existing codebase

```bash
sdlc init .                       # Run from inside the repo
# open your agent
/sdlc-setup                       # Agent analyzes existing code first, then interviews you
/sdlc-start
```

### Running a single phase manually

Skip the orchestrator and run one phase at a time:

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

The orchestrator is designed to resume safely. If the agent is interrupted mid-phase, the next tick picks up from the last committed artifact — it never re-does completed work. Completed stories are tracked and skipped.

Just re-run:
```
/sdlc-orchestrate
```

### Switching agents mid-project

All workflow state lives in `.sdlc/` — not in any agent's memory. Any supported agent can pick up from the exact point another left off.

```bash
# 1. Change executor in .sdlc/spec.yaml
executor: kiro   # was: claude-code

# 2. Re-run init to install skills and write the correct context file
sdlc init .

# 3. Open the new agent — it reads the same state.json and continues
/sdlc-orchestrate
```

---

## Project Configuration

Each project is configured via `.sdlc/spec.yaml`, generated by `/sdlc-setup`:

```yaml
project_name: My App
tech_stack: Node.js
repo: owner/repo                 # GitHub repo (optional)
slack_webhook: https://...       # Slack webhook for gate notifications (optional)
description: What the project does
executor: claude-code            # AI agent: claude-code | codex | kiro | cline

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
  planning: false                # Agent advances automatically
  implementation: false
  testing: true
  review: false
```

Set `phase_approvals.<phase>: false` for phases you trust the agent to advance through without review.

---

## Customizing Agent Behavior

### Global engineering rules (`global.md`)

`.sdlc/memory/global.md` defines organization-wide standards that apply to every project — code quality rules, testing requirements, security policies, commit message format, documentation standards. Edit this file to encode your team's conventions. It is merged into the agent's context file on every project.

### Project context (`project.md`)

`.sdlc/memory/project.md` documents the project-specific context: stack details, architecture decisions, domain terminology, folder conventions, deployment targets, known constraints. The agent reads this before every phase. Keep it up to date as the project evolves.

---

## State Machine

The orchestrator manages 11 states across the workflow:

| State | Type | Description |
|-------|------|-------------|
| `requirement_in_progress` | Auto | Drafting requirements |
| `requirement_ready_for_approval` | **Human gate** | Requirements PR open — awaiting approval |
| `design_in_progress` | Auto | Designing system architecture |
| `awaiting_design_approval` | **Human gate** | Design ready — awaiting approval |
| `task_plan_in_progress` | Auto | Breaking design into stories and tasks |
| `task_plan_ready` | **Human gate** | Task plan ready — awaiting approval |
| `story_in_progress` | Auto | Implementing a story |
| `story_awaiting_review` | **Human gate** | Story PR open — awaiting approval |
| `feedback_incorporation` | Auto | Incorporating review feedback |
| `blocked` | **Human gate** | Needs manual intervention |
| `done` | Terminal | Complete |

---

## Directory Structure

```
my-project/
├── docs/sdlc/                    # Phase artifacts — committed, visible on GitHub
│   ├── requirements.md           # on branch: sdlc/requirements
│   ├── design.md                 # on branch: sdlc/design
│   └── plan.md                   # on branch: sdlc/plan
├── .sdlc/                        # Orchestration state (gitignored)
│   ├── spec.yaml                 # Project specification
│   ├── memory/
│   │   ├── global.md             # Organization-wide engineering rules
│   │   └── project.md            # Project-specific context
│   ├── workflow/
│   │   ├── state.json            # Current state and history
│   │   └── feedback/             # Ingested PR review comments per phase
├── AGENT.md                      # Generated context for the agent (gitignored)
│                                 # (CLAUDE.md for Claude Code, AGENTS.md for Codex)
└── .agent/
    └── settings.json             # Hooks configuration (gitignored)
```

Each phase's artifact lives on its own branch and is accessible via the GitHub PR.
A symlink is also created at `~/.sdlc/projects/<slug>` pointing to `.sdlc/`.

---

## Integrations

### Slack
Set `slack_webhook` in `spec.yaml`. The agent automatically sends a notification whenever it reaches an approval gate, including a summary of what was produced and the PR link.

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
| `sdlc:implementation` | Implementation & stories |
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

**Phase issues** — one issue per SDLC phase added to Backlog at setup. As the agent advances through the workflow, each issue moves across the board automatically via `sdlc github sync-board`.

**Story issues** — when the task plan is approved, the agent parses `docs/sdlc/plan.md` and creates one GitHub issue per `STORY-NNN`, each labelled `sdlc:implementation` and added to the board.

**PR → issue linking** — every phase PR includes `Closes #N` so merging the PR automatically closes the phase issue and triggers the workflow automation to move it to Done.

The result:

```
GitHub Projects Board
├── #1  [sdlc:requirement] Requirements          Done ✓
├── #2  [sdlc:design]      System Design         Done ✓
├── #3  [sdlc:plan]        Task Plan             Done ✓
├── #4  [sdlc:impl]        STORY-001: Auth       In Progress
├── #5  [sdlc:impl]        STORY-002: API        Backlog
├── #6  [sdlc:impl]        STORY-003: UI         Backlog
└── #7  [sdlc:review]      Code Review           Backlog
```

---

## Skills Reference

| Skill | Who uses it | When |
|-------|-------------|------|
| `/sdlc-start` | **You** | Once — kicks off everything |
| `/sdlc-orchestrate` | Loop / agent | Every tick automatically |
| `/sdlc-setup` | You (advanced) | Only to redo configuration |
| `/sdlc-requirement` | You (advanced) | Run a single phase manually |
| `/sdlc-design` | You (advanced) | Run a single phase manually |
| `/sdlc-plan` | You (advanced) | Run a single phase manually |
| `/sdlc-implement` | You (advanced) | Run a single phase manually |
| `/sdlc-validate` | You (advanced) | Run a single phase manually |
| `/sdlc-review` | You (advanced) | Run a single phase manually |
| `/sdlc-analyze-repo` | You (advanced) | Analyze codebase before setup |
| `/sdlc-feedback` | You (advanced) | Apply feedback manually |

---

## CLI Reference

### Human-facing commands

| Command | Description |
|---------|-------------|
| `sdlc init [source]` | Scaffold a project (new, GitHub repo, or local path) |
| `sdlc status` | Show current workflow state and history |
| `sdlc watch [--interval N]` | Poll GitHub PRs and trigger the agent on approval |
| `sdlc webhook [--port N] [--secret S]` | Webhook receiver for real-time GitHub events |
| `sdlc state approve` | Advance past a gate manually (fallback when no GitHub) |

### Agent-facing commands (called during orchestration)

| Command | Description |
|---------|-------------|
| `sdlc state get` | Get current state (machine-readable) |
| `sdlc state set <state>` | Transition to a new state |
| `sdlc state history` | View state transition history |
| `sdlc artifact read <name>` | Read a phase artifact |
| `sdlc artifact list` | List available artifacts |
| `sdlc story start <STORY-NNN>` | Set active story and begin implementation |
| `sdlc story complete` | Mark story approved, advance to next or done |
| `sdlc notify <phase> <event>` | Send a Slack notification |
| `sdlc github setup` | Labels + board + workflows + phase issues (run once) |
| `sdlc github sync-board` | Move active phase issue to correct board column |
| `sdlc github create-story-issues [plan_file]` | Create one issue per STORY-NNN in plan.md |
| `sdlc github create-task-issues [plan_file]` | Create one issue per task in plan.md |
| `sdlc github pr-status <branch>` | Check if a phase PR is approved or merged |
| `sdlc github ingest-feedback <branch> <phase>` | Pull PR review comments as feedback |
| `sdlc github create-pr <branch> <phase>` | Open a PR for a phase branch |
| `sdlc github create-issue <title> [body-file]` | Create a GitHub issue |
| `sdlc github close-phase-issue <phase>` | Close the GitHub issue for a phase |

---

## License

MIT
