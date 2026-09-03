"""Explain entries and verb list for the ``duck`` noun.

Owned by the ``duck`` noun task: adding a ``duck`` verb means editing this
module (``VERBS`` + ``ENTRIES``), ``cli/_commands/duck.py`` and
``tests/test_duck.py`` — nothing else. See :mod:`microduck_cli.explain.env` for
the shared conventions.

Every entry links the upstream page that owns the behaviour it describes.
"""

from __future__ import annotations

#: The upstream page every duck verb is answerable to, at the ref pinned in
#: ``docs/upstream-pins.md`` (``sim-remote-io`` @ ``0cd676d``).
CHEATSHEET = (
    "https://github.com/pollen-robotics/microduck/blob/sim-remote-io/docs/robot/cheatsheet.md"
)

VERBS: list[str] = [
    "duck overview — describe the duck noun (operate the duck in robotctl's words)",
    "duck health — the robot's own verdict on itself; exit 2 when it is not healthy",
    "duck version — API version, daemon version and build revision, from hello",
    "duck monitor — one line (or one NDJSON object) per robot.state frame",
    "duck init — power the joints and ramp to the home pose (gated; moves every joint)",
    "duck relax — cut power; the robot collapses (gated, and wants --yes)",
    "duck enable — hand the robot to its policy, or take it back (gated)",
    "duck do <skill> — run one skill, the request a gamepad button sends (gated)",
    "duck mode — read the drive mode; --set switches it (gated)",
    "duck look — point the camera at a trunk-frame point (gated)",
    "duck stop — zero the intents this client is sending; not an emergency stop",
    "duck move — drive at intent rate for a duration, then stop (gated)",
    "duck quack — play this robot's own voice, to tell ducks apart",
    "duck configure — print the params file this CLI generated for the duck (--list)",
    "duck record — record every sense the duck reports as JSONL on stdout",
]

_ADDRESSING = """\
## Addressing

Every verb takes the same three flags, resolved by `microduck_cli.duck.addressing`:

    --duck NAME      the duck to talk to (else $DUCK_SIM_DUCK, else the only one present)
    --socket PATH    an explicit control socket; wins over --duck
    --state DIR      where the sockets live (else $DUCK_SIM_STATE, else ~/.cache/duck-sim)

and `--json`. Results go to stdout, diagnostics and errors to stderr, never mixed.
"""

_GATE = f"""\
## The gate

This verb moves or powers the robot, so it goes through the motion gate:

- on a TTY without `--apply` it prints the plan and asks for confirmation;
- on a non-TTY without `--apply` it prints the plan and **sends nothing** (exit 0,
  and the socket is never even opened);
- with `--apply` it sends, on a TTY or not — the agent path.

The plan lists the exact JSON-RPC calls that would go out, and carries the
upstream safety sentence that applies to the verb.

Upstream: {CHEATSHEET}
"""

_UPSTREAM = f"""\
## Upstream

{CHEATSHEET}
"""

_DUCK = f"""\
# microduck-cli duck

Noun group for *operating the duck*: the direct-control surface, spoken in
robotctl's words and carried over robotd's JSON-RPC socket.

The verb names mirror `robotctl` at the pinned commit — its `RobotCommand` enum
(`init`, `enable`, `relax`, `do`, `mode`, `look`), its top-level namespace
(`health`, `version`, `monitor`, `quack`, `configure`) and the `robot.stop`
method it documents but exposes no subcommand for (`stop`). Two verbs are ours
and only ours: `move` (a bounded drive at intent rate — upstream drives that
from the gamepad and the browser console) and `record` (the JSONL recorder,
which is a recording tool rather than a robot command).

`init`, `relax`, `enable`, `do`, `move`, `mode --set` and `look` are gated.

{_ADDRESSING}
## Usage

    microduck-cli duck
    microduck-cli duck overview
    microduck-cli duck health --json
    microduck-cli duck init --apply

{_UPSTREAM}"""

_DUCK_OVERVIEW = f"""\
# microduck-cli duck overview

Read-only description of the `duck` noun: what it holds, which verbs are gated,
and the verb list itself. Descriptive, so it never hard-fails — a stray
positional argument is accepted and ignored.

## Usage

    microduck-cli duck overview
    microduck-cli duck overview --json

{_UPSTREAM}"""

_HEALTH = f"""\
# microduck-cli duck health

Asks `robot.health` and renders the robot's own verdict: the loop, the bus, the
IMU, and the battery and motor temperatures when they are measured (a bare
`robotd --fake` measures neither, and "not measured" is reported as such rather
than as zero).

**Exits 2 when the robot is not healthy**, mirroring `robotctl health`'s
non-zero exit, so a script can gate on it. The report is printed either way.

{_ADDRESSING}
## Usage

    microduck-cli duck health
    microduck-cli duck health --json --duck duck-a

{_UPSTREAM}"""

_VERSION = f"""\
# microduck-cli duck version

The `hello` handshake, reported: the daemon's `api_version`, its
`daemon_version` and its build `revision`, alongside the API version this CLI
speaks. Skew is *reported, never refused* — the daemon accepts our version and
we accept its; a verb that needs a method an older daemon lacks says so itself.

{_ADDRESSING}
## Usage

    microduck-cli duck version
    microduck-cli duck version --json

{_UPSTREAM}"""

_MONITOR = f"""\
# microduck-cli duck monitor

Subscribes to `robot.state` and prints one line per frame: the loop rate, the
policy, the fall and limp flags, and what was *requested* against what was
*applied* — the pair that makes "the stick is forward and the robot is still"
readable.

`--json` makes stdout pure NDJSON: one `robot.state` params object per line and
nothing else, so `| jq` and `> log` both behave. `--hz` asks the daemon to
decimate server-side. `--frames N` stops after N frames — this CLI's addition,
so an agent can bound a run without sending itself a signal; the default (0)
runs until Ctrl-C, which ends cleanly.

{_ADDRESSING}
## Usage

    microduck-cli duck monitor --hz 10
    microduck-cli duck monitor --json --frames 100 > frames.ndjson

{_UPSTREAM}"""

_INIT = f"""\
# microduck-cli duck init

`robot.init`: powers the joints and ramps to the home pose over about two
seconds. **It moves every joint** — have the robot on its stand, or hold it.
Needs no policy: a robot with no walking network can still stand.

{_GATE}
{_ADDRESSING}
## Usage

    microduck-cli duck init            # plan (non-TTY) or prompt (TTY)
    microduck-cli duck init --apply
"""

_RELAX = f"""\
# microduck-cli duck relax

`robot.relax`: cuts power to the joints. **The robot collapses** if nothing is
holding it, which is why it wants `--yes` — the same flag `robotctl robot relax`
carries. `--apply` without `--yes` is refused (exit 1) with the safety sentence.
`--yes` is an acknowledgement, not a shortcut: it never substitutes for the gate,
so a TTY still confirms and a non-TTY still only plans until `--apply` is given.

Not the same as `stop`: `stop` zeroes the intents and leaves the robot standing.

{_GATE}
{_ADDRESSING}
## Usage

    microduck-cli duck relax                 # plan / prompt
    microduck-cli duck relax --yes --apply
"""

_ENABLE = f"""\
# microduck-cli duck enable

`robot.enable`: hands the robot to its policy, or takes it back. This is the
gamepad's Start button, and the difference from `init` is the whole point —
`init` position-ramps to a pose with nothing balancing, `enable` gives the robot
to the policy, which then holds it up.

`--on` (the default), `--off`, or `--toggle` — toggle being what a client cannot
get right by remembering, because the robot's state moves without asking it.

{_GATE}
{_ADDRESSING}
## Usage

    microduck-cli duck enable --on --apply
    microduck-cli duck enable --toggle --apply
"""

_DO = f"""\
# microduck-cli duck do

`robot.do`: one one-shot skill — `ground-pick`, `kick-left`, `kick-right`,
`sit` (a toggle) or `roulade`. The same requests the gamepad's buttons send, for
a bench with no pad. The names are `robotctl`'s; the wire spells them in snake
case (`sit` is `sit_toggle`), and this verb translates.

The policy must be enabled and driving; a skill whose network is not on this
robot is refused with the robot's own reason, which this verb surfaces.

{_GATE}
{_ADDRESSING}
## Usage

    microduck-cli duck do roulade --apply
    microduck-cli duck do sit --json --apply
"""

_MODE = f"""\
# microduck-cli duck mode

With no argument: `robot.mode`, which drive mode this robotd runs — `walk` or
`roller`. Changes nothing, so it is not gated.

With `--set walk|roller`: `robot.setMode`, which does change the robot, so it
goes through the gate. A robot with no policy loaded has nothing to switch
between and refuses, with that as the reason.

{_GATE}
{_ADDRESSING}
## Usage

    microduck-cli duck mode --json
    microduck-cli duck mode --set roller --apply
"""

_LOOK = f"""\
# microduck-cli duck look

`robot.look`: point the camera at a trunk-frame point — X forward, Y left, Z up,
in metres. The daemon runs the gaze IK against its own robot model and moves the
head, so there are no sign conventions to remember. A point beyond the head's
reach gets the closest gaze the joints allow, and the daemon says so.

`--neck-pitch` is the neck posture to aim around, in radians; the IK holds it
rather than solving it.

{_GATE}
{_ADDRESSING}
## Usage

    microduck-cli duck look --x 1 --y 0 --z 0 --apply
    microduck-cli duck look --x 0.3 --y 0 --z -0.3 --apply
"""

_STOP = f"""\
# microduck-cli duck stop

`robot.stop`: zeroes the intents this client is sending. **It is not an
emergency stop** — nothing in this system cuts servo power from a client — and
the verb is a plain one for that reason. Ungated, because stopping is the safe
direction; `relax` is the one that cuts power.

{_ADDRESSING}
## Usage

    microduck-cli duck stop
    microduck-cli duck stop --json

{_UPSTREAM}"""

_MOVE = f"""\
# microduck-cli duck move

Drive for a bounded time, then stop. **This verb is ours, not `robotctl`'s** —
upstream drives `robot.move` from the gamepad and the browser console, so there
is no CLI subcommand to mirror.

`robot.move` is a *continuous* intent with a deadman behind it: one notification
decays, and the daemon stops the robot when the stream stops. So this sends
`robot.move` at intent rate (20 Hz) for `--duration` seconds, refreshing the
deadman, and then sends `robot.stop`. Ctrl-C stops too — the stop is in a
`finally`, so an interrupted drive never leaves the robot running.

{_GATE}
{_ADDRESSING}
## Usage

    microduck-cli duck move --vx 0.2 --duration 2 --apply
    microduck-cli duck move --vyaw 0.5 --duration 1 --json --apply
"""

_QUACK = f"""\
# microduck-cli duck quack

`robot.sound` with this robot's own voice — the loudest way to tell ducks apart.
Every voice bank is seeded from the SoC serial, so the duck that answers is the
one you are talking to. There is no `quack` tag on the wire: `robotctl quack`
sends `SoundTag::Chirp`, and so does this.

On the pinned API `robot.sound` is a continuous intent, so it goes as a
notification and there is no acceptance to read back. A daemon that does answer
has its refusal surfaced with the robot's own reason.

{_ADDRESSING}
## Usage

    microduck-cli duck quack
    microduck-cli duck quack --json

{_UPSTREAM}"""

_CONFIGURE = f"""\
# microduck-cli duck configure

`--list`: print the params file **this CLI generated** for the duck —
`<state-dir>/<duck>.toml`, as rendered by `microduck_cli.env.params` — or say
plainly that there is none.

Deliberately *not* `robotctl configure`, which edits the on-robot
`/etc/robot/robotd.toml` in a TUI. This CLI never reads or writes the robot's
own config; `tests/test_no_config_writes.py` enforces that. `--list` is the only
mode offered, and anything else is refused (exit 1) naming `robotctl configure`
as the tool for the job.

{_ADDRESSING}
## Usage

    microduck-cli duck configure --list
    microduck-cli duck configure --list --json --duck duck-a

{_UPSTREAM}"""

_RECORD = f"""\
# microduck-cli duck record

Record everything the duck reports, as JSONL, in arrival order. One JSON object
per line:

    {{"ts": <monotonic seconds>, "source": "state|health|pad|tof|remote|hello", "params": {{}}}}

`state` frames come from the subscription, `health` is polled at 2 Hz,
`remote` (`robot.remoteSessionActive`) at 1 Hz, and `pad`/`tof` arrive when
those streams are subscribed. `hello` is the first record of every recording.

**Stdout carries records and nothing else** — the summary and every named drop
go to stderr — so `microduck-cli duck record > run.jsonl` is a clean file.
`--out FILE` writes the records to a file instead and puts the summary on
stdout, where a result belongs. `--seconds` bounds the run; Ctrl-C ends it early
and still leaves a valid file.

The schema is `microduck_cli.duck.record.RECORD_SCHEMA`, shared with the replay
reader so the two cannot drift.

{_ADDRESSING}
## Usage

    microduck-cli duck record --seconds 10 > run.jsonl
    microduck-cli duck record --seconds 10 --out run.jsonl --json

{_UPSTREAM}"""

ENTRIES: dict[tuple[str, ...], str] = {
    ("duck",): _DUCK,
    ("duck", "overview"): _DUCK_OVERVIEW,
    ("duck", "health"): _HEALTH,
    ("duck", "version"): _VERSION,
    ("duck", "monitor"): _MONITOR,
    ("duck", "init"): _INIT,
    ("duck", "relax"): _RELAX,
    ("duck", "enable"): _ENABLE,
    ("duck", "do"): _DO,
    ("duck", "mode"): _MODE,
    ("duck", "look"): _LOOK,
    ("duck", "stop"): _STOP,
    ("duck", "move"): _MOVE,
    ("duck", "quack"): _QUACK,
    ("duck", "configure"): _CONFIGURE,
    ("duck", "record"): _RECORD,
}
