"""Explain entries and verb list for the ``rules`` noun.

Owned by the ``rules`` noun task: adding a ``rules`` verb means editing this
module (``VERBS`` + ``ENTRIES``), ``cli/_commands/rules.py`` and
``tests/test_rules_cli.py`` — nothing else. See :mod:`microduck_cli.explain.env`
for the shared conventions.

``rules engine`` is a noun of its own (it has action verbs, so the agent-first
rubric requires it to answer ``overview``), which is why both it and each of its
four verbs carry an entry here.
"""

from __future__ import annotations

_CHEATSHEET_URL = "https://github.com/pollen-robotics/microduck/blob/main/docs/robot/cheatsheet.md"
_DUCKCTL_URL = (
    "https://github.com/pollen-robotics/microduck/blob/sim-remote-io/docs/robot/duckctl.md"
)

VERBS: list[str] = [
    "rules overview — describe the rules noun (the data-only rules layer and its engine)",
    "rules list — render the merged rules config (shipped defaults + box-local overlay) by origin",
    "rules check — validate the rules, their action names against a skills snapshot, and a replay",
    "rules engine — the tick-engine sub-noun (run, start, stop, status)",
    "rules engine overview — describe the engine sub-noun, its verbs and its start sequence",
    "rules engine run — run the engine in the foreground: connect, hello, health, init,"
    " enable, armed",
    "rules engine start — spawn a detached 'engine run --apply'; the heartbeat is the liveness",
    "rules engine stop — SIGTERM the engine the heartbeat names, after a pid-cmdline check",
    "rules engine status — heartbeat freshness, pid liveness, tick rate, daemon reachability",
    "rules intent <kind> — submit one intent through the ONE registry (spooled to a live engine)",
]

_RULES = f"""\
# microduck-cli rules

Noun group for the *data-only rules layer*: reactions and inhibitions declared
as data (events → rules → actions), merged from a shipped set plus a local
overlay, and evaluated by the rule engine on the ONE 50 Hz tick.

Two layers, merged per rule `id`:

- the **shipped defaults** inside the wheel
  (`microduck_cli/behavior/default_rules.toml`) — small on purpose, only what is
  obviously correct in an arbitrary room;
- a **box-local overlay** at `<state>/rules.toml` (or `--rules PATH`). An overlay
  entry with a shipped `id` replaces it wholesale, an overlay-only id is
  appended, and `enabled = false` tombstones the shipped rule of that id.

A rule never builds a behaviour itself: it submits an intent through the ONE
registry, the same call `rules intent` makes, so a rule-fired and a
hand-injected refusal are byte-identical.

## Usage

    microduck-cli rules
    microduck-cli rules list --json
    microduck-cli rules check --skills snapshot.json --replay session.jsonl
    microduck-cli rules engine run --duck duck-a --apply
    microduck-cli rules intent move --payload '{{"vx": 0.1}}'

## See also

- `microduck-cli explain rules engine`
- Upstream control surface: {_DUCKCTL_URL}
"""

_RULES_OVERVIEW = """\
# microduck-cli rules overview

Read-only description of the `rules` noun: what it holds (the data-only rules
layer, its two-layer merge, and the engine that evaluates it) and which verbs
exist. Descriptive, so it never hard-fails — a stray positional argument is
accepted and ignored.

## Usage

    microduck-cli rules overview
    microduck-cli rules overview --json
"""

_RULES_LIST = f"""\
# microduck-cli rules list

Renders the MERGED rules config — the shipped defaults with the box-local
overlay layered over them — one line per rule: `id`, kind (`react`/`inhibit`),
the predicate, the action (or the disabled set), the cooldown, and the ORIGIN
each rule came from:

- `shipped` — from the packaged `default_rules.toml`;
- `overlay` — contributed or replaced by `<state>/rules.toml` (or `--rules`);
- `tombstoned` — an id the overlay disabled with `enabled = false`, listed so a
  missing shipped rule is visible rather than silently absent.

Read-only: no socket, no daemon, no write.

## Usage

    microduck-cli rules list
    microduck-cli rules list --rules ./my-rules.toml
    microduck-cli rules list --json

## See also

- Policies and skills upstream: {_CHEATSHEET_URL}
"""

_RULES_CHECK = f"""\
# microduck-cli rules check

Three checks, none of which touches the robot's motion:

1. **Content** — the merged config goes through the one validation gate
   (`RulesConfig.from_dict`). A problem is reported NAMING the offending rule id
   and the command still **exits 0**: `check` is descriptive, and a descriptive
   verb never hard-fails on content.
2. **Action names** — every `do` rule's skill is checked against a skills
   snapshot: `--skills SNAPSHOT.json` when given, else a live duck
   (`--duck`/`--socket`), else skipped with a diagnostic on stderr. On the pinned
   daemon (API 16) the skills come from `robot.subscribe`; from API 18 on they
   come from `robot.policies`. A miss reads `rule '<id>': <skill> not in [...]`.
3. **Replay** — with `--replay RECORD.jsonl` the rules are run over a recorded
   sense stream offline: no socket, no clock, no sleep. Text prints the summary
   (ticks, fires, drops by reason, inhibited actions); `--json` carries every
   tick.

A broken *invocation* — an unreadable or non-JSONL `--replay` file — is still an
error (exit 1). Only rule CONTENT is reported without failing.

## Usage

    microduck-cli rules check
    microduck-cli rules check --rules ./my-rules.toml --skills ./skills.json
    microduck-cli rules check --socket /run/duck-a.sock --json
    microduck-cli rules check --replay ./session.jsonl

## See also

- Skills and policy slots upstream: {_CHEATSHEET_URL}
"""

_RULES_ENGINE = f"""\
# microduck-cli rules engine

The tick engine: one process, one socket, one 50 Hz seam. Every rider — the
rules evaluator, the human-driving gate, the health poller, the intent spool —
composes onto that ONE seam. A second engine against the same duck is not
"degraded", it is two authors fighting over every channel, so a second run is
refused (exit 1, `engine live`) **before** any socket is opened.

Liveness is a HEARTBEAT (`<state>/state.json`), never a flag file: a flag cannot
expire and a killed engine would lock the operator out of their own duck
forever. A stale or absent stamp means "no evidence", is reported, and lets the
next engine start.

Verbs: `run` (foreground, gated), `start` (detached), `stop` (SIGTERM after a
pid-cmdline identity check), `status` (freshness, tick rate, daemon reach).

## Usage

    microduck-cli rules engine
    microduck-cli rules engine overview --json
    microduck-cli rules engine run --apply

## See also

- Power and driving upstream: {_CHEATSHEET_URL}
"""

_RULES_ENGINE_OVERVIEW = """\
# microduck-cli rules engine overview

Read-only description of the `rules engine` sub-noun: its purpose, its four
verbs, the six start steps (`connect`, `hello`, `health`, `init`, `enable`,
`armed`) and the one-engine-at-a-time rule. Descriptive, so a stray positional
argument is accepted and ignored.

## Usage

    microduck-cli rules engine overview
    microduck-cli rules engine overview --json
"""

_RULES_ENGINE_RUN = f"""\
# microduck-cli rules engine run

Runs the engine in the FOREGROUND against one duck. The start sequence, in
order, each logged as one `[SENSE stage=start ... event=<step>]` line on stderr:

    connect  open the duck's control socket
    hello    record the daemon's api_version (skew is reported, never refused)
    health   robot.health — an unhealthy duck refuses the run (exit 2)
    init     robot.init   — GATED
    enable   robot.enable {{"on": true}} — GATED
    armed    robot.subscribe, rules loaded, idle registered (unless --no-idle)

`init` and `enable` power and drive the robot, so they go through the motion
gate: on a TTY without `--apply` it confirms interactively; on a non-TTY without
`--apply` it prints a zero-side-effect plan of all six steps and **sends
nothing** — no socket is opened at all; with `--apply` it proceeds (agent mode).

The run owns what it energizes: any abnormal exit (an exception, Ctrl-C, a
SIGTERM) sends `robot.stop`, `robot.pose {{"active": false}}`, `robot.mouth
{{"open": 0}}` and `robot.sound {{"hold": false}}`, each independently, and
exits non-zero NAMING whichever of them did not land. `robot.relax` is never
sent — a duck with no torque falls. A clean exit sends nothing at all, so a
deliberate hold survives the verb that set it.

`--max-ticks N` stops after N ticks (what a test drives); `--hz` sets the tick
rate; `--no-idle` leaves the resting layer unregistered.

## Usage

    microduck-cli rules engine run --duck duck-a --apply
    microduck-cli rules engine run --socket /run/duck-a.sock --hz 50 --apply
    microduck-cli rules engine run --json

## See also

- `init` powers the joints and ramps to the home pose: {_CHEATSHEET_URL}
"""

_RULES_ENGINE_START = """\
# microduck-cli rules engine start

Spawns `rules engine run ... --apply` as a detached child with the same
addressing and rules arguments, and returns immediately with the child's pid.

It writes **nothing else**: there is no pidfile, because the engine's own
heartbeat (`<state>/state.json`) is the liveness record, and a second file that
cannot expire is exactly the failure mode the heartbeat exists to avoid.

The child's stdout and stderr go to `/dev/null` — run the engine in the
foreground when you want to watch the sense log.

## Usage

    microduck-cli rules engine start --duck duck-a
    microduck-cli rules engine start --json
"""

_RULES_ENGINE_STOP = """\
# microduck-cli rules engine stop

Reads the heartbeat, and signals the pid it names **only after**
`/proc/<pid>/cmdline` still contains `rules engine run`. A pid is not an
identity: pids are recycled, and signalling a recycled one has taken out an
unrelated login session in this family's upstream before. A mismatch is reported
as `stale` and is never signalled.

Outcomes: `signalled`, `stale`, `gone`, `nothing-to-stop`. All exit 0 — "there
was nothing to stop" is an answer, not a failure.

## Usage

    microduck-cli rules engine stop
    microduck-cli rules engine stop --state ~/.cache/duck-sim --json
"""

_RULES_ENGINE_STATUS = """\
# microduck-cli rules engine status

Reports whether an engine is driving this duck, from two INDEPENDENT facts that
are both required: the heartbeat is fresh, and its pid is still alive. Either
alone is not proof — a fresh stamp with a dead pid is the last beat of an engine
that died milliseconds ago, and a live pid with a stale stamp is a wedged or
unrelated process.

Also reports the last tick, the configured and achieved rate, the overrun count,
and whether the duck's daemon answers a `hello` probe at all.

## Usage

    microduck-cli rules engine status
    microduck-cli rules engine status --duck duck-a --json
"""

_RULES_INTENT = """\
# microduck-cli rules intent <kind>

Submits ONE intent — `do`, `look`, `move`, `sound`, `stop`, `mode`, `idle` —
through the same registry a rule fires through. Origin is a record, never an
input to judgement: an over-limit payload gets byte-identical refusal text
whether a rule, a human or an agent submitted it.

Two paths, and the verb picks by itself:

- **an engine is live** — the intent is appended to `<state>/intents.jsonl`, the
  spool the engine drains on its next tick, and this verb waits up to 2 seconds
  for the engine's acknowledgement in `<state>/intents.log`, then prints it.
- **no engine is live** — the intent is validated and the would-be admission is
  printed. Nothing is sent: with no engine there is nobody to compose it onto a
  tick, and a lone socket write would be a second author.

A refusal prints the registry's text VERBATIM and exits 1.

## Usage

    microduck-cli rules intent stop
    microduck-cli rules intent move --payload '{"vx": 0.1, "duration_s": 2}'
    microduck-cli rules intent do --payload '{"skill": "kick_left"}' --json
"""

ENTRIES: dict[tuple[str, ...], str] = {
    ("rules",): _RULES,
    ("rules", "overview"): _RULES_OVERVIEW,
    ("rules", "list"): _RULES_LIST,
    ("rules", "check"): _RULES_CHECK,
    ("rules", "engine"): _RULES_ENGINE,
    ("rules", "engine", "overview"): _RULES_ENGINE_OVERVIEW,
    ("rules", "engine", "run"): _RULES_ENGINE_RUN,
    ("rules", "engine", "start"): _RULES_ENGINE_START,
    ("rules", "engine", "stop"): _RULES_ENGINE_STOP,
    ("rules", "engine", "status"): _RULES_ENGINE_STATUS,
    ("rules", "intent"): _RULES_INTENT,
}
