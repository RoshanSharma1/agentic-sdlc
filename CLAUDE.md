# Claude Code Project Context: Chorus AIDLC

This file provides foundational context and engineering standards for the Chorus AIDLC orchestrator.

## 🚀 System Overview

**Chorus AIDLC** is an AI agent extension that turns any supported AI coding agent into an autonomous software development assistant. It drives the full SDLC — requirements, design, planning, implementation, testing, and documentation — with human approval gates at each critical milestone.

**Your Role:** As the primary executor, you drive phase-specific workflows via skills like `/sdlc-requirement`, `/sdlc-design`, `/sdlc-implement`, etc.

## 🏗 Architecture

### Core Components

```
sdlc_orchestrator/
├── state_machine.py        # Hierarchical state machine (Phase → Status → Story)
├── backend.py              # OrchestratorRuntime (event-driven executor)
├── agent_registry.py       # Multi-agent fallback system
├── agent_status_checker.py # Agent exhaustion/credit monitoring
├── memory.py               # 3-layer memory (global/project/state)
├── cli.py                  # CLI entry point
├── commands/               # CLI command implementations
├── integrations/           # GitHub, webhooks
├── ui/react-app/           # Chorus dashboard (Vite/React)
└── templates/              # spec.yaml, skill templates
```

### State Machine Hierarchy

```
Phase (requirement → design → planning → implementation → testing → documentation → done)
  └── Status (pending → in_progress → awaiting_approval → done)
      └── Stories (single-artifact phases: 1 story; implementation: N stories)
            └── Story
                  ├── status (pending → in_progress → awaiting_review → done)
                  ├── github_issue  (optional)
                  ├── github_pr     (optional)
                  └── tasks  (implementation stories only)
```

**Status:** ✅ State machine verified and production-ready (verified 2026-04-26)

## 🤖 Multi-Agent Support

**Supported Agents:** claude-code (you), kiro, gemini, codex

**Priority Order (default):**
1. claude-code (priority 1)
2. kiro (priority 2)
3. gemini (priority 3)
4. codex (priority 4)

**Fallback System:**
- When an agent hits credit/quota limits, the registry automatically tries the next available agent
- Usage tracked via `AgentRegistry` (API calls, tokens, costs, exhaustion)
- Enable/disable fallback in `spec.yaml`: `agent_fallback: true`

## 🛠 Key Workflows

### Phase Execution Pattern

Each phase skill follows this pattern:

1. **Load context** — Read spec.yaml, project memory, feedback from previous runs
2. **Execute phase work** — Generate requirements, design docs, implementation plans, code, tests, or docs
3. **Create artifacts** — Write markdown files to `.sdlc/workflow/artifacts/`
4. **Create PR** — Branch (`sdlc-<project>-<phase>`), commit, push, open PR
5. **Transition state** — Mark phase as `awaiting_approval`
6. **Wait for approval** — Human reviews PR on GitHub, approves → webhook triggers next phase

### Memory System (3 Layers)

**Layer 1 — Global (`~/.sdlc/global.md`)**
- Machine-wide coding values, DoD, review rules
- Shared across all projects

**Layer 2 — Project (`.sdlc/memory/project.md`)**
- Stack, architecture, domain language, constraints
- Project-specific context

**Layer 3 — State (`.sdlc/state.json`)**
- Execution memory: phase, status, tasks, decisions
- Auto-managed by state machine

**Context File:** This CLAUDE.md is auto-regenerated from global + project memory + spec.yaml via `MemoryManager.regenerate_claude_md()`.

## 📋 Coding Standards

### Python

- **Type hints:** Use `from __future__ import annotations` for forward references
- **Dataclasses:** Prefer dataclasses over dicts for structured data
- **Persistence:** Use `BackendStore` (SQLite) for runtime data, JSON for state snapshots
- **Error handling:** Let exceptions bubble to runtime; use `Optional[T]` for nullable returns
- **CLI:** Use Click for commands (see `sdlc_orchestrator/commands/`)

### React/TypeScript

- **Framework:** Vite + React + TypeScript
- **Styling:** CSS modules (`.css` files co-located with components)
- **State:** React hooks (useState, useEffect), no external state library
- **API:** Fetch via `services/api.ts` → Python backend (port 7842 or 8765)
- **Build:** `npm run build` → production bundle in `dist/`

### Git Workflow

- **Branch naming:** `sdlc-<project>-<phase>` or `sdlc-<project>-<story-id>`
- **Commit messages:** Conventional commits (`feat:`, `fix:`, `refactor:`, `docs:`)
- **PR automation:** Orchestrator creates PRs automatically; you review on GitHub
- **No force-push:** Respect existing commits; append new commits for fixes

## 🧪 Testing & Validation

**Backend Tests:**
```bash
pytest tests/                    # Run all tests
pytest tests/test_state_machine.py  # State machine tests
```

**UI Development:**
```bash
cd sdlc_orchestrator/ui/react-app
npm install
npm run dev                     # Dev server on port 3000
```

**Dashboard:** `sdlc ui` (production) or `npm run dev` (development)

## 🔌 Integration Points

### GitHub Webhooks

- **Port:** 8765 (run via `sdlc webhook`)
- **Events:** PR reviews, approvals → triggers `APPROVAL_RECEIVED` event
- **Setup:** See `WEBHOOK-SETUP.md` for ngrok/GitHub configuration

### Chorus Dashboard

- **Port:** 7842 (default)
- **Purpose:** Visual status, artifact viewer, agent registry, workflow history
- **Tech:** React app served by Python backend (`sdlc ui`)

## 🎯 Phase-Specific Guidelines

### Requirement Phase (`/sdlc-requirement`)

- Interview user about product goals, features, constraints
- Generate structured requirements document
- Output: `requirements.md` artifact
- Branch: `sdlc-<project>-requirements`

### Design Phase (`/sdlc-design`)

- Read requirements artifact
- Create architecture, component diagrams, data models
- Output: `design.md` artifact
- Branch: `sdlc-<project>-design`

### Planning Phase (`/sdlc-plan`)

- Break down design into implementation stories
- Estimate complexity, identify dependencies
- Output: `plan.md` with story list
- Branch: `sdlc-<project>-plan`

### Implementation Phase (`/sdlc-implement`)

- Execute stories one at a time (state machine tracks current story)
- Each story → separate branch + PR (`sdlc-<project>-STORY-NNN`)
- Write code, handle edge cases, follow existing patterns
- Approval → next story OR if all done → transition to testing

### Testing Phase (`/sdlc-testing`)

- Write comprehensive tests (unit, integration, E2E as appropriate)
- Capture screenshots/evidence for UI features
- Output: Test files + `testing_evidence.md`
- Branch: `sdlc-<project>-testing`

### Documentation Phase (`/sdlc-docs`)

- Generate user-facing docs, API references, setup guides
- Update README with new features
- Output: Documentation files
- Branch: `sdlc-<project>-docs`

## 💡 Best Practices

### When You're the Executor

1. **Read context first** — Always load spec.yaml, project memory, and prior phase artifacts
2. **Check state** — Use `WorkflowState(project_dir)` to see current phase/status/story
3. **Single phase focus** — Don't jump ahead; complete the current phase artifact
4. **PR per phase** — Each phase = one PR (except implementation: one PR per story)
5. **Feedback loop** — If a phase fails approval, read feedback from `.sdlc/feedback/<phase>.md`

### When Another Agent Is Executor

- You may be invoked interactively for troubleshooting or manual tasks
- Use `sdlc agent list` to see active executor and fallback order
- Use `sdlc status` to see current workflow state
- Respect the state machine — don't manually modify `.sdlc/state.json`

## 🚨 What NOT to Do

- **Don't bypass state machine:** Always use `WorkflowState` methods, never edit `state.json` directly
- **Don't skip approvals:** Each phase must go through `awaiting_approval` → human review → transition
- **Don't delete .sdlc/:** This directory contains all workflow state and artifacts
- **Don't create files outside project:** Write artifacts to `.sdlc/workflow/artifacts/`, not `/tmp` or elsewhere
- **Don't re-introduce removed patterns:** The codebase has been refactored; avoid legacy patterns (e.g., manual JSONL parsing for agents)

## 📚 Key Files to Know

| File | Purpose |
|------|---------|
| `spec.yaml` | Project configuration (name, stack, executor, approvals) |
| `state.json` | Current workflow state (phase, status, stories, tasks) |
| `.sdlc/memory/project.md` | Project-specific context (stack, architecture, constraints) |
| `~/.sdlc/global.md` | Machine-wide coding standards and DoD |
| `.sdlc/workflow/artifacts/` | Phase outputs (requirements.md, design.md, plan.md, etc.) |
| `.sdlc/feedback/<phase>.md` | Human feedback after phase rejection |
| `agent_registry.json` | Agent usage stats, credits, fallback history |

## 🔄 Workflow State Transitions

```python
# Common state machine operations
wf = WorkflowState(project_dir)

# Start a phase
wf.transition_to(Phase.REQUIREMENT, Status.IN_PROGRESS)

# Submit for approval
wf.submit_for_approval()  # → Status.AWAITING_APPROVAL

# Human approves
wf.approve()  # → Status.DONE, auto-advances to next phase

# Human rejects with feedback
# (manual: append to .sdlc/feedback/requirement.md, reset phase to in_progress)
```

## 🎓 Learning Resources

- **Architecture verification:** `docs/chorus-aidlc-verification.md` (complete state machine spec)
- **Webhook setup:** `WEBHOOK-SETUP.md` (GitHub integration)
- **Agent registry:** `AGENT_REGISTRY_QUICKSTART.md` (multi-agent fallback)
- **CLI help:** `sdlc --help` (all commands)

---

**Current Project:** agentic-sdlc (this orchestrator itself)
**Current Phase:** requirement (in_progress)
**Executor:** claude-code (you)
**Dashboard:** http://localhost:7842 (run `sdlc ui`)
