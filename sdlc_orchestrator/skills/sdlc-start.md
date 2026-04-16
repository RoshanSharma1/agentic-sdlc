# sdlc-start

Bootstrap and launch SDLC orchestration for the current project in one shot.
Handles init, setup, and the first orchestration tick — detecting what's already
done and skipping it.

---

## Step 1 — Check init status

```bash
ls .sdlc/spec.yaml 2>/dev/null && echo "INIT_DONE" || echo "INIT_NEEDED"
```

**If `INIT_NEEDED`:** run:
```bash
sdlc init .
```

Wait for it to complete. This scaffolds `.sdlc/`, installs hooks and skills,
and writes an empty spec. Continue to Step 2.

**If `INIT_DONE`:** skip to Step 2.

---

## Step 2 — Check setup status

Read `.sdlc/spec.yaml` and run:
```bash
sdlc state get
```

Setup is **already done** if ALL of the following are true:
- `spec.yaml` has a non-empty `description` field
- `state` is anything other than `requirement_in_progress`

**If already done:** skip to Step 3.

**If setup is needed:** run the full `sdlc-setup` skill inline now — interview
the developer, fill in `spec.yaml`, draft requirements, and advance state to
`requirement_ready_for_approval`. Do not return to this skill until setup is
complete.

---

## Step 3 — Launch orchestration

Setup is complete. Run the `sdlc-orchestrate` skill now to execute the first
autonomous tick.

After it completes (or pauses at an approval gate), read `executor` from
`.sdlc/spec.yaml` and tell the developer the correct continuous-loop command:

| executor | continuous loop command |
|----------|------------------------|
| `claude-code` | `while true; do claude -p "/sdlc-orchestrate"; sleep 600; done` |
| `codex`       | `while true; do codex -p "/sdlc-orchestrate"; sleep 600; done` |
| `kiro`        | `while true; do kiro -p "/sdlc-orchestrate"; sleep 600; done` |
| `cline`       | Run `/sdlc-orchestrate` manually each tick in VS Code |

Then say:

```
✓ SDLC orchestration started.

To run continuously (paste the command for your agent above):

  while true; do <agent> -p "/sdlc-orchestrate"; sleep 600; done

Each iteration spawns a fresh agent process (no context bleed between ticks).
Run this in a dedicated terminal tab and leave it running.

The agent will pause and notify you at each approval gate.
To approve a gate:
  sdlc state approve
```
