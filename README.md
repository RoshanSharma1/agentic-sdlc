# SDLC Orchestrator

An **AI agent extension** that turns any supported AI coding agent into an autonomous software development assistant. It drives the full SDLC — requirements, design, planning, implementation, testing, and documentation — with human approval gates at each critical milestone.

> **What kind of extension is this?**
> SDLC Orchestrator extends your AI coding agent with three components:
> - **Skills** — phase-specific commands (e.g. `/sdlc-start`, `/sdlc-requirement`) that give the agent new behaviors
> - **Hooks** — event-driven automation that reacts to what the agent does (e.g. detecting test failures)
> - **CLI** (`sdlc`) — a state management layer plus Python runtime that tracks workflow progress and dispatches phase agents

> **Supported agents:** Claude Code, Codex, Kiro, Gemini
> Set `executor` in `spec.yaml` to one of: `claude-code`, `codex`, `kiro`, `gemini`

---

## Architecture: Chorus AIDLC

The orchestrator implements a **hierarchical state machine** (Chorus: Agentic SDLC) that manages the full development lifecycle:

```
Phase (requirement → design → planning → implementation → testing → documentation → done)
  └── Status (pending → in_progress → awaiting_approval → done)
      └── Stories (single-artifact phases: 1 story; implementation: N stories)
            └── Story
                  ├── status (pending → in_progress → awaiting_review → done)
                  ├── github_issue  (optional)
                  ├── github_pr     (optional)
                  └── tasks  (implementation stories only: TASK-NNN → status)
```

**Key Components:**
- **State Machine** (`WorkflowState`) — Manages phase/status/story transitions with full history
- **Runtime** (`OrchestratorRuntime`) — Event-driven executor that spawns agents in headless mode
- **Backend Store** (SQLite + JSON) — Dual persistence for state, runs, jobs, and events
- **Agent Registry** — Multi-agent support with automatic fallback

See [docs/chorus-aidlc-verification.md](docs/chorus-aidlc-verification.md) for the complete technical architecture.

**Status:** ✅ State machine verified and production-ready (verified 2026-04-26)

---

## How It Works

The orchestrator uses an **event-driven runtime** that coordinates between you, GitHub, and autonomous agents:

1. **You run `/sdlc-start`** — Interviews you, writes `spec.yaml`, initializes the state machine
2. **Python runtime dispatches agents** — Spawns phase agents in headless mode (e.g., `claude --yes /sdlc-requirement`)
3. **Agent completes phase** — Transitions state to `awaiting_approval`, opens GitHub PR
4. **You approve PR on GitHub** — Webhook triggers `APPROVAL_RECEIVED` event
5. **Runtime auto-dispatches next phase** — Event handler spawns the next agent
6. **Repeat until done** — Linear progression through all phases

Each phase produces a GitHub PR on a namespaced branch (`sdlc-<project>-<phase>`). You review on GitHub, leave comments as feedback, and approve the PR. The runtime detects the approval via webhook (or polling) and automatically continues — **no terminal commands needed after the initial `/sdlc-start`**.

---

## Runtime Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                      User / GitHub                           │
└────────────────┬──────────────────────────┬─────────────────┘
                 │                          │
           (PR approval)              (CLI commands)
                 │                          │
                 v                          v
┌────────────────────────────────────────────────────────────┐
│                  OrchestratorRuntime                        │
│  ┌──────────┐  ┌──────────────┐  ┌────────────────────┐   │
│  │ EventBus │  │ WorkflowState│  │ AgentRegistry      │   │
│  │ (pub/sub)│◄─┤ (state       │─►│ (multi-agent       │   │
│  │          │  │  machine)    │  │  dispatch)         │   │
│  └─────┬────┘  └──────┬───────┘  └──────┬─────────────┘   │
│        │              │                  │                 │
│        └──────────────┴──────────────────┘                 │
│                       │                                    │
│                       v                                    │
│        ┌──────────────────────────────────┐               │
│        │  BackendStore (SQLite)           │               │
│        │  • projects  • jobs  • events    │               │
│        └──────────────────────────────────┘               │
└───────────────────────┬──────────────────┬────────────────┘
                        │                  │
                        v                  v
               ┌────────────────┐  ┌───────────────────┐
               │ state.json     │  │ Agent Process     │
               │ (.sdlc/)       │  │ (headless mode)   │
               └────────────────┘  └───────────────────┘
```

**Components:**
- **EventBus** — Publishes events: `APPROVAL_RECEIVED`, `AGENT_RUN_FINISHED`, `JOB_QUEUED`, etc.
- **WorkflowState** — State machine operations: `transition_to()`, `approve()`, `submit_for_approval()`
- **AgentRegistry** — Tracks available agents, selects with fallback (spec.yaml `executor` → `job_agents` → all available)
- **BackendStore** — SQLite persistence for projects, agent runs, jobs, approval events, workflow history
- **Runtime** — Spawns agents via `spawn_for_project()`, waits for exit code, publishes `AGENT_RUN_FINISHED`

**Event Flow Example:**
```
GitHub webhook: PR approved
  ↓
EventBus publishes APPROVAL_RECEIVED
  ↓
Default handler calls runtime.spawn_for_project(phase="design")
  ↓
AgentRegistry selects agent (codex → claude-code → kiro → gemini)
  ↓
Runtime spawns: `claude --yes /sdlc-design`
  ↓
Agent runs in background thread
  ↓
Exit code 0 → EventBus publishes AGENT_RUN_FINISHED
  ↓
WorkflowState transitions to next phase or awaiting_approval
```

---

## Workflow Example

```
You                          Agent (autonomous)
────                         ──────────────────
sdlc init                →   Scaffolds .sdlc/ directory
/sdlc-start              →   Interviews you, writes spec.yaml
                             Creates worktree/<project> base branch
                             Drafts requirements
                             Opens PR: sdlc-<project>-requirements → worktree/<project>
⏸  Approve PR            →   Produces design.md
                             Opens PR: sdlc-<project>-design → worktree/<project>
⏸  Approve PR            →   Produces plan.md (stories + tasks)
                             Opens PR: sdlc-<project>-plan → worktree/<project>
⏸  Approve PR            →   Implements story by story, opens PR per story
⏸  Approve each story PR →   Moves to next story
                             When all stories done: executes testing phase, fixes issues, writes test results
                             Opens PR: sdlc-<project>-testing → worktree/<project>
⏸  Approve PR            →   Writes docs + archives artifacts
                             Opens PR: sdlc-<project>-docs → worktree/<project>
⏸  Approve PR            →   Opens final PR: worktree/<project> → main
⏸  Merge when ready      →   Ships to main
```

---

## Requirements

- Python 3.11+
- A supported AI coding agent (see above)
- Git
- `gh` CLI (optional, for GitHub integration)
- Node.js + npm (for Docusaurus documentation build, if `apps/docs` exists)

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

`/sdlc-start` handles everything in one shot:
1. Asks for a project name (or picks up an existing one)
2. Creates a `worktree/<project>` base branch — all phase branches fork from here, `main` is never touched during development
3. Interviews you (project name, description, approval preferences)
4. Writes `spec.yaml` and launches the first orchestration tick

Pass `--no-approvals` to let the agent advance through all phases without waiting for PR approval:
```
/sdlc-start --no-approvals
```

**Agent-specific open commands:**

| Agent | How to open | Skills path |
|-------|-------------|-------------|
| **Claude Code** | `claude` in terminal | `~/.claude/commands/` |
| **Codex** | `codex` in terminal | `~/.codex/commands/` |
| **Kiro** | `kiro-cli chat` in terminal | `~/.kiro/skills/` + `~/.kiro/agents/` |
| **Gemini** | `gemini` in terminal | `~/.gemini/commands/` |

---

### Step 3 — Start the pipeline

After `/sdlc-start` completes, start the pipeline from the dashboard or API:

```bash
curl -X POST http://localhost:8765/api/projects/<project>/start-pipeline
```

The Python orchestrator reads workflow state and spawns only the phase agent it needs.

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

### Step 6 — Setup Real-Time GitHub Webhooks (Recommended)

For fully autonomous operation, configure GitHub webhooks to trigger the orchestrator in real-time when PRs are approved.

**Quick Setup:**

```bash
# 1. Run the 24/7 webhook setup
./scripts/setup-webhook-24x7.sh

# 2. Start ngrok tunnel
./scripts/start-ngrok.sh

# 3. Configure GitHub webhook (see WEBHOOK-SETUP.md)
#    URL: https://<ngrok-url>/webhook
#    Secret: (provided by setup script)
#    Events: Pull requests, Pull request reviews
```

See [WEBHOOK-SETUP.md](WEBHOOK-SETUP.md) for complete setup guide.

**Manual Webhook Server:**

```bash
# Start webhook server
sdlc webhook --port 8765 --secret your-webhook-secret

# In another terminal, expose via ngrok
ngrok http 8765

# Configure GitHub webhook:
#    URL: https://<ngrok-url>/webhook
#    Secret: your-webhook-secret
#    Events: Pull requests, Pull request reviews
```

---

### Step 7 — Review story PRs

Implementation is broken into stories. For each story, the agent opens a PR on a `sdlc-<project>-story-NNN` branch. Review it normally in GitHub.

When you approve the PR, the agent marks the story complete and moves to the next one. When all stories are done it automatically enters the testing phase.

### Step 8 — Testing phase

After all stories are approved the agent:

1. Reads the requirement-derived `test-cases.md` checklist
2. Runs the full automated validation pass and exercises each testcase
3. Fixes defects found during testing on a dedicated `sdlc-<project>-testing` branch
4. Writes `docs/sdlc/<project>/test-results.md` with per-test evidence and blockers
5. Opens a PR: `sdlc-<project>-testing → worktree/<project>`

Approve the PR and the agent will merge the testing fixes into the base branch before documentation starts.

---

### Step 9 — Documentation phase

After testing is approved the agent:

1. Writes product and technical documentation into `apps/docs/docs/<project>/` (Docusaurus)
2. Runs `npm run build` in `apps/docs` — build must pass before the PR is opened
3. Archives all phase artifacts (`requirements.md`, `test-cases.md`, `design.md`, `plan.md`, `test-results.md`) to `docs/sdlc/<project>/` so they land on `main` after merge
4. Opens a PR: `sdlc-<project>-docs → worktree/<project>`

Approve the PR, then the agent opens the final `worktree/<project> → main` PR for you to merge when ready to ship.

---

## Bypassing Approval Gates

To skip PR approval for all phases (fully autonomous run):

```bash
sdlc state no-approvals   # set all phase_approvals to false
sdlc state approvals      # restore all to true
```

Or set per-phase in `spec.yaml`:

```yaml
phase_approvals:
  requirement: true
  design: true
  planning: false       # agent advances automatically
  implementation: false
  testing: false
  documentation: true
```

---

## Workflow Scenarios

### New greenfield project

```bash
sdlc init
cd my-project
# open your agent
/sdlc-start
```

### Attaching to an existing codebase

```bash
sdlc init .
# open your agent
/sdlc-start
```

### Resuming after interruption

The orchestrator resumes safely from the last committed artifact — it never re-does completed work.

Restart the pipeline for the active project from the dashboard or `POST /api/projects/<project>/start-pipeline`.

### Switching agents mid-project

All workflow state lives in `.sdlc/` — not in any agent's memory.

```bash
# 1. Change executor in .sdlc/spec.yaml
executor: kiro

# 2. Re-run init to install skills for the new agent
sdlc init .

# 3. Start the pipeline again for the project
curl -X POST http://localhost:8765/api/projects/<project>/start-pipeline
```

---

## Project Configuration

`.sdlc/spec.yaml`, generated by `/sdlc-start`:

```yaml
project_name: My App
tech_stack: Node.js
repo: owner/repo
slack_webhook: ""
description: What the project does
executor: kiro               # claude-code | codex | kiro | gemini

phase_approvals:             # Set to false to let the agent advance without waiting for PR approval
  requirement: true
  design: true
  planning: true
  implementation: true
  testing: true
  documentation: true
```

---

## State Machine

The orchestrator uses a **hierarchical state machine** with 3 levels:

### Core States

**Phases (7):**
- `requirement` → `design` → `planning` → `implementation` → `testing` → `documentation` → `done`

**Statuses (5):**
- `pending` — Not yet started
- `in_progress` — Agent working (executable state)
- `awaiting_approval` — PR open, waiting for human review (approval gate)
- `blocked` — Manual intervention needed
- `done` — Phase complete

**Story Statuses (5):**
- `pending` → `in_progress` → `awaiting_review` → `feedback` → `done`

### State Combinations

| Phase | Status | Type | Description |
|-------|--------|------|-------------|
| `requirement` | `in_progress` | Auto | Drafting requirements |
| `requirement` | `awaiting_approval` | **Human gate** | Requirements PR open |
| `design` | `in_progress` | Auto | Designing system architecture |
| `design` | `awaiting_approval` | **Human gate** | Design PR open |
| `planning` | `in_progress` | Auto | Breaking design into stories and tasks |
| `planning` | `awaiting_approval` | **Human gate** | Plan PR open |
| `implementation` | `in_progress` | Auto | Implementing stories (story-level tracking) |
| `implementation` | `awaiting_approval` | **Human gate** | Story PR open |
| `testing` | `in_progress` | Auto | Running validation pass and fixing defects |
| `testing` | `awaiting_approval` | **Human gate** | Testing PR open |
| `documentation` | `in_progress` | Auto | Writing docs + archiving artifacts |
| `documentation` | `awaiting_approval` | **Human gate** | Docs PR open |
| `*` | `blocked` | **Human gate** | Needs manual intervention |
| `done` | `done` | Terminal | Complete — final PR opened |

**Note:** Legacy flat-state strings (e.g., `requirement_in_progress`, `task_plan_ready`) are supported for backward compatibility but the system now uses the hierarchical `phase:status` model internally.

### State Persistence

All state is stored in:
- **Primary:** `.sdlc/workflow/state.json` (git-ignored, local state file)
- **Backend:** `~/.sdlc/backend.sqlite3` (SQLite database for dashboard + events)

View current state:
```bash
sdlc status              # Human-friendly display
sdlc state get           # Machine-readable output
sdlc state history       # Transition log
```

---

## Branch Structure

```
main                                    ← never touched during development
└── worktree/<project>                  ← base branch; all phase branches fork from here
    ├── sdlc-<project>-requirements     → PR → worktree/<project>
    ├── sdlc-<project>-design           → PR → worktree/<project>
    ├── sdlc-<project>-plan             → PR → worktree/<project>
    ├── sdlc-<project>-story-001        → PR → worktree/<project>
    ├── sdlc-<project>-story-002        → PR → worktree/<project>
    ├── sdlc-<project>-testing          → PR → worktree/<project>
    └── sdlc-<project>-docs             → PR → worktree/<project>

When all phases are done:
  Agent opens PR: worktree/<project> → main
  You review and merge when ready to ship.
```

---

## Directory Structure

```
my-project/
├── apps/
│   └── docs/                         # Docusaurus site
│       └── docs/<project>/           # Generated by documentation phase
│           ├── overview.md
│           ├── getting-started.md
│           ├── architecture.md
│           ├── api.md
│           └── changelog.md
├── docs/
│   └── sdlc/
│       ├── <project>-requirements.md # Working artifact (on phase branch)
│       ├── <project>-test-cases.md
│       ├── <project>-design.md
│       ├── <project>-plan.md
│       └── <project>/                # Archived to main after docs phase
│           ├── requirements.md
│           ├── test-cases.md
│           ├── design.md
│           ├── plan.md
│           ├── test-results.md
│           └── README.md
├── .sdlc/                            # Orchestration state (gitignored)
│   ├── spec.yaml
│   ├── active                        # Current project name
│   ├── memory/
│   │   ├── global.md                 # Org-wide engineering rules
│   │   └── project.md                # Project-specific context
│   └── workflow/
│       ├── state.json
│       └── feedback/                 # Ingested PR review comments per phase
├── AGENT.md                          # Generated agent context (gitignored)
└── .agent/
    └── settings.json                 # Hooks configuration (gitignored)
```

---

## Integrations

### Slack
Set `slack_webhook` in `spec.yaml`. The agent sends a notification at every approval gate with a summary and PR link.

### GitHub

```bash
sdlc github setup
```

Creates labels, a Projects v2 board, workflow automations, and one issue per SDLC phase. Story issues are created when the plan is approved.

**Labels:**

| Label | Phase |
|-------|-------|
| `sdlc:requirement` | Requirements |
| `sdlc:design` | Design |
| `sdlc:plan` | Task planning |
| `sdlc:implementation` | Implementation & stories |
| `sdlc:documentation` | Documentation |
| `awaiting-review` | Any gate |
| `blocked` | Blocked state |

---

## Skills Reference

| Skill | Who uses it | When |
|-------|-------------|------|
| `/sdlc-start` | **You** | Once — kicks off everything |
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

**Project Setup:**
| Command | Description |
|---------|-------------|
| `sdlc init [source]` | Scaffold a project (new, GitHub repo, or local path) |

**Monitoring:**
| Command | Description |
|---------|-------------|
| `sdlc status` | Show current workflow state and history (human-friendly) |
| `sdlc webhook [--port N] [--secret S]` | Webhook receiver for real-time GitHub events |

**State Management:**
| Command | Description |
|---------|-------------|
| `sdlc state get` | Print current state (machine-readable: `phase:status`) |
| `sdlc state approve` | Advance past approval gate manually (fallback when no GitHub) |
| `sdlc state no-approvals` | Disable all phase approval gates — agent advances automatically |
| `sdlc state approvals` | Re-enable all phase approval gates |
| `sdlc state set <value>` | Manually set phase/status (supports `requirement`, `in_progress`, legacy strings) |
| `sdlc state history` | View state transition history with timestamps |

### Agent-facing commands (called during orchestration)

**Skills (invoked as `/sdlc-*` by agents):**
| Skill | When Used |
|-------|-----------|
| `/sdlc-start` | User-initiated: interview + setup + first phase |
| `/sdlc-requirement` | Runtime dispatches for requirement phase |
| `/sdlc-design` | Runtime dispatches for design phase |
| `/sdlc-plan` | Runtime dispatches for planning phase |
| `/sdlc-implement` | Runtime dispatches for implementation (per story) |
| `/sdlc-validate` | Runtime dispatches for testing phase |
| `/sdlc-review` | Runtime dispatches for documentation phase |
| `/sdlc-feedback` | Apply PR review feedback |
| `/sdlc-cleanup-worktree` | Clean up git worktrees |

**Utilities (called from within skills):**
| Command | Description |
|---------|-------------|
| `sdlc artifact read <name>` | Read a phase artifact (requirements, design, plan, etc.) |
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

## Technical Documentation

For developers and technical readers:

- **[State Machine Verification](docs/chorus-aidlc-verification.md)** — Complete technical architecture of the Chorus AIDLC state machine, including:
  - Hierarchical state model (Phase → Status → Stories → Tasks)
  - `WorkflowState` class internals
  - `OrchestratorRuntime` event-driven execution
  - Backend persistence (SQLite + JSON)
  - Multi-agent dispatch logic
  - Test coverage and verification checklist

- **[Agent Registry Quickstart](AGENT_REGISTRY_QUICKSTART.md)** — How to configure multi-agent support and fallback

---

## License

MIT
