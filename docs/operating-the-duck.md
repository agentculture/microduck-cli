# Operating the duck: a six-command walkthrough

Everything below runs against `robotd --fake` — a single-process stand-in for
the real daemon that answers the same JSON-RPC socket
(`docs/upstream-pins.md`'s pinned `pollen-robotics/microduck` `sim-remote-io`
commit). No physical MicroDuck is required, and **none has been driven from
this CLI yet** — that verification is t23's job
(see `docs/verification/` once it lands). Every command here also has a
per-verb `microduck-cli explain <path>` entry with more detail and its own
upstream link.

Prerequisites: a checkout of `pollen-robotics/microduck` (and, for the
training lane, `pollen-robotics/microduck_rl`) at the pinned commits in
[`docs/upstream-pins.md`](upstream-pins.md), either beside this repo
(`../microduck`, `../microduck_rl`) or pointed at with `MICRODUCK_CLONE`
/ `DUCK_SIM_RL`. `env doctor` (step 1) tells you exactly what is missing if
anything is.

## 1. `env doctor` — is this box ready?

```bash
uv run microduck env doctor
```

Diagnoses the upstream clones, a cargo toolchain new enough to build
`robotd`/`robotctl`/`tofd`/`sounds`, the built daemons themselves, the
`microduck_rl` venv with `onnxruntime` installed, a state directory short
enough for unix sockets, a free `duck-body` port, the host class, and
whether the optional `HF_TOKEN` / `WANDB_API_KEY` / `DUCK_PIN` credentials
are configured (reported as set/unset only, never their values). Exits `2`
naming every failing check's remediation when unhealthy, `0` otherwise. See
`microduck-cli explain env doctor` and
<https://github.com/pollen-robotics/microduck/blob/sim-remote-io/docs/design/simulation.md>.

## 2. `env up --fake` — bring up the stack

```bash
uv run microduck env up --fake
```

Builds the daemons (unless `--skip-build`), starts `robotd --fake`, and
polls `hello` + `robot.health` over its control socket until it reports
healthy (60s timeout for `--fake`). Nothing is typed by hand — the clone
paths, the state directory and the socket path are all derived. Swap
`--fake` for `--sim --ducks N` to run the real `duck-body` simulator instead
(120s timeout, needs a scene and optionally `--headless`). See
`microduck-cli explain env up`.

## 3. `duck health` — the robot's own verdict

```bash
uv run microduck duck health
```

Asks `robot.health` and renders the loop, bus and IMU state, plus battery
and motor temperatures when they are measured (a bare `robotd --fake`
measures neither, and says so rather than reporting zero). Exits `2` when
the robot is not healthy, mirroring `robotctl health`. See
`microduck-cli explain duck health` and
<https://github.com/pollen-robotics/microduck/blob/main/docs/robot/cheatsheet.md>.

## 4. `rules engine run` — run the tick engine

```bash
uv run microduck rules engine run --max-ticks 50 --apply
```

Runs the 50 Hz tick engine in the foreground against the fake duck: connect,
`hello`, `health`, `init` (gated), `enable` (gated), `armed` — each step
logged on stderr. `--max-ticks 50` stops after 50 ticks (about one second at
50 Hz) instead of running until Ctrl-C, which is what makes this safe to
paste into a walkthrough. `--apply` is needed because `init`/`enable` power
and move the (fake) robot, so the verb goes through the same TTY-confirm /
dry-run / `--apply` gate every motion verb does. On any abnormal exit the run
releases what it energized (`robot.stop`, pose, mouth, sound) independently,
never `robot.relax`. See `microduck-cli explain rules engine run`.

## 5. `rules intent stop` — inject one intent

```bash
uv run microduck rules intent stop
```

Submits one intent through the same ONE admission registry a rule fires
through — `stop` zeroes the intents this client is sending (not an emergency
stop; nothing here cuts servo power). With no engine live the intent is
validated and the would-be admission printed, sending nothing; with an
engine live (as in step 4, if still running in another terminal) it is
spooled and the engine's acknowledgement is printed. See
`microduck-cli explain rules intent`.

## 6. `env down` — tear the stack back down

```bash
uv run microduck env down
```

Stops every process `env up` started under the state directory: reads each
pidfile, deletes it *before* signalling anything, and signals a pid only
when `/proc/<pid>/cmdline` still names the binary that pidfile was written
for — never by name (`pkill`/`killall`), because pids are recycled. See
`microduck-cli explain env down`.

## Where to go next

- [`docs/upstream-pins.md`](upstream-pins.md) — the exact upstream commits
  every command above is validated against.
- `microduck-cli explain <noun>` (`env`, `duck`, `policy`, `rules`) — the full
  per-verb reference, each entry linking the upstream page it implements
  against.
- [`.claude/skills/operate-microduck/SKILL.md`](../.claude/skills/operate-microduck/SKILL.md)
  — the first-party operator skill: this walkthrough as a procedure an agent can
  follow end to end (open the sim with its MuJoCo window, operate the duck, watch
  it, close it down), plus the hard rules driving it must not break.
  `microduck-cli learn` prints the recipe for recreating it in another runtime.
- [`CLAUDE.md`](../CLAUDE.md) — the architecture: the agent-first CLI
  contracts, the behaviour engine, and what to take from each sibling repo.
