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
- `state` is anything other than `draft_requirement`

**If already done:** skip to Step 3.

**If setup is needed:** run the full `sdlc-setup` skill inline now — interview
the developer, fill in `spec.yaml`, draft requirements, and advance state to
`requirement_ready_for_approval`. Do not return to this skill until setup is
complete.

---

## Step 3 — Launch orchestration

Setup is complete. Run the `sdlc-orchestrate` skill now to execute the first
autonomous tick.

After it completes (or pauses at an approval gate), tell the developer:

```
✓ SDLC orchestration started.

To run continuously with a clean context on every tick:

  while true; do claude -p "/sdlc-orchestrate"; sleep 600; done

Each iteration spawns a fresh Claude process (no context bleed between ticks).
Run this in a dedicated terminal tab and leave it running.

Claude will pause and notify you at each approval gate.
To approve a gate:
  sdlc state approve
```
