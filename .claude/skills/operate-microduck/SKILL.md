---
name: operate-microduck
description: >
  Open the MicroDuck simulation with its MuJoCo window, operate the duck —
  stand it up, enable its policy, run skills, turn its head, send motion,
  drive the data-only rules engine, monitor and record its senses — and close
  the simulation down again when the user is done. Drives the `microduck` CLI
  (`env up` / `duck` / `rules` / `env down`) end to end, including the
  screenshot recipe for watching the MuJoCo window from a headless session.
  First-party to microduck-cli — authored here, not vendored. Use when the
  user says "operate the duck", "open the simulation", "start the sim",
  "close the sim", "make the duck stand / walk / look / quack", or asks to
  see what the duck is doing.
type: command
---

# operate-microduck — open the sim, drive the duck, close it down

The skill is named **`operate-microduck`**. It is the operator front door to
the `microduck` CLI: one lane from a cold box to a standing duck in a MuJoCo
window, through the verbs that move it, and back down to nothing running.

## When to use

- The user asks to **open / start the simulation** (with or without a window).
- The user asks to **make the duck do something** — stand, enable, run a
  skill, look somewhere, move, quack, or react to rules.
- The user wants to **watch** the duck (a screenshot of the MuJoCo window) or
  **record** what it senses.
- The user asks to **close the sim** / tear the stack down.

Do *not* use it to change the CLI's code — that is ordinary repo work. This
skill operates the shipped CLI; it does not modify it.

## Prerequisites

1. **Two sibling clones at the pinned commits** in
   [`docs/upstream-pins.md`](../../../docs/upstream-pins.md):
   `pollen-robotics/microduck` (branch `sim-remote-io`) and
   `pollen-robotics/microduck_rl` (branch `develop`). Re-pinning is a
   deliberate, separate act — never bump a clone to drive the duck.
2. **`microduck env doctor` must be healthy.** Run it first, every time:

   ```bash
   uv run microduck env doctor
   ```

   It exits `2` and names each failing check's remediation when the box is not
   ready (clones, cargo toolchain, built daemons, the `microduck_rl` venv with
   `onnxruntime`, a short-enough state dir, a free `duck-body` port, host
   class, optional credentials). Do not proceed on a `2`.
3. **Environment variables** — all optional, all with defaults:

   | Variable | Default | What it points at |
   |---|---|---|
   | `MICRODUCK_CLONE` | `../microduck` | the `microduck` checkout |
   | `DUCK_SIM_RL` | `../microduck_rl` | the `microduck_rl` checkout |
   | `DUCK_SIM_STATE` | `~/.cache/duck-sim` | pidfiles, sockets, engine state |

   The operator never types a socket path, a params file or an ORT path — the
   CLI derives all three.

## Open the simulation

```bash
uv run microduck env up --sim                 # MuJoCo body, window on the box's display
uv run microduck env up --sim --headless      # MuJoCo body, no window
uv run microduck env up --fake                # single-process stand-in, for sanity/tests
```

- `--sim` runs the real `duck-body` MuJoCo simulator behind `robotd --sim`
  (120 s health timeout). A **window** appears on the box's display — so when
  you are driving from a headless session, the display owner's `DISPLAY` and
  `XAUTHORITY` must be exported into the command's environment (see
  *Watch it* below), otherwise the body cannot open one.
- `--headless` is the same body with no window. Use it when nobody is
  watching; it is also the safe default over SSH.
- `--fake` is `robotd --fake`: no physics, no window, 60 s timeout. Use it for
  a sanity check or when the user just wants the verbs exercised.
- Add `--skip-build` when the daemons are already built.

Then stand the duck up and hand it to its policy:

```bash
uv run microduck duck init --apply     # power the joints, ramp to home (~8 s to stand)
uv run microduck duck enable --apply   # hand the robot to its policy — it holds a stance
```

`init` takes roughly **eight seconds** to finish standing. Do not judge the
duck before it has.

## Operate

```bash
uv run microduck duck health              # the robot's own verdict; exit 2 when unhealthy
uv run microduck duck version             # API version, daemon version, build revision
uv run microduck duck monitor --frames 5 --json   # N state frames, one JSON object each
```

**Look** — point the head at a trunk-frame point:

```bash
uv run microduck duck look --x 0.2 --y 0.4 --z -0.1 --apply
```

Pick a **visibly large** target. `--x 0.2 --y 0.4 --z -0.1` turns the head
about 60° — obvious in the window. Small offsets move the head a couple of
degrees and read as "nothing happened"; if the user asked to see the duck
look somewhere, use a big target.

**Skills** — the one-shot moves the daemon knows:

```bash
uv run microduck rules check --duck duck-a   # lists the skills this daemon actually has
uv run microduck duck do roulade --apply
```

Never guess a skill name. `rules check --duck` reads the live snapshot (from
`robot.subscribe` on API 16) and tells you what exists.

**Move** — drive at intent rate for a duration, then stop:

```bash
uv run microduck duck move --vx 0.15 --duration 3 --apply
```

**Say this before you run it:** at the pinned commits the walk network is
selected and the twist reaches the daemon, but its joint targets are static —
**the duck leans, it does not take steps.** Locomotion is *not* achieved at
this pin; see
[`docs/verification/2026-09-04-sim-bringup.md`](../../../docs/verification/2026-09-04-sim-bringup.md)
("Walking in the MuJoCo body — not achieved at this pin"). Promising a walk
and delivering a lean is the failure mode this paragraph exists to prevent.

**Voice and recording:**

```bash
uv run microduck duck quack                       # this robot's own voice
uv run microduck duck record > senses.jsonl       # pure JSONL on stdout
```

**Rules** — the data-only reaction layer and its 50 Hz tick engine:

```bash
uv run microduck rules list                       # merged config (shipped + overlay) by origin
uv run microduck rules check --rules ./my.toml --duck duck-a
uv run microduck rules intent look                # one intent through the ONE registry
uv run microduck rules engine run --duck duck-a --rules ./my.toml --apply --max-ticks 300
```

`rules engine run` is the **only** process that opens the duck's control
socket: connect → `hello` → `health` → `init` → `enable` → armed, each step
logged on stderr. Always bound an operator run with `--max-ticks N` (300 ticks
≈ 6 s at 50 Hz) unless the user asked for a long-running engine. On any
abnormal exit it releases what it energized; it never sends `robot.relax`.

**The human-driving gate.** While a human is driving — a recent `pad.report`,
`pad_active`, or `robot.remoteSessionActive` — the engine withholds every
motion channel it composed. robotd arbitrates nothing between clients, so if
someone picks up the pad the engine gets out of the way rather than fighting
for the socket. If the duck "ignores" the engine, check whether a pad is live
before debugging anything else.

**What `--apply` means.** Every verb that moves hardware goes through the same
gate: on a TTY it asks for confirmation; on a pipe **without** `--apply` it
prints a zero-side-effect dry-run plan and sends nothing; on a pipe **with**
`--apply` it proceeds (agent mode). So an agent driving this CLI must pass
`--apply` deliberately — and a command that printed a plan did *not* move the
duck.

## Watch it

The MuJoCo window lives on the box's graphical session, not in your terminal.
To capture it from a headless session, export the **display owner's** session
environment into the screenshot command — `DISPLAY`, `XAUTHORITY`, and the
session `DBUS_SESSION_BUS_ADDRESS` — then take the shot:

```bash
# Discover the display owner's environment rather than hard-coding it:
#   - the X display and cookie:  the desktop session's DISPLAY / XAUTHORITY
#   - the session bus:           tr '\0' '\n' < /proc/<gnome-shell-pid>/environ \
#                                  | grep '^DBUS_SESSION_BUS_ADDRESS='
export DISPLAY=<the session's display>
export XAUTHORITY=<the session's X authority file>
export DBUS_SESSION_BUS_ADDRESS=<the gnome-shell session bus>
gnome-screenshot -f /tmp/duck.png
```

Then read the PNG back. Two shots a few seconds apart are the honest way to
tell motion from a static pose (this is how the "no gait" finding above was
made). If no graphical session exists, say so and use `--headless` plus
`duck monitor --json` instead of pretending to have looked.

## Close the simulation

```bash
uv run microduck env down      # stop every process env up started
uv run microduck env status    # verify: nothing tracked is alive
```

`env down` reads each pidfile under the state directory, deletes it *before*
signalling, and signals a pid only when `/proc/<pid>/cmdline` still names the
binary that pidfile was written for. **Never kill by name** — no `pkill`, no
`killall`, no `kill $(pgrep robotd)`. Pids are recycled; a name-based kill can
take out something else entirely.

## Hard rules

- **Never `relax` a duck someone wants standing without saying what it does.**
  `duck relax` cuts power and the robot *collapses*. It is gated and wants
  `--yes`; if the user asks for it, state the consequence first.
- **Never send motion without `--apply` on a pipe** and then claim the duck
  moved. A dry-run plan is a plan, not an action — report it as one.
- **Tear down what you started.** If you brought the stack up for a task, take
  it down when the task ends: `env down`, then `env status` to prove it.
- **Leave the window up if the user is watching — and say so.** When the user
  is looking at the MuJoCo window, do not run `env down` at the end of a step;
  tell them the stack is still up and how to close it.
- **Do not smooth over what does not work.** Locomotion at this pin, the API-16
  method gaps (`robot.policies`, `policy.*` answer `-32601`), a missing display
  — report them as they are.

## Provenance

**First-party to `microduck-cli`.** Origin = this repo; it is *not* vendored
from guildmaster, and the cite-don't-import rule points the other way for it:
guildmaster may broadcast this skill to the AgentCulture mesh, and downstream
consumers cite this copy rather than editing their own. The canonical copy is
`.claude/skills/operate-microduck/SKILL.md` in `microduck-cli`; an agent in
another runtime copies it verbatim, or writes it from the recipe
`microduck learn` prints ("Authoring the operator skill"). Fixes belong here,
upstream — never patched locally in a consumer.
