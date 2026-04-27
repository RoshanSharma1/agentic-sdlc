# sdlc-start

Set up and launch SDLC orchestration. Run this once per project cycle.

Accepts an optional `--no-approvals` flag:
- `sdlc-start` — default, requires manual PR approval at each phase gate
- `sdlc-start --no-approvals` — agent advances through all phases automatically

---

## Step 1 — Pick or name the project

**First**, extract and present ideas from the ideas folder:

```bash
python3 - <<'EOF'
import os, zipfile, re, json
folder = os.path.expanduser("~/Documents/Claude/Projects/Ideas for contentautomation platform")
ideas = []
for fname in sorted(f for f in os.listdir(folder) if f.endswith(".docx")):
    path = os.path.join(folder, fname)
    try:
        with zipfile.ZipFile(path) as z:
            xml = z.read("word/document.xml").decode("utf-8")
        text = re.sub(r"<[^>]+>", " ", xml)
        text = re.sub(r"\s+", " ", text).strip()
        ideas.append({"file": fname, "text": text[:4000]})
    except Exception as e:
        ideas.append({"file": fname, "text": f"(could not parse: {e})"})
print(json.dumps(ideas, indent=2))
EOF
```

Read the extracted content, then list existing projects alongside the ideas and ask **one question** via `AskUserQuestion`:

```
Here are your saved ideas:

1. <title> — <one-sentence summary>
2. <title> — <one-sentence summary>
3. <title> — <one-sentence summary>
4. <title> — <one-sentence summary>
5. <title> — <one-sentence summary>

Existing projects you can continue:
  • cap-agent-2.0
  • multi-tenant-saas-platform-workflow-marketplace
  • real-time-collaborative-content-studio

Enter:
  • A number (or numbers like "1 3") to start from an idea
  • An existing project name to continue it
  • A new name to start from scratch
```

**Handle the response:**

- **Number(s)** — derive the project name by slugifying the idea title. Pre-load `description`, `users`, `goals`, `non_goals`, `tech_stack` from the selected PRD(s) so the interview in Step 4 skips fields already covered. If multiple numbers, merge the ideas and confirm the combined scope before continuing.
- **Existing project name** — set it as active and skip to Step 2 (worktree already exists).
- **New name** — proceed normally with a blank spec.

Keep the chosen slug in `$PROJECT` for the remaining steps. Do not create or update `.sdlc/` in the main repo.
```bash
PROJECT=$(echo "<chosen-name>" | tr '[:upper:]' '[:lower:]' | sed 's/[^a-z0-9-]/-/g' | sed 's/--*/-/g' | sed 's/^-//;s/-$//')
echo "Active project: $PROJECT"
```

---

## Step 2 — Ensure git repo and create worktree

```bash
git rev-parse --git-dir 2>/dev/null || (git init && git add -A && git commit -m "init" --allow-empty)

# Reuse the PROJECT slug chosen in Step 1.
PROJECT="<project-slug>"
REPO_ROOT=$(git rev-parse --show-toplevel)
WORKTREE_PATH="$REPO_ROOT/worktree/$PROJECT"

# Create a dedicated branch for this worktree without checking it out in the
# main repo.
BASE_REF=main
git rev-parse --verify "$BASE_REF" >/dev/null 2>&1 || BASE_REF=HEAD
git rev-parse --verify "worktree/$PROJECT" >/dev/null 2>&1 || git branch "worktree/$PROJECT" "$BASE_REF"
git push -u origin worktree/$PROJECT 2>/dev/null || true

# Add the worktree
git worktree add "$WORKTREE_PATH" worktree/$PROJECT 2>/dev/null || echo "Worktree already exists"

echo "Worktree: $WORKTREE_PATH"
echo "Base branch: worktree/$PROJECT"
```

Immediately scaffold `.sdlc/` inside the worktree. This is the only persistent SDLC state for the project. Do **not** create `.sdlc/projects/`.
```bash
mkdir -p "$WORKTREE_PATH/.sdlc/memory"
mkdir -p "$WORKTREE_PATH/.sdlc/workflow/artifacts"
mkdir -p "$WORKTREE_PATH/.sdlc/workflow/logs"
mkdir -p "$WORKTREE_PATH/.sdlc/feedback"

# Write complete workflow state with base_branch
python3 -c "
from pathlib import Path
from sdlc_orchestrator.state_machine import WorkflowState
wf = WorkflowState(Path('$WORKTREE_PATH'))
wf._data['base_branch'] = 'worktree/$PROJECT'
wf._data['current_branch'] = 'worktree/$PROJECT'
wf.save()
print('base_branch = worktree/$PROJECT')
"

(cd "$WORKTREE_PATH" && sdlc relink 2>/dev/null || true)
```

---

## Step 3 — Interview the developer (if spec is missing or incomplete)

Read the worktree spec:
```bash
PROJECT="<project-slug>"
WORKTREE_PATH="$(git rev-parse --show-toplevel)/worktree/$PROJECT"
cat "$WORKTREE_PATH/.sdlc/spec.yaml" 2>/dev/null
```

If `description` is already filled in, use it as the default and do not re-ask that field.

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
Do you want to require manual approval at each phase gate (requirements, design, plan, stories, testing, docs)?
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

Write spec to `$WORKTREE_PATH/.sdlc/spec.yaml`, then run GitHub setup from the worktree:
```bash
cd "$WORKTREE_PATH"
sdlc github setup 2>/dev/null || true
```

---

## Step 4 — Run full setup interview

Invoke the `sdlc-setup` skill now from `worktree/$PROJECT` to conduct the full interview (goals, non-goals, users, constraints) and draft requirements. All reads and writes must target the worktree `.sdlc/` directory.

---

## Step 5 — Done

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
    sdlc-<name>-testing             → PR → worktree/<name>
    sdlc-<name>-docs                → PR → worktree/<name>
    ...

  When all stories are done:
    Agent opens PR: worktree/<name> → main
    You review and merge when ready to ship.

Working directory: worktree/<name>/

Start the pipeline for the worktree:

  POST /api/projects/<name>/start-pipeline

Approve each phase by merging the PR on GitHub, or: sdlc state approve
```
