# sdlc-start

Set up and launch SDLC orchestration. Run this once per project cycle.

Accepts an optional `--no-approvals` flag:
- `sdlc-start` — default, requires manual PR approval at each phase gate
- `sdlc-start --no-approvals` — agent advances through all phases automatically

---

## Step 1 — Determine project name

**Always ask the user** — never silently pick a default or reuse `.sdlc/active`.

List any existing projects first:

```bash
ls .sdlc/projects/ 2>/dev/null
```

Then ask:

```
Existing projects: <list, or "none">

What is the project name?
(Enter a name from the list to continue an existing project, or a new name to start one.)
```

Wait for the user's answer before proceeding. Do not infer or default.

Set the chosen name as active (slugified — lowercase, hyphens only):
```bash
PROJECT=$(echo "<chosen-name>" | tr '[:upper:]' '[:lower:]' | sed 's/[^a-z0-9-]/-/g' | sed 's/--*/-/g' | sed 's/^-//;s/-$//')
sdlc project activate "$PROJECT"
echo "Active project: $PROJECT"
```

---

## Step 2 — Scaffold project directory if new

Use `$PROJECT` from Step 1 (do not re-read `.sdlc/active`).

```bash
if [ ! -d ".sdlc/projects/$PROJECT" ]; then
  mkdir -p .sdlc/projects/$PROJECT/memory
  mkdir -p .sdlc/projects/$PROJECT/workflow/artifacts
  mkdir -p .sdlc/projects/$PROJECT/workflow/logs
  mkdir -p .sdlc/projects/$PROJECT/feedback
  echo "Created project: $PROJECT"
fi
```

---

## Step 3 — Ensure git repo and create worktree

Use `$PROJECT` from Step 1.

```bash
git rev-parse --git-dir 2>/dev/null || (git init && git add -A && git commit -m "init" --allow-empty)

REPO_ROOT=$(git rev-parse --show-toplevel)

# Guard: must run from repo root, not from inside a worktree
if git rev-parse --git-dir 2>/dev/null | grep -q "\.git/worktrees"; then
  echo "ERROR: you are inside a worktree. Run /sdlc-start from the repo root: $REPO_ROOT"
  exit 1
fi

WORKTREE_PATH="$REPO_ROOT/worktree/$PROJECT"

# Create a dedicated branch for this worktree, branching from main
git checkout main 2>/dev/null || true
git checkout -b worktree/$PROJECT 2>/dev/null || git checkout worktree/$PROJECT
git push -u origin worktree/$PROJECT 2>/dev/null || true

# Add the worktree
git worktree add "$WORKTREE_PATH" worktree/$PROJECT 2>/dev/null || echo "Worktree already exists"

echo "Worktree: $WORKTREE_PATH"
echo "Base branch: worktree/$PROJECT"
```

Scaffold `.sdlc/` inside the worktree and record `base_branch`:
```bash
mkdir -p "$WORKTREE_PATH/.sdlc/projects/$PROJECT/memory"
mkdir -p "$WORKTREE_PATH/.sdlc/projects/$PROJECT/workflow/artifacts"
mkdir -p "$WORKTREE_PATH/.sdlc/projects/$PROJECT/workflow/logs"
mkdir -p "$WORKTREE_PATH/.sdlc/projects/$PROJECT/feedback"
grep -qxF "$PROJECT" "$WORKTREE_PATH/.sdlc/active" 2>/dev/null || echo "$PROJECT" >> "$WORKTREE_PATH/.sdlc/active"

# Copy spec if it exists
cp ".sdlc/projects/$PROJECT/spec.yaml" "$WORKTREE_PATH/.sdlc/projects/$PROJECT/spec.yaml" 2>/dev/null || true

# Write state.json with base_branch
python3 -c "
import json
f = '$WORKTREE_PATH/.sdlc/projects/$PROJECT/workflow/state.json'
try:
    d = json.load(open(f))
except Exception:
    d = {}
d['base_branch'] = 'worktree/$PROJECT'
d['current_branch'] = 'worktree/$PROJECT'
json.dump(d, open(f, 'w'), indent=2)
print('base_branch = worktree/$PROJECT')
"
```

---

## Step 4 — Interview the developer

Always ask — do not skip even if a spec already exists.

Auto-detect what you can before asking:
```bash
git remote get-url origin 2>/dev/null
ls package.json pyproject.toml requirements.txt go.mod Cargo.toml pom.xml 2>/dev/null
```

Ask only these two questions:

| Field | Question |
|---|---|
| `project_name` | What is this project called? |
| `description` | In one sentence, what problem does it solve? |

Then, **if `--no-approvals` was NOT passed**, ask:

```
Do you want to require manual approval at each phase gate (requirements, design, plan, stories)?
  [Y] Yes — I'll review and approve each phase PR on GitHub (default)
  [N] No  — agent advances automatically through all phases
```

Set `phase_approvals` in spec.yaml:
- `--no-approvals` flag passed → all phases `false`
- User answered **No** → all phases `false`
- User answered **Yes** (default) → all phases `true`

Set these automatically without asking:
- `tech_stack` — detect from files
- `repo` — parse from `git remote get-url origin`
- `executor` — `kiro`

Write spec to both `.sdlc/projects/$PROJECT/spec.yaml` and `$WORKTREE_PATH/.sdlc/projects/$PROJECT/spec.yaml`, then:
```bash
sdlc github setup 2>/dev/null || true
```

---

## Step 5 — Done

**Stop here.** Do not run any phases. The orchestration loop does that.

Tell the developer:

```
✓ Project '<name>' is ready.

Branch structure:
  main                              ← production, never touched during SDLC
  worktree/<name>                   ← base branch; all phase branches fork from here
    sdlc-<name>-requirements        → PR → worktree/<name>
    sdlc-<name>-design              → PR → worktree/<name>
    sdlc-<name>-plan                → PR → worktree/<name>
    sdlc-<name>-story-001           → PR → worktree/<name>
    ...

  When all stories are done:
    Agent opens PR: worktree/<name> → main
    You review and merge when ready to ship.

Working directory: worktree/<name>/

Run the orchestration loop from the worktree to start:

  cd worktree/<name>
  while kiro-cli chat --agent sdlc-orchestrate --no-interactive --trust-all-tools start; do sleep 600; done

Approve each phase by merging the PR on GitHub, or: sdlc state approve
```
