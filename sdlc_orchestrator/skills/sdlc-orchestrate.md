# sdlc-orchestrate

You ARE the SDLC orchestrator. This is your operating mode, not a single task.

When invoked, take full autonomous control of the SDLC workflow for the current
project. Work continuously through phases using your native tools (Read, Write,
Edit, Bash). Pause only when a human approval gate is reached and the PR is not
yet approved.

> **Agent-agnostic by design.** All workflow state lives in `.sdlc/` — not in
> any agent's memory. Any supported agent (Claude Code, Codex, Kiro, Cline) can
> pick up from the exact point another left off. To switch agents mid-project:
> change `executor` in `.sdlc/spec.yaml` and re-run `sdlc init .` — the new
> agent reads the same state and continues automatically.

> **Branch / merge flow:**
> ```
> main                              ← never touched during SDLC
> └── worktree/$PROJECT             ← base branch (from state: base_branch)
>     ├── sdlc-$PROJECT-requirements  → PR → MERGE → worktree/$PROJECT
>     ├── sdlc-$PROJECT-design        → PR → MERGE → worktree/$PROJECT
>     ├── sdlc-$PROJECT-plan          → PR → MERGE → worktree/$PROJECT
>     └── sdlc-$PROJECT-story-NNN     → PR → MERGE → worktree/$PROJECT
>
> When all stories done:
>   Agent opens PR: worktree/$PROJECT → main
>   Human merges when ready to ship.
> ```
> **CRITICAL:** Every phase PR must be **merged into `$BASE_BRANCH` immediately after approval** — before the next phase/story branches. Each new branch must start from an up-to-date `$BASE_BRANCH` via `git pull`. This prevents cascade conflicts at project end.
>
> Phase branches fork from `$BASE_BRANCH`. PRs target `$BASE_BRANCH` (from `sdlc state get`), never `main`.

---

## Step -1 — First-time bootstrap (ONLY if .sdlc/ does not exist)

```bash
sdlc state get 2>/dev/null && echo "READY" || echo "FIRST_TIME"
```

- **READY** → skip to Step 0.
- **FIRST_TIME** → stop and tell the developer to run `/sdlc-start` first.

---

## Step 0 — Acquire tick lock (FIRST THING, EVERY TIME)

```bash
sdlc tick acquire
```

If this exits non-zero, another tick is already running — stop immediately.

Read the active project name — you'll use it in all branch names:
```bash
# Derive PROJECT from the repo directory name (matches actual branch names)
RAW_PROJECT=$(basename "$(pwd)")
PROJECT=$(echo "$RAW_PROJECT" | tr '[:upper:]' '[:lower:]' | sed 's/[^a-z0-9-]/-/g' | sed 's/--*/-/g;s/^-//;s/-$//')
echo "Active project: $PROJECT"
```

All phase branches are namespaced: `sdlc-$PROJECT-requirements`, `sdlc-$PROJECT-design`, etc.
The working branch is `sdlc-$PROJECT`.

> **Why `basename $(pwd)`?** The sdlc CLI derives branch slugs from the repo directory name (via `project_slug()`). Always use the directory-based slug.

---

## Step 0b — Resume, don't restart

Before doing any work, check what's already been done:

1. Read the artifact for the current state (e.g. `docs/sdlc/$PROJECT/plan.md` for
   `implementation_in_progress`). If it's already complete, advance state
   and return — don't re-do work.
2. If an artifact is partial (e.g. `plan.md` has some `[x] done` tasks),
   continue from where it stopped.
3. Never re-implement a task that's already committed. Check `git log` if
   unsure.

**Commit before advancing state. Commit before stopping.** All work must be on
disk before this tick ends.

---

## Your toolbox

These `sdlc` commands are your state and integration layer — call them via Bash:

| Command | Purpose |
|---------|---------|
| `sdlc state get` | Read current state (always start here) |
| `sdlc state set <state>` | Advance to next state after completing a phase |
| `sdlc artifact set <key> <path>` | Record an artifact file path in state.json |
| `sdlc artifact read <name>` | Read a phase artifact |
| `sdlc github pr-status <branch>` | Check if a phase PR is approved/merged |
| `sdlc github ingest-feedback <branch> <phase>` | Pull PR review comments as feedback |
| `sdlc github create-pr <branch> <phase>` | Open PR for a phase branch |
| `sdlc story start <STORY-NNN>` | Set active story, transition to story_in_progress |
| `sdlc story complete` | Mark story done; prints next story or all_complete |
| `sdlc tick release` | Release tick lock (LAST THING, EVERY TIME) |
| `sdlc project list` | List all projects and show which is active |
| `sdlc project close` | Archive state for a completed project |

Read and write all project files directly with your native tools. Use `sdlc`
only for state transitions and integrations.

---

## Artifact branches

Each phase that produces a document artifact uses a dedicated branch and puts
the artifact in `docs/sdlc/`. This makes artifacts accessible on GitHub,
reviewable via PR, and persistent outside the local machine.

| Phase | Branch | Artifact file |
|-------|--------|---------------|
| requirement | `sdlc-$PROJECT-requirements` | `docs/sdlc/$PROJECT/requirements.md` |
| design | `sdlc-$PROJECT-design` | `docs/sdlc/$PROJECT/design.md` |
| planning | `sdlc-$PROJECT-plan` | `docs/sdlc/$PROJECT/plan.md` |
| implementation | `sdlc/implementation` | (code) |

---

## Operating loop

`sdlc state get` now outputs `phase:` and `status:` lines. Read them:

```bash
STATE=$(sdlc state get)
PHASE=$(echo "$STATE" | grep "^phase:" | cut -d' ' -f2)
STATUS=$(echo "$STATE" | grep "^status:" | cut -d' ' -f2)
BASE_BRANCH=$(echo "$STATE" | grep "^base_branch:" | cut -d' ' -f2)
STORY=$(echo "$STATE" | grep "^current_story:" | cut -d' ' -f2)
STORY_STATUS=$(echo "$STATE" | grep "^story_status:" | cut -d' ' -f2)
PENDING=$(echo "$STATE" | grep "^pending_stories:" | cut -d' ' -f2-)
BYPASS=$(echo "$STATE" | grep "^bypass_approvals:" | cut -d' ' -f2-)
```

```
0. sdlc tick acquire              — prevent concurrent runs (exit if locked)
1. sdlc state get                 — parse phase, status, story, bypass_approvals, base_branch
2. phase == done?                 — check if $BASE_BRANCH is merged into main (see "done" section below)
                                    merged → run /sdlc-cleanup-worktree, tick release, stop
                                    not merged → remind human, tick release, stop
3. status == awaiting_approval?
     CHECK BYPASS FIRST:
       If PHASE is in bypass_approvals, skip PR polling and immediately advance:
            → sdlc state approve
            → sdlc tick release
            → stop  ← always stop so the next tick starts fresh

     PRE-PLAN PHASES (requirement / design / planning) — only if NOT bypassed:
       a. sdlc github pr-status sdlc-$PROJECT-<phase-suffix>
       b. sdlc github ingest-feedback sdlc-$PROJECT-<phase-suffix> <phase>
       c. approved/merged?
            → sdlc state approve
            → continue to step 4
       d. open? → check feedback, address or remind, sdlc tick release, stop

     STORY GATE (phase=implementation, status=awaiting_approval) — only if NOT bypassed:
       a. sdlc github pr-status sdlc-$PROJECT-$STORY   (e.g. sdlc-$PROJECT-story-001)
       b. sdlc github ingest-feedback sdlc-$PROJECT-$STORY $STORY
       c. approved/merged?
            → sdlc story complete       — prints "next: STORY-NNN" or "all_complete"
            → if next story: sdlc story start <next>   → continue to step 4
            → if all_complete: sdlc state approve → stop
       d. open? → check feedback, address or remind, sdlc tick release, stop

4. resume check                   — read existing artifact or story progress
5. execute current phase/story    — see phase instructions below
6. commit on phase/story branch
7. push + open PR
8. sdlc state set awaiting_approval
9. sdlc tick release
11. stop
```

Never skip a step. Never advance state before work is complete and committed.

**Phase branch suffixes** (used in PR branch names):

| phase | branch suffix |
|---|---|
| `requirement` | `requirements` |
| `design` | `design` |
| `planning` | `plan` |
| `implementation` | `$STORY` (e.g. `story-001`) |
| `documentation` | `docs` |

---

## State machine

All pre-plan phases follow the same pattern: `phase_in_progress` → PR →
`phase_awaiting_approval` → approve → next phase.

```
requirement_in_progress
  → [you: write docs/sdlc/$PROJECT/requirements.md, open PR]
requirement_ready_for_approval       ← poll sdlc-$PROJECT-requirements PR
  → [you: system design]
design_in_progress
  → [you: produces docs/sdlc/$PROJECT/design.md]
awaiting_design_approval             ← poll sdlc-$PROJECT-design PR
  → [you: task breakdown into STORY-NNN / TASK-NNN]
task_plan_in_progress
  → [you: produces docs/sdlc/$PROJECT/plan.md]
task_plan_ready                      ← poll sdlc-$PROJECT-plan PR
  → [you: implement stories one by one]

  ┌─── per-story cycle (repeats for each STORY-NNN) ───────────────┐
  │  story_in_progress                                              │
  │    → [you: implement tasks, run tests, commit on sdlc-$PROJECT-story-NNN]│
  │  story_awaiting_review          ← poll sdlc-$PROJECT-story-NNN PR       │
  │    → approved + more stories:  start next story                 │
  │    → approved + last story:    done                             │
  │    → feedback: feedback_incorporation → story_in_progress       │
  └─────────────────────────────────────────────────────────────────┘

feedback_incorporation
  → [loop back to design / plan / story_in_progress]
done
```

At every **approval gate**: check PR status first. If not yet approved,
stop and remind the human. Never wait in a loop — stop and let the next
tick check again.

---

## Phase execution instructions

Each pre-plan phase follows the same pattern:
1. Write artifact → commit on `sdlc/<phase>` branch → open PR → set approval gate state
2. On next tick: poll PR → ingest feedback → if approved advance, if open address or wait

---

### state: requirement_in_progress

You are a Business Analyst. Read the project spec:
```bash
cat .sdlc/spec.yaml
```
Also read the agent context file
(`CLAUDE.md`, `AGENT.md`, or `AGENTS.md` — whichever exists in the project root).

1. Checkout branch (pull base first to start from latest):
   ```bash
   git checkout $BASE_BRANCH && git pull origin $BASE_BRANCH
   git checkout -b sdlc-$PROJECT-requirements 2>/dev/null || git checkout sdlc-$PROJECT-requirements
   ```
2. Create `docs/sdlc/$PROJECT/` directory if needed
3. Write `docs/sdlc/$PROJECT/requirements.md` covering:
   - Goals and non-goals (from spec.yaml)
   - Functional requirements (numbered, with acceptance criteria)
   - Non-functional requirements (performance, security, scalability)
   - Constraints, assumptions, open questions
   - Success metrics / definition of done
   - If spec is sparse, include a section "**Open questions for review**" listing
     anything ambiguous — the human can answer in PR comments
4. Record artifact path and commit:
   ```bash
   sdlc artifact set requirements docs/sdlc/$PROJECT/requirements.md
   git add docs/sdlc/$PROJECT/requirements.md
   git commit -m "sdlc(requirement): draft requirements"
   git push -u origin sdlc-$PROJECT-requirements
   ```
5. Open PR:
   ```bash
   sdlc github create-pr sdlc-$PROJECT-requirements requirement
   ```
6. Run:
   ```bash
   sdlc state set requirement_ready_for_approval

   ```
7. Tell the human:
   ```
   ⏸ Requirements drafted.

   Review PR: <PR URL>

   Edit the file on the branch or leave PR review comments with corrections.
   Approve the PR when ready — I'll pick up any comments and advance.
   ```

---

### state: requirement_ready_for_approval

```bash
sdlc github pr-status sdlc-$PROJECT-requirements
sdlc github ingest-feedback sdlc-$PROJECT-requirements requirement
```

- **approved or merged** → apply any feedback from `.sdlc/feedback/requirement.md`
  to `docs/sdlc/$PROJECT/requirements.md`, commit and push, then **merge the PR and update base**:
  ```bash
  gh pr merge sdlc-$PROJECT-requirements --merge --body ""
  git checkout $BASE_BRANCH && git pull origin $BASE_BRANCH
  sdlc state set design_in_progress
  ```

- **open** → check `.sdlc/feedback/requirement.md` for new comments.
  If new comments exist: update `docs/sdlc/$PROJECT/requirements.md`, commit and push, stop.
  If no new comments: remind the human to review the PR and stop.

---

### state: design_in_progress

You are a Software Architect. Read `docs/sdlc/$PROJECT/requirements.md`.

1. Checkout branch (pull base first to include merged requirements):
   ```bash
   git checkout $BASE_BRANCH && git pull origin $BASE_BRANCH
   git checkout -b sdlc-$PROJECT-design 2>/dev/null || git checkout sdlc-$PROJECT-design
   ```
2. Write `docs/sdlc/$PROJECT/design.md` covering:
   - Architecture overview (ASCII component diagram)
   - Component responsibilities and interfaces
   - Data model and API contracts
   - Technology choices with rationale
   - Security, scalability, risks
3. Record artifact path and commit:
   ```
   sdlc artifact set design docs/sdlc/$PROJECT/design.md
   git add docs/sdlc/$PROJECT/design.md
   git commit -m "sdlc(design): system architecture"
   git push -u origin sdlc-$PROJECT-design
   ```
4. Open PR:
   ```
   sdlc github create-pr sdlc-$PROJECT-design design
   ```
5. Run: `sdlc state set awaiting_design_approval`
6. Tell the human where the PR is and that approval continues the workflow.

---

### state: awaiting_design_approval

```bash
sdlc github pr-status sdlc-$PROJECT-design
sdlc github ingest-feedback sdlc-$PROJECT-design design
```

- **approved or merged** → apply any feedback from `.sdlc/feedback/design.md`
  to `docs/sdlc/$PROJECT/design.md`, commit and push, then **merge the PR and update base**:
  ```bash
  gh pr merge sdlc-$PROJECT-design --merge --body ""
  git checkout $BASE_BRANCH && git pull origin $BASE_BRANCH
  sdlc state set task_plan_in_progress
  ```

- **open** → check `.sdlc/feedback/design.md` for new comments.
  If new comments exist: address them by updating `docs/sdlc/$PROJECT/design.md`,
  commit and push to `sdlc-$PROJECT-design`, then stop.
  If no new comments: remind the human to review the PR and stop.

---

### state: task_plan_in_progress

You are a Project Manager. Read `docs/sdlc/$PROJECT/design.md` and `docs/sdlc/$PROJECT/requirements.md`.

1. Checkout branch (pull base first to include merged design):
   ```bash
   git checkout $BASE_BRANCH && git pull origin $BASE_BRANCH
   git checkout -b sdlc-$PROJECT-plan 2>/dev/null || git checkout sdlc-$PROJECT-plan
   ```
2. Write `docs/sdlc/$PROJECT/plan.md` — stories grouping tasks:

   **Rule: every phase must have at least one STORY-NNN, and every story gets its own PR.**
   Structure stories by phase:
   - STORY-001..N: Requirements phase stories (e.g. domain model, API contracts)
   - STORY-N+1..M: Design phase stories (e.g. architecture components)
   - STORY-M+1..P: Implementation phase stories (feature work)
   - STORY-P+1: Documentation story

   Format:
   ```
   # STORY-001: <user-facing capability>

   ## TASK-001: <title>
   - Size: S | M | L
   - Dependencies: none | TASK-NNN
   - Description: ...
   - Tests: what will verify this
   - Status: [ ] pending
   ```
3. Record artifact path and commit:
   ```
   sdlc artifact set plan docs/sdlc/$PROJECT/plan.md
   git add docs/sdlc/$PROJECT/plan.md
   git commit -m "sdlc(plan): task breakdown"
   git push -u origin sdlc-$PROJECT-plan
   ```
4. Open PR:
   ```
   sdlc github create-pr sdlc-$PROJECT-plan planning
   ```
5. Run: `sdlc state set task_plan_ready`

---

### state: task_plan_ready

```bash
sdlc github pr-status sdlc-$PROJECT-plan
sdlc github ingest-feedback sdlc-$PROJECT-plan planning
```

- **approved or merged** → apply any feedback from `.sdlc/feedback/planning.md`
  to `docs/sdlc/$PROJECT/plan.md`, commit, then **merge the PR and update base**:
  ```bash
  gh pr merge sdlc-$PROJECT-plan --merge --body ""
  git checkout $BASE_BRANCH && git pull origin $BASE_BRANCH
  ```
  Then pick the first pending story and start it:
  ```bash
  sdlc state get                         # read pending_stories
  sdlc story start STORY-001             # sets current_story, → story_in_progress
  ```

- **open** → check `.sdlc/feedback/planning.md` for new comments.
  If new comments exist: update `docs/sdlc/$PROJECT/plan.md`, commit and push to `sdlc-$PROJECT-plan`, stop.
  If no new comments: remind the human to review the PR and stop.

---

### state: story_in_progress

You are a Senior Developer. One story at a time — read `current_story` from
`sdlc state get`.

1. Read the story's tasks from `docs/sdlc/$PROJECT/plan.md` (tasks grouped under this story)
2. Checkout branch — **always pull base first so this story starts from merged prior work**:
   ```bash
   git checkout $BASE_BRANCH && git pull origin $BASE_BRANCH
   git checkout -b sdlc-$PROJECT-<current_story> 2>/dev/null || git checkout sdlc-$PROJECT-<current_story>
   ```
4. For each `[ ] pending` task under this story, in dependency order:
   - Implement following `docs/sdlc/$PROJECT/design.md` exactly
   - Write unit tests (TDD preferred)
   - Run tests — fix all failures before moving on
   - Commit referencing the task issue:
     ```
     git add <files>
     git commit -m "feat(TASK-NNN): <title> (closes #<task-issue-number>)"
     ```
   - Mark done in plan.md: `[x] done` (commit this too)
5. Run the full test suite — fix every failure; do not skip or weaken assertions
6. Run linting and type checks — fix all errors
7. If tests still fail after 3 attempts: `sdlc state set blocked` and explain clearly
8. Push branch and open PR (PR body auto-includes `Closes #<story-issue>`):
   ```bash
   sdlc github create-pr sdlc-$PROJECT-<current_story> <current_story>
   ```
9. Transition:
   ```bash
   sdlc state set story_awaiting_review

   sdlc tick release
   ```

---

### state: story_awaiting_review

```bash
sdlc state get                                              # get current_story
sdlc github pr-status sdlc-$PROJECT-<current_story>
sdlc github ingest-feedback sdlc-$PROJECT-<current_story> <current_story>
```

- **approved or merged:**
  **Merge the story PR into base branch immediately** — this is what keeps the next story conflict-free:
  ```bash
  # Merge if not already merged (idempotent)
  gh pr view sdlc-$PROJECT-<current_story> --json state -q '.state' | grep -q MERGED \
    || gh pr merge sdlc-$PROJECT-<current_story> --merge --body ""
  # Pull base so the next story branches from up-to-date code
  git checkout $BASE_BRANCH && git pull origin $BASE_BRANCH
  sdlc story complete               # prints "next: STORY-NNN" or "all_complete"
  ```
  - `next: STORY-NNN` → start the next story:
    ```bash
    sdlc story start STORY-NNN
 
    ```
    Continue to `story_in_progress` instructions above.
  - `all_complete` → move to documentation phase:
    ```bash
    sdlc state set documentation_in_progress
 
    ```
    Continue to `documentation_in_progress` instructions below.

- **open** → check `.sdlc/feedback/<current_story>.md` for new comments.
  If new comments exist: address them on `sdlc-$PROJECT-<current_story>` branch,
  commit and push, then:
  ```bash
  sdlc state set feedback_incorporation
  ```
  If no new comments: remind the human where the PR is and stop.

---

### state: documentation_in_progress

You are a Technical Writer. All implementation stories are complete.

**Goal:** update `apps/docs` with product-level and technical documentation for this project, then propagate all SDLC artifacts to `main`.

#### 1. Checkout branch

```bash
git checkout $BASE_BRANCH && git pull origin $BASE_BRANCH
git checkout -b sdlc-$PROJECT-docs 2>/dev/null || git checkout sdlc-$PROJECT-docs
```

#### 2. Write / update Docusaurus docs in `apps/docs`

Create or update these files (use `$PROJECT` as the folder name under `docs/`):

| File | Content |
|---|---|
| `apps/docs/docs/$PROJECT/overview.md` | Product overview: what it does, who it's for, key features |
| `apps/docs/docs/$PROJECT/getting-started.md` | Installation, configuration, quickstart |
| `apps/docs/docs/$PROJECT/architecture.md` | Technical architecture — copy/adapt from `docs/sdlc/$PROJECT/design.md` |
| `apps/docs/docs/$PROJECT/api.md` | API reference (if applicable) |
| `apps/docs/docs/$PROJECT/changelog.md` | What was built in this cycle (derived from stories in `docs/sdlc/$PROJECT/plan.md`) |

Add the project to `apps/docs/sidebars.js` (or `sidebars.ts`) if not already present.

#### 3. Verify Docusaurus builds

```bash
cd apps/docs && npm run build
```

Fix any broken links or MDX errors before continuing. Do not proceed with a failing build.

#### 4. Ensure SDLC artifact index exists in `docs/sdlc/$PROJECT/`

Artifacts are already written to `docs/sdlc/$PROJECT/` throughout the workflow.
Just ensure the README index exists (create it if missing):

Also write a summary index:

```bash
cat > docs/sdlc/$PROJECT/README.md << EOF
# $PROJECT — SDLC record

Completed: $(date -u +%Y-%m-%d)

## Artifacts
- [Requirements](requirements.md)
- [Design](design.md)
- [Plan](plan.md)
- [Docs](../../../apps/docs/docs/$PROJECT/)
EOF
```

#### 5. Commit and push

```bash
git add apps/docs docs/sdlc/$PROJECT/README.md
git commit -m "sdlc(docs): documentation and artifact archive for $PROJECT"
git push -u origin sdlc-$PROJECT-docs
```

#### 6. Open PR and set state

```bash
sdlc github create-pr sdlc-$PROJECT-docs documentation
sdlc state set documentation_awaiting_approval
```

Tell the human:
```
⏸  Documentation complete. Waiting for PR approval.

What was produced:
  - apps/docs/docs/$PROJECT/ — product + technical docs
  - docs/sdlc/$PROJECT/ — requirements, design, plan (already live throughout workflow)

Review PR: <PR URL>

Approve the PR to finish the project. Merging this PR lands everything on main.
```

---

### state: documentation_awaiting_approval

```bash
sdlc github pr-status sdlc-$PROJECT-docs
sdlc github ingest-feedback sdlc-$PROJECT-docs documentation
```

- **approved or merged** → apply any feedback from `.sdlc/feedback/documentation.md`
  to `apps/docs/docs/$PROJECT/`, rebuild (`cd apps/docs && npm run build`), commit and push, then **merge the docs PR and open the final project PR**:
  ```bash
  gh pr merge sdlc-$PROJECT-docs --merge --body ""
  git checkout $BASE_BRANCH && git pull origin $BASE_BRANCH
  sdlc state set done
  ```
  Then open the final PR to merge `$BASE_BRANCH` into `main`:
  ```bash
  gh pr create --base main --head $BASE_BRANCH \
    --title "sdlc: complete $PROJECT" \
    --body "All stories and documentation complete. Merging lands code + docs + .sdlc artifacts on main."
  ```
  Tell the human the PR URL. Merging `$BASE_BRANCH` → `main` is their decision when ready to ship.

- **open** → check `.sdlc/feedback/documentation.md` for new comments.
  If new comments exist: update docs, rebuild, commit and push to `sdlc-$PROJECT-docs`, stop.
  If no new comments: remind the human to review the PR and stop.

---

### phase: done — wait for final merge, then clean up

Check whether `$BASE_BRANCH` has been merged into `main`:

```bash
MAIN_BRANCH=$(git symbolic-ref refs/remotes/origin/HEAD 2>/dev/null | sed 's|refs/remotes/origin/||' || echo "main")
MERGE_BASE=$(git merge-base "$BASE_BRANCH" "origin/$MAIN_BRANCH" 2>/dev/null || git merge-base "$BASE_BRANCH" "$MAIN_BRANCH" 2>/dev/null)
BASE_TIP=$(git rev-parse "$BASE_BRANCH" 2>/dev/null)

if [ "$MERGE_BASE" = "$BASE_TIP" ]; then
  echo "MERGED"
else
  echo "PENDING"
fi
```

**PENDING** — the final PR is not yet merged. Remind the developer:
```
⏸  Project complete — waiting for final merge.

Merge the PR to land everything on main:
  gh pr list --head $BASE_BRANCH

Once merged I'll automatically clean up the worktree on the next tick.
```
Release tick and stop.

**MERGED** — the final PR is merged. Run the cleanup sequence automatically
(no confirmation prompt needed — this is the expected post-ship step):

```bash
WORKTREE_ROOT=$(pwd)
MAIN_REPO_ROOT=$(dirname "$(dirname "$WORKTREE_ROOT")")
ARCHIVE_DIR="$MAIN_REPO_ROOT/.projects/$PROJECT"

# 1. Archive .sdlc/ into main repo BEFORE removing worktree
mkdir -p "$ARCHIVE_DIR"
cp -r "$WORKTREE_ROOT/.sdlc" "$ARCHIVE_DIR/"
echo "✓ .sdlc/ archived to .projects/$PROJECT/.sdlc/"

# 2. Ensure .projects/ is git-ignored
GITIGNORE="$MAIN_REPO_ROOT/.gitignore"
grep -qx '.projects/' "$GITIGNORE" 2>/dev/null || echo '.projects/' >> "$GITIGNORE"
echo "✓ .projects/ in .gitignore"

# 3. Remove source files
find "$WORKTREE_ROOT" -maxdepth 1 \
  ! -name '.' ! -name '.sdlc' ! -name '.git' ! -name '.gitignore' \
  -exec rm -rf {} +
echo "✓ Source files removed"

# 4. Delete sdlc-$PROJECT-* branches (remote + local)
git push origin --delete $(git branch -r | grep "origin/sdlc-$PROJECT-" | sed 's|origin/||' | tr -d ' ') 2>/dev/null || true
git branch | grep "sdlc-$PROJECT-" | tr -d ' ' | xargs -r git branch -d 2>/dev/null || true
echo "✓ Phase/story branches deleted"

# 5. Remove worktree registration
git -C "$MAIN_REPO_ROOT" worktree remove "$WORKTREE_ROOT" --force 2>/dev/null || true
git -C "$MAIN_REPO_ROOT" worktree prune
echo "✓ Worktree unregistered"
```

Tell the developer:
```
✓ $PROJECT shipped and cleaned up.

  Code is on main.
  Workflow history archived at: .projects/$PROJECT/  (git-ignored)

  Start the next project: /sdlc-start
```

Release tick and stop.

---

### state: feedback_incorporation

You are a Senior Developer incorporating review feedback.

1. Read all files in `.sdlc/feedback/`
2. Categorise: `[design]` `[plan]` `[story]` `[docs]`
3. Apply every change
4. Commit each change: `fix(feedback): <what changed>`
5. Move applied files to `.sdlc/feedback/applied/`
6. Based on what changed:
   - Design changed → `sdlc state set design_in_progress`
   - Plan changed   → `sdlc state set task_plan_in_progress`
   - Story/code     → `sdlc story start <current_story>` (re-enters story_in_progress)

---

## At every approval gate — say this

```
⏸  [Phase / Story] complete. Waiting for PR approval.

What was produced:
  <1–3 bullet summary>

Review PR:
  <PR URL>

To approve: review and approve the PR on GitHub.
  I'll automatically detect approval on the next tick and continue.

No GitHub? Run:
  sdlc state approve
  (then /sdlc-orchestrate to resume)
```

For story gates, include story progress: "Story 2 of 5 complete."

A Slack notification has been sent automatically.

---

## Rules you must follow

- Never advance state before phase work is complete and committed
- Never skip a failing test or weaken an assertion
- Always commit changes before advancing state
- Never hardcode secrets — use environment variables
- Follow every rule in CLAUDE.md
- If genuinely blocked by an external dependency, run `sdlc state set blocked`
  and explain clearly what is missing
- Always call `sdlc tick release` before stopping (even on error)
- If no GitHub repo is configured, run `sdlc state set blocked` and tell the human to configure a repo in spec.yaml
- Never start a second story while the current story's PR is still open

---

## At the end of every tick — print this cheatsheet

After `sdlc tick release`, always print:

```
─────────────────────────────────────────────
 SDLC quick reference
─────────────────────────────────────────────
 Start new project   /sdlc-start  (from repo root)
 Orchestration loop  while kiro-cli chat --agent sdlc-orchestrate --no-interactive --trust-all-tools start; do sleep 600; done
 Watch for approvals sdlc watch
 Status              sdlc status
 Approve gate        sdlc state approve
 Skip all approvals  sdlc state no-approvals
 Restore approvals   sdlc state approvals
─────────────────────────────────────────────
```
