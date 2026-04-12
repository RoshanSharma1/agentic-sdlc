# SDLC Orchestrator

A **Claude Code extension** that turns Claude into an autonomous software development agent. It drives the full SDLC — requirements, design, planning, implementation, testing, and review — with human approval gates at each critical milestone.

> **What kind of extension is this?**
> SDLC Orchestrator is a **Claude Code extension**. It bundles three things that extend Claude Code's capabilities:
> - **Skills** — custom slash commands (e.g. `/sdlc-orchestrate`) that give Claude new behaviors
> - **Hooks** — event-driven automation that reacts to what Claude does (e.g. detecting test failures)
> - **CLI** (`sdlc`) — a state management layer Claude calls to track workflow progress

---

## How It Works

You install the extension, point it at a project, and run `/sdlc-setup` once inside Claude Code. After that, a single command — `/loop 10m /sdlc-orchestrate` — hands Claude the wheel. Claude works through each SDLC phase autonomously, committing code as it goes. When it reaches a decision point that needs human judgement (scope approval, design sign-off, code review), it pauses, notifies you via Slack or the terminal, and waits for `sdlc state approve`.

```
You                          Claude (autonomous)
────                         ───────────────────
sdlc init                →   Scaffolds .sdlc/ directory
/sdlc-setup              →   Interviews you, writes spec.yaml + requirements.md
/loop 10m /sdlc-orchestrate
                         →   Drafts clarifying questions
⏸  sdlc answer          →   Builds structured requirements.md
⏸  sdlc state approve   →   Produces design.md
⏸  sdlc state approve   →   Produces plan.md
                         →   Implements, tests, commits
⏸  sdlc state approve   →   Opens PR, waits for review
                         →   Incorporates feedback, marks done
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

### Step 4 — Handle approval gates

At each gate, Claude pauses and prints a message like:

```
⏸  Requirements complete. Human review required.

What was produced:
  • requirements.md — 12 functional requirements with acceptance criteria
  • Non-functional: response time <200ms, 99.9% uptime

To review:
  .sdlc/workflow/artifacts/requirements.md

To approve and continue:
  sdlc state approve
  (then run /loop 10m /sdlc-orchestrate or /sdlc-orchestrate to resume)
```

If you have Slack configured, you'll also get a webhook notification with the same summary.

**To approve and let Claude continue:**
```bash
sdlc state approve
```
Then Claude picks up on the next loop tick (or manually trigger `/sdlc-orchestrate`).

**To answer requirement questions** (at the `awaiting_requirement_answer` gate):
```bash
# Opens your $EDITOR to fill in answers
sdlc answer

# Or provide a pre-written answers file
sdlc answer --file my-answers.md
```

**To provide feedback instead of approving:**

Edit the artifact directly (e.g. `requirements.md`) and add comments, then run:
```bash
sdlc answer --file feedback.md
```
Claude will incorporate the feedback before advancing.

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

### Step 6 — Review the PR

When implementation and testing are done, Claude opens a GitHub PR and enters the `awaiting_review` gate. Review the PR normally in GitHub. When you leave review comments, pull them into the feedback loop:

```bash
sdlc github ingest-feedback
sdlc state approve
```

Claude will apply the feedback (looping back to the appropriate phase if design or plan needs to change) and re-run until everything is clean.

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
├── .sdlc/                        # SDLC state (gitignored)
│   ├── spec.yaml                 # Project specification
│   ├── memory/
│   │   ├── global.md             # Organization-wide engineering rules
│   │   └── project.md            # Project-specific context
│   └── workflow/
│       ├── state.json            # Current state and history
│       ├── artifacts/
│       │   ├── requirement_questions.md
│       │   ├── requirements.md
│       │   ├── design.md
│       │   ├── plan.md
│       │   ├── test_report.md
│       │   └── review_summary.md
│       ├── logs/                 # Phase execution logs
│       └── feedback/             # PR review feedback per phase
├── CLAUDE.md                     # Generated context for Claude (gitignored)
└── .claude/
    └── settings.json             # Hooks configuration (gitignored)
```

A symlink is also created at `~/.sdlc/projects/<slug>` pointing to `.sdlc/` for multi-project support.

---

## Integrations

### Slack
Set `slack_webhook` in `spec.yaml`. Claude automatically sends a notification whenever it reaches an approval gate, including a summary of what was produced and the exact command to approve.

### GitHub
Authenticate `gh` CLI and set `repo` in `spec.yaml`. The orchestrator will:
- Create an epic issue for the project
- Create per-phase child issues
- Set up a GitHub project board
- Open a PR after implementation with phase metadata
- Pull PR review comments back as feedback files

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
| `sdlc answer [--file PATH]` | Submit answers to requirement questions |
| `sdlc state approve` | Advance past the current approval gate |
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
| `sdlc github create-pr` | Create a GitHub PR for the current phase |
| `sdlc github create-issue` | Create a GitHub issue |
| `sdlc tick acquire/release` | Prevent concurrent orchestration runs |

---

## License

MIT
