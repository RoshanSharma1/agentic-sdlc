# Kiro Project Context: Chorus AIDLC

This file provides foundational context and engineering standards for the Chorus AIDLC orchestrator.

## 🚀 System Overview

**Chorus AIDLC** is an AI agent extension that enables autonomous software development across the full SDLC — requirements, design, planning, implementation, testing, and documentation. You (Kiro) are one of the supported executors with priority 2 (fallback after claude-code).

**Your Role:** Execute phase-specific workflows via commands like `/sdlc-requirement`, `/sdlc-design`, `/sdlc-implement`, invoked as:
```bash
kiro-cli chat --agent sdlc-requirement --no-interactive start
```

## 🏗 Architecture

### Core Components

The orchestrator is Python-based (backend/CLI) with a React dashboard:

```
sdlc_orchestrator/
├── state_machine.py        # Hierarchical state machine
├── backend.py              # OrchestratorRuntime (event-driven)
├── agent_registry.py       # Multi-agent fallback system
├── memory.py               # 3-layer memory (global/project/state)
├── cli.py                  # CLI entry point
├── commands/               # CLI command modules
├── integrations/           # GitHub webhooks
└── ui/react-app/           # Chorus dashboard (Vite/React)
```

### State Machine

Workflow state follows a hierarchical structure:

```
Phase (requirement → design → planning → implementation → testing → docs → done)
  └── Status (pending → in_progress → awaiting_approval → done)
      └── Stories (single-artifact phases: 1 story; implementation: N stories)
            └── Story (status, github_issue, github_pr, tasks)
```

**Status:** ✅ State machine verified and production-ready (verified 2026-04-26)

## 🤖 Multi-Agent Support

**Supported Agents:** claude-code, kiro (you), gemini, codex

**Priority Order:**
1. claude-code (priority 1)
2. **kiro (you, priority 2)** ← You're the first fallback
3. gemini (priority 3)
4. codex (priority 4)

**Fallback Mechanism:**
- When claude-code hits credit/quota limits, the registry automatically selects you
- Your usage is tracked via `AgentRegistry` (API calls, tokens, costs)
- Fallback can be enabled/disabled in `spec.yaml`: `agent_fallback: true`

## 🛠 Executor Configuration

**Your CLI invocation pattern:**
```bash
kiro-cli chat --agent {skill} --no-interactive start
```

Skills are registered in `~/.kiro/skills/` as markdown files (e.g., `sdlc-requirement.md`, `sdlc-implement.md`).

**Key config (from memory.py):**
```python
EXECUTOR_CONFIG = {
    "kiro": ("AGENT.md", Path.home() / ".kiro" / "skills", Path(".kiro")),
}

EXECUTOR_CLI = {
    "kiro": ["kiro-cli", "chat", "--agent", "{skill}", "--no-interactive", "start"],
}
```

## 📋 Phase Execution Pattern

Each phase follows this workflow:

1. **Load context** — Read spec.yaml, project memory, artifacts from prior phases
2. **Execute phase work** — Generate requirements/design/code/tests/docs
3. **Create artifacts** — Write markdown/code to `.sdlc/workflow/artifacts/`
4. **Create PR** — Branch (`sdlc-<project>-<phase>`), commit, push, open GitHub PR
5. **Transition state** — Mark phase as `awaiting_approval`
6. **Wait for human** — User reviews PR on GitHub → webhook triggers next phase

## 🎯 Phase-Specific Workflows

### Requirement Discovery (`/sdlc-requirement`)

- Interview user about goals, features, constraints
- Generate structured requirements document
- Output: `requirements.md` artifact
- Branch: `sdlc-<project>-requirements`

### Design (`/sdlc-design`)

- Read requirements artifact
- Create architecture, component diagrams, data models
- Output: `design.md` artifact
- Branch: `sdlc-<project>-design`

### Planning (`/sdlc-plan`)

- Break down design into implementation stories
- Estimate complexity, identify dependencies
- Output: `plan.md` with story list
- Branch: `sdlc-<project>-plan`

### Implementation (`/sdlc-implement`)

- Execute current story (state machine tracks which one)
- Each story → separate branch + PR (`sdlc-<project>-STORY-NNN`)
- Write code following existing patterns
- After each story approval → next story OR transition to testing

### Testing (`/sdlc-testing`)

- Write comprehensive tests (unit, integration, E2E as appropriate)
- Capture screenshots/evidence for UI features
- Output: Test files + `testing_evidence.md`
- Branch: `sdlc-<project>-testing`

### Documentation (`/sdlc-docs`)

- Generate user-facing docs, API references, setup guides
- Update README with new features
- Output: Documentation files
- Branch: `sdlc-<project>-docs`

## 💾 Memory System (3 Layers)

**Layer 1 — Global (`~/.sdlc/global.md`)**
- Machine-wide coding values, DoD, review rules
- Shared across all projects

**Layer 2 — Project (`.sdlc/memory/project.md`)**
- Stack, architecture, domain language, constraints
- Project-specific context

**Layer 3 — State (`.sdlc/state.json`)**
- Execution memory: phase, status, tasks, decisions
- Auto-managed by state machine

**Context File:** This AGENT.md is auto-regenerated from global + project memory + spec.yaml.

## 📊 Coding Standards

### Python

- Type hints using `from __future__ import annotations`
- Dataclasses for structured data (avoid dicts where possible)
- Click for CLI commands
- SQLite (`BackendStore`) for runtime persistence
- JSON for state snapshots

### TypeScript/React

- Vite + React + TypeScript
- CSS modules for component styling
- Fetch API via `services/api.ts` → Python backend
- React hooks for state (no Redux/Zustand)

### Git Workflow

- **Branch naming:** `sdlc-<project>-<phase>` or `sdlc-<project>-<story-id>`
- **Commits:** Conventional format (`feat:`, `fix:`, `refactor:`, `docs:`)
- **PRs:** Created automatically by orchestrator
- **No force-push:** Append commits for fixes; respect existing history

## 🧪 Testing & Validation

**Backend:**
```bash
pytest tests/
pytest tests/test_state_machine.py
```

**Frontend:**
```bash
cd sdlc_orchestrator/ui/react-app
npm install
npm run dev    # Dev server on :3000
npm run build  # Production build
```

**Dashboard:**
```bash
sdlc ui  # Starts on port 7842
```

## 🔌 Integration Points

### GitHub Webhooks

- **Port:** 8765 (run via `sdlc webhook`)
- **Events:** PR reviews, approvals → triggers `APPROVAL_RECEIVED` event
- **Setup:** See `WEBHOOK-SETUP.md`

### Chorus Dashboard

- **Port:** 7842 (default)
- **Purpose:** Visual workflow status, artifact viewer, agent registry, history
- **Tech:** React app served by Python backend

## 💡 Best Practices

1. **Check state first** — Use `WorkflowState(project_dir)` to see current phase/status/story
2. **Single phase focus** — Complete one phase at a time; don't jump ahead
3. **Use state machine** — Call `WorkflowState` methods, never edit `state.json` manually
4. **Read feedback** — If phase fails approval, check `.sdlc/feedback/<phase>.md`
5. **Artifacts in .sdlc/** — Write outputs to `.sdlc/workflow/artifacts/`, not /tmp

## 🚨 What to Avoid

- **Don't bypass state machine:** Never edit `state.json` directly
- **Don't skip approval gates:** Each phase must go through `awaiting_approval` → human review
- **Don't delete .sdlc/:** Contains all workflow state and artifacts
- **Don't create files outside project:** Keep everything in the project tree
- **Don't re-introduce legacy patterns:** Avoid manual session parsing or debug scripts

## 📚 Key Files to Know

| File | Purpose |
|------|---------|
| `spec.yaml` | Project configuration (name, stack, executor, phase approvals) |
| `state.json` | Current workflow state (phase, status, stories, tasks) |
| `.sdlc/memory/project.md` | Project-specific context |
| `.sdlc/workflow/artifacts/` | Phase outputs (requirements.md, design.md, etc.) |
| `.sdlc/feedback/<phase>.md` | Human feedback after phase rejection |
| `agent_registry.json` | Agent usage stats, credits, fallback history |

## 🔄 State Transitions (Example)

```python
from sdlc_orchestrator.state_machine import WorkflowState, Phase, Status

wf = WorkflowState(project_dir)

# Start a phase
wf.transition_to(Phase.REQUIREMENT, Status.IN_PROGRESS)

# Submit for approval
wf.submit_for_approval()  # → Status.AWAITING_APPROVAL

# Human approves
wf.approve()  # → Status.DONE, auto-advances to next phase

# Human rejects (manual process: append feedback, reset to in_progress)
```

## 🎓 Learning Resources

- **Architecture verification:** `docs/chorus-aidlc-verification.md` (complete state machine spec)
- **Webhook setup:** `WEBHOOK-SETUP.md` (GitHub integration)
- **Agent registry:** `AGENT_REGISTRY_QUICKSTART.md` (multi-agent fallback)
- **CLI help:** `sdlc --help` (all commands)

---

**Current Project:** agentic-sdlc (this orchestrator itself)
**Your Priority:** 2 (first fallback after claude-code)
**Current Phase:** requirement (in_progress)
**Dashboard:** http://localhost:7842 (run `sdlc ui`)
