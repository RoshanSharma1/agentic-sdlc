# Agent Registry Quickstart

`agentic-sdlc` is not supposed to depend on one agent staying healthy forever. The goal is autonomous SDLC execution, even when one provider runs out of credits, hits a quota wall, or has a subscription issue.

This document captures both:
- the current implementation that already exists in the repo
- the target product behavior we want next, especially in the UI

## Product Goal

Treat Claude, Codex, Kiro, and other coding agents as one execution pool.

The registry should:
- know which agents are configured for a project
- track subscription and credit availability
- automatically switch to another agent when the current one is blocked
- preserve SDLC progress without asking the user to manually rerun commands
- expose all of this in the dashboard, including the ability to start a project from the UI

## What Exists Today

These pieces are already implemented:

- `sdlc_orchestrator/agent_registry.py` stores project-level agent state in `.sdlc/agent_registry.json`
- `trigger_agent()` in `sdlc_orchestrator/commands/init.py` can use registry fallback automatically
- credit failures are detected reactively from agent output
- CLI management exists through `sdlc agent ...`
- the dashboard exists through `sdlc ui`
- the dashboard can monitor projects and start the orchestration loop for an existing worktree

Current limitation:

- credit/subscription awareness is reactive, not proactive
- the dashboard does not yet have an agent registry view
- the dashboard does not yet have a first-class "Start Project" flow
- project setup still depends on CLI plus `/sdlc-start`

## Current Behavior

When `agent_fallback` is enabled, the runtime does this:

1. Try the preferred executor from `.sdlc/spec.yaml`
2. If that agent fails with a credit or quota-style error, mark it unavailable
3. Try the next available agent by priority
4. Persist the result in `.sdlc/agent_registry.json`

Detected credit-style failures currently include patterns like:
- `credit exhausted`
- `quota exceeded`
- `insufficient credits`
- `subscription expired`
- `payment required`
- `429 too many requests`

This already solves the immediate blocker: you should not need to manually switch agents just because one provider is out of credits.

## Current Quickstart

Until the UI start flow exists, the working path is:

```bash
sdlc init
```

Then open your agent in the repo and run:

```text
/sdlc-start
```

Enable fallback in `.sdlc/spec.yaml`:

```yaml
executor: claude-code
agent_fallback: true
```

Inspect and manage the registry:

```bash
sdlc agent list
sdlc agent status
sdlc agent reset --all
sdlc agent add codex --priority 3
sdlc agent disable kiro
```

Open the dashboard:

```bash
sdlc ui
```

Today, the dashboard is useful for monitoring projects and starting the orchestration loop after a project already exists. It is not yet the place where a project is created from scratch.

## Target Autonomous Flow

This is the behavior we want `agentic-sdlc` to grow into:

1. Open the dashboard
2. Click `Start Project`
3. Enter project name, repo/path, description, approval mode, and preferred agent order
4. UI creates the worktree, writes `.sdlc/spec.yaml`, initializes `.sdlc/agent_registry.json`, and starts the orchestration loop
5. Registry continuously tracks agent health, subscription state, and recent credit failures
6. If the active agent is blocked, orchestration switches to the next healthy agent automatically
7. UI shows which agent is active, which ones are exhausted, when they can be retried, and why a switch happened

## UI Capabilities We Want

The dashboard should gain three agent-facing surfaces.

### 1. Start Project

A simple project launcher that can:
- create or pick a project slug
- create the worktree
- collect project description and approval settings
- choose preferred executor and fallback order
- start the first orchestration run

### 2. Agent Registry Panel

A project-level agent panel that shows:
- agent name
- provider
- priority
- status: `available`, `no_credits`, `cooldown`, `error`, `disabled`
- last used time
- last error
- optional reset time or next retry time

### 3. Fallback Timeline

A lightweight activity feed:
- `claude-code failed: quota exceeded`
- `registry switched to codex`
- `codex completed planning`
- `kiro re-enabled after reset window`

## Recommended Next Implementation

If we build this in slices, the clean sequence is:

1. Extend registry schema
   Add fields like `provider`, `reset_at`, `cooldown_until`, `health_reason`, `last_credit_error`, and `supports_headless`.

2. Improve routing policy
   Let selection consider both static priority and runtime state, not just simple fallback order.

3. Add registry APIs to the dashboard server
   Expose endpoints for listing agents, updating status, resetting agents, and viewing switch history.

4. Add `Start Project` UI flow
   Move the current `sdlc init` plus `/sdlc-start` bootstrap into a dashboard form backed by server endpoints.

5. Add proactive subscription awareness
   Start with manual reset windows, then optionally integrate provider APIs or webhooks where available.

## Definition Of Done

This feature is complete when:

- a user can start a project from the dashboard without dropping to the terminal
- orchestration continues automatically when one agent runs out of credits
- the registry makes switching decisions visible in the UI
- the user can see which agents are healthy and which are exhausted
- the system no longer depends on the user remembering which provider still has credits left

## Summary

The short version is:

- `agentic-sdlc` already has the beginnings of multi-agent fallback
- the current blocker is that fallback is still mostly CLI-driven and reactive
- the next step is to turn the registry into a real control plane
- the UI should become the place where projects start and where agent availability is visible

That is the path from "multi-agent support" to actual autonomous SDLC execution.
