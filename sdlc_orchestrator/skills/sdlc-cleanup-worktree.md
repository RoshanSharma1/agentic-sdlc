# sdlc-cleanup-worktree

Clean up a completed worktree after its `worktree/$PROJECT` branch has been
merged into the main repo. Archives `.sdlc/` into the main repo under
`.projects/$PROJECT/` (git-ignored), removes source files, and unregisters
the worktree.

Run this from inside the worktree directory (`worktree/$PROJECT/`).

---

## Step 1 — Safety checks

```bash
sdlc state get
```

Abort with an error if **any** of the following are true:

- `phase` is not `done` — project is still in progress.
- `status` is not `done` — project is still in progress.

Read `base_branch` from the output (e.g. `worktree/inkstack-foo`).

```bash
BASE_BRANCH=$(sdlc state get | grep '^base_branch:' | awk '{print $2}')
echo "Base branch: $BASE_BRANCH"
```

Check the base branch has been merged into the main branch:

```bash
REPO_ROOT=$(git rev-parse --show-toplevel)
MAIN_BRANCH=$(git symbolic-ref refs/remotes/origin/HEAD 2>/dev/null | sed 's|refs/remotes/origin/||' || echo "main")

MERGE_BASE=$(git merge-base "$BASE_BRANCH" "origin/$MAIN_BRANCH" 2>/dev/null || git merge-base "$BASE_BRANCH" "$MAIN_BRANCH" 2>/dev/null)
BASE_TIP=$(git rev-parse "$BASE_BRANCH" 2>/dev/null)

if [ "$MERGE_BASE" != "$BASE_TIP" ]; then
  echo "ERROR: $BASE_BRANCH has not been fully merged into $MAIN_BRANCH."
  echo "Merge the PR first, then re-run this skill."
  exit 1
fi

echo "✓ $BASE_BRANCH is merged into $MAIN_BRANCH"
```

If the check fails, stop and tell the developer to merge the PR first.

---

## Step 2 — Archive .sdlc/ into main repo

Move `.sdlc/` to `.projects/$PROJECT/` in the main repo root **before**
removing the worktree, so the workflow history is preserved.

```bash
WORKTREE_ROOT=$(pwd)
PROJECT=$(basename "$WORKTREE_ROOT" | tr '[:upper:]' '[:lower:]' | sed 's/[^a-z0-9-]/-/g;s/--*/-/g;s/^-//;s/-$//')
MAIN_REPO_ROOT=$(dirname "$(dirname "$WORKTREE_ROOT")")
ARCHIVE_DIR="$MAIN_REPO_ROOT/.projects/$PROJECT"

mkdir -p "$ARCHIVE_DIR"
cp -r "$WORKTREE_ROOT/.sdlc" "$ARCHIVE_DIR/"
echo "✓ .sdlc/ archived to .projects/$PROJECT/.sdlc/"
```

Ensure `.projects/` is git-ignored in the main repo:

```bash
GITIGNORE="$MAIN_REPO_ROOT/.gitignore"
if ! grep -qx '.projects/' "$GITIGNORE" 2>/dev/null; then
  echo '.projects/' >> "$GITIGNORE"
  echo "✓ .projects/ added to .gitignore"
else
  echo "✓ .projects/ already in .gitignore"
fi
```

---

## Step 3 — Confirm and delete source files

Preview what will be removed:

```bash
find "$WORKTREE_ROOT" -maxdepth 1 \
  ! -name '.' ! -name '.sdlc' ! -name '.git' ! -name '.gitignore' \
  | sort
```

Present this list and ask for confirmation via `AskUserQuestion`:

```
The following will be permanently removed from this worktree:
  <list from above>

.sdlc/ has already been archived to .projects/$PROJECT/

Proceed? [y/N]
```

If the answer is not `y` or `yes` (case-insensitive), abort.

```bash
find "$WORKTREE_ROOT" -maxdepth 1 \
  ! -name '.' ! -name '.sdlc' ! -name '.git' ! -name '.gitignore' \
  -exec rm -rf {} +

echo "✓ Source files removed"
```

---

## Step 4 — Remove stale phase and story branches

```bash
# Remote branches
git push origin --delete $(git branch -r | grep "origin/sdlc-$PROJECT-" | sed 's|origin/||' | tr -d ' ') 2>/dev/null \
  && echo "✓ Remote sdlc-$PROJECT-* branches deleted" \
  || echo "(no remote branches to delete)"

# Local branches
git branch | grep "sdlc-$PROJECT-" | tr -d ' ' | xargs -r git branch -d 2>/dev/null || true
```

---

## Step 5 — Remove the git worktree registration

```bash
git -C "$MAIN_REPO_ROOT" worktree remove "$WORKTREE_ROOT" --force 2>/dev/null \
  && echo "✓ Worktree unregistered" \
  || echo "(worktree already unregistered)"

git -C "$MAIN_REPO_ROOT" worktree prune
```

---

## Step 6 — Done

Tell the developer:

```
✓ $PROJECT archived and cleaned up.

  Workflow history preserved at: .projects/$PROJECT/.sdlc/
  (git-ignored — local only, never committed)

  To review:
    cat .projects/$PROJECT/.sdlc/workflow/state.json
    ls  .projects/$PROJECT/.sdlc/memory/

  Branch worktree/$PROJECT is merged. Cleanup complete.
```
