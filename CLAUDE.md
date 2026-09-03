# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this repo actually is right now

`microduck-cli` is an **AgentCulture mesh agent** whose destination is
*controlling the MicroDuck robot — one CLI that any agent or human drives*
(`culture.yaml`, [README](README.md)).

The mesh-agent scaffold cloned from `culture-agent-template` is still the
chassis — an agent-first introspection CLI (`whoami`, `learn`, `explain`,
`overview`, `doctor`, `cli`), a mesh identity, the vendored guildmaster skill
kit, and a build/CI/deploy baseline. **The duck layer has started landing on top
of it**: `ipc/` (the pinned JSON-RPC wire table), `duck/` (socket addressing, the
motion gate), `env/` (train-host detection), `explain/` (per-noun catalogs), and
`behavior/` — the pure model, the sense snapshot, the rules schema, the rule
engine with its single admission registry, and the tick engine itself
(`engine.py`, `liveness.py`, `senselog.py`) — and `train/` (argv builders for
the microduck_rl lane, the smoke gate, the artifact ledger). `env/` also carries
`doctor.py`, `params.py` and `stack.py` (the sim stack lifecycle). Nothing here
drives real hardware yet: the socket client is landing in its own task, so every
module below the composition root is exercised through injected seams and the
in-process fake daemon in `tests/fake_robotd.py`.

Two things to internalize before touching anything:

- **The runtime agent prompt is `AGENTS.colleague.md`, not this file.**
  `culture.yaml` declares `backend: colleague`, and the backend→prompt-file map
  (see `doctor`) resolves `colleague` → `AGENTS.colleague.md`. This `CLAUDE.md`
  is guidance for *Claude Code working in the repo*; editing it does not change
  the mesh agent's runtime behavior.
- **Both console scripts install now.** `[project.scripts]` defines `microduck`
  *and* `microduck-cli` (t9 shipped both, the same fix `reachy-mini-cli` made), so
  the old "Failed to spawn" gotcha is history: `uv run microduck …`,
  `uv run microduck-cli …` and `python -m microduck_cli …` are all equivalent. The
  *internal* prog name stays `microduck-cli`, so `--help` text, error messages,
  `learn`/`explain` bodies and JSON payloads all say `microduck-cli` — keep it
  that way; the test assertions pin it.

## Common commands

```bash
uv sync                                              # create .venv, install runtime + dev deps
uv run microduck whoami                              # run the CLI (note: 'microduck')
uv run pytest -n auto                                # full suite (xdist parallel)
uv run pytest tests/test_cli.py::test_whoami_text    # a single test
uv run pytest -n auto --cov=microduck_cli --cov-report=term   # coverage (CI gate: fail_under=60)
uv run teken cli doctor . --strict                   # the agent-first rubric gate CI enforces
```

Lint stack (the CI `lint` job runs all of these; line length is 100 everywhere):

```bash
uv run black --check microduck_cli tests
uv run isort --check-only microduck_cli tests
uv run flake8 microduck_cli tests
uv run bandit -c pyproject.toml -r microduck_cli     # B101/B404/B603 skipped in pyproject
markdownlint-cli2 "**/*.md" "#node_modules" "#.local" "#.claude/skills" "#.teken"
```

## Architecture: the agent-first CLI

Everything routes through `microduck_cli/cli/__init__.py:main()` → `_build_parser()`.
Three cross-cutting contracts are enforced by tests and by the rubric gate.

- **Adding a verb.** Write `cli/_commands/<verb>.py` exposing `register(sub)`
  (add `--json`, `set_defaults(func=…)`), then add one import + call inside
  `_build_parser()`. That is the only wiring step; `whoami.py` is the canonical
  example. For a **noun group** (a subcommand with its own verbs — how duck
  control will land, e.g. `microduck duck walk`), mirror `_commands/cli.py`:
  build the child subparsers with `parser_class=type(p)` so nested parse errors
  keep the structured error contract instead of argparse's default exit-2. A noun
  with action-verbs must also expose its own `overview` (rubric requirement).
- **Error contract** (`cli/_errors.py` + `_dispatch`/`_CliArgumentParser`).
  Every failure raises `CliError(code, message, remediation)`; `_dispatch` catches
  it and wraps *any* other exception, so no Python traceback ever leaks. Argparse
  errors route through `_CliArgumentParser.error()` too — and because parse errors
  fire before `args.json` exists, `main()` pre-scans raw argv for `--json` into the
  class-level `_json_hint`. **Handlers raise `CliError` — never `sys.exit`, never
  print-and-return.** Text errors are always two lines: `error: …` then `hint: …`
  (the `hint:` prefix is rubric-required).
- **Output contract** (`cli/_output.py`). Results → stdout, errors and diagnostics
  → stderr, **never mixed**, in text and JSON mode alike. Use `emit_result` /
  `emit_error` / `emit_diagnostic`, not `print`. Exit codes: `0` success, `1`
  user-input error, `2` environment error, `3+` reserved (constants in `_errors.py`).
- **The explain catalog** (`microduck_cli/explain/catalog.py`). `ENTRIES` is keyed
  by command-path tuples (`("whoami",)`, `("cli","overview")`; `()`,
  `("microduck-cli",)` and `("microduck",)` all resolve to root).
  `test_every_catalog_path_resolves` asserts each *existing* entry renders, but
  nothing forces a *new* verb to have one — so adding a verb means updating
  **three places in lockstep** or the docs silently drift: the catalog entry, the
  `_VERBS` list in `overview.py`, and the `_TEXT` + `_as_json_payload` blocks in
  `learn.py`.
- **Identity** (`_commands/whoami.py`). `culture.yaml` is hand-parsed line by line
  (no YAML dependency, to keep runtime deps empty) — only the documented flat
  `suffix`/`backend`/`model` shape is understood. `find_culture_yaml()` walks up
  from `__file__`, so identity is the agent's own even when invoked elsewhere; a
  wheel install (no `culture.yaml` beside the package) falls back to literal
  defaults and `doctor` reports a single info check.

### The packages beside `cli/`

Engine logic lives in a sibling package and `cli/_commands/` stays thin argparse
wiring (reachy-mini-cli's split). What is on disk today:

| Package | What it holds |
|---|---|
| `microduck_cli/ipc/` | `proto.py` — the duck-ipc-proto wire table, transcribed from the pinned commit in `docs/upstream-pins.md`; `client.py` — the JSON-RPC socket client (`RobotClient`, `RpcError`). |
| `microduck_cli/duck/` | `addressing.py` (name → sockets, pure: env and `listdir` injected), `gate.py` (the arm101-style TTY / dry-run / `--apply` motion gate, `Consent`/`consent()`/`render_dry_run()`), `record.py` (the JSONL sense recorder, `Recorder`, shared schema with replay). |
| `microduck_cli/env/` | `hosts.py` (train-host detection), `doctor.py` (rubric-shaped environment report), `params.py` (generated robotd params for a laptop run), `stack.py` (sim stack up/down/status, pid-by-cmdline). |
| `microduck_cli/train/` | `lane.py` (pure argv builders for `list-envs`, the 64-env smoke test, `train`, `play`, `export`, `publish`, `infer`, plus the smoke-gate record that refuses a long run without a passed smoke) and `artifacts.py` (append-only JSONL ledger). Never imports the RL package. |
| `microduck_cli/behavior/` | The engine and everything it composes — see the next section. |
| `microduck_cli/explain/` | `catalog.py` plus one module per noun (`duck`, `env`, `policy`, `rules`). |

### The agent-first rubric (why some code looks odd)

`teken cli doctor . --strict` gates CI on a seven-bundle rubric. Several shapes
exist only to satisfy it — don't "simplify" them away:

- `learn` must be ≥200 chars and mention purpose, command map, exit codes,
  `--json`, and `explain`.
- Any noun with action-verbs must also expose `overview` — the entire reason the
  `cli` noun group exists (`cli overview` describes the CLI; the global `overview`
  describes the *agent*).
- Descriptive verbs must never hard-fail on a bad path — hence `overview` takes an
  ignored positional `target` and still exits 0.

Separate from the in-package `microduck doctor`, which checks **agent-identity
invariants**: `prompt-file-present`, `backend-consistency` (`claude`→`CLAUDE.md`,
`colleague`→`AGENTS.colleague.md`, `acp`→`AGENTS.md`, `gemini`→`GEMINI.md`), and
`skills-present`. Change the backend in `culture.yaml` and you must teach `doctor`
the matching prompt file.

## Architecture: the behaviour engine

`microduck_cli/behavior/` is the duck's runtime. Everything below describes code
that is on disk; nothing here is a roadmap entry.

- **`model.py` / `sense.py` / `rules.py`** — pure value objects and pure
  functions: channels + `StopClass` priorities + `Lifetime` + `arbitrate` /
  `admit`; the frozen `Sense` snapshot and the `SenseProviders` peek seam; the
  data-only rules schema (two-layer merge: shipped + box-local overlay, by id).
- **`engine.py`** — `Engine(sink, providers, clock=…, sleep=…, hz=50)` and its
  loop. Per tick, in this order: read ONE `Sense`; ask each live behaviour for its
  contribution once; `arbitrate` a single owner per channel; compose the pose;
  **write it through the sink exactly once**; call `tick_seam(ctx)` **exactly once,
  after the write**; expire finished lifetimes; sleep to an absolute deadline.
  `TickContext` is what a rider gets (`now`, `tick`, `sense`, `active`,
  `ownership`, `pose`, `emit`, `admit`, `evict`, `active_names`). `TickBus` is the
  ordered, fault-isolating fan-out. `TickMetrics` wraps the whole tick and reports
  `overruns` / `max_tick_ms` / `achieved_hz`.
- **`sink.py`** — the `TargetSink` that turns one composed pose into robotd wire
  traffic and nothing else: continuous channels go as notifications, discrete
  ones as requests (per `microduck_cli.ipc.proto.is_notification`), and it owns
  the params encoding `behavior/intents.py` deliberately refuses to invent.
- **`intents.py`** — `Intent` (kind, payload, provenance) and the ONE
  `KindRegistry`/`default_registry()` every submission path validates through —
  a rule firing and `rules intent` are the same act, judged identically.
- **`rule_engine.py`** — `RuleEngine`: react/inhibit evaluation against one
  `Sense` snapshot, answering what fires and what doesn't (and why, as a `Drop`
  in `TickResult`) each tick. No I/O.
- **`human_gate.py`** — while a human is driving (a recent `pad.report`,
  `pad_active`, or `robot.remoteSessionActive`) every MOTION channel the engine
  composed is withheld — robotd arbitrates nothing between clients, so the
  engine gets out of the way instead of fighting the pad for the socket.
- **`idle.py`** — the resting layer: small, slow head motion plus an occasional
  chirp (`StopClass.PASSIVE`), so the duck reads as "alive, not driving" rather
  than "off" between commands; hands a channel back the instant anything else
  claims it.
- **`skills.py`** — a `SkillsSnapshot` (what the duck can actually run) built
  from `robot.subscribe` (API 16, deviation d1) or `robot.policies` (API ≥ 18),
  both normalised to one shape so a caller never needs to know which daemon it
  is talking to.
- **`defaults.py`** — reads `microduck_cli/behavior/default_rules.toml` (shipped
  inside the wheel via `importlib.resources`) through the same
  `RulesConfig.from_dict` gate every operator overlay goes through.
- **`replay.py`** — the offline half of the rule engine: drives `RuleEngine`
  tick by tick over a recorded JSONL sense stream with a fake, record-driven
  clock — no socket, no sleep, fully reproducible.
- **`release.py`** — own what you energize: on any abnormal exit sends
  `robot.stop`, `robot.pose {"active": false}`, `robot.mouth {"open": 0}` and
  `robot.sound {"hold": false}`, each independently so one failure doesn't abort
  the rest; never `robot.relax` (a duck with no torque falls); sends nothing on
  a clean exit so a deliberate hold survives.
- **`compose.py`** — the ONE composition root: `build_runtime()` wires one
  `RobotClient`, the one `KindRegistry`, and the tick-seam riders
  (`human_gate`, `idle`, the rule engine, the intent spool) into a running
  duck; `cli/_commands/rules.py` is thin argparse wiring on top of it.
- **`liveness.py`** — the `state.json` heartbeat (`Heartbeat.beat`, temp file +
  `os.replace`) and `refuse_if_engine_live()`.
- **`senselog.py`** — the `microduck.sense` logger, the fixed
  `[SENSE stage=… source=… event=…] detail` line, and `install_logging()`.

The `rules` noun (`cli/_commands/rules.py`) drives `compose.py` through its
`engine` sub-noun's four verbs — `run` (foreground, gated: connect → hello →
health → init → enable → armed, each step logged), `start` (a detached `engine
run --apply`, liveness is the heartbeat alone — no pidfile), `stop` (SIGTERM
after a `/proc/<pid>/cmdline` identity check), `status` (heartbeat freshness +
pid liveness + tick rate + daemon reachability) — plus `rules intent <kind>`,
which submits one intent through the same `KindRegistry`: with an engine live it
is appended to `<state>/intents.jsonl` (the spool the engine drains on its next
tick) and the verb waits up to 2s for the engine's acknowledgement in
`<state>/intents.log`; with no engine live it is validated and the would-be
admission is printed, sending nothing.

Four rules the code enforces and a change must keep:

- **ONE tick seam, never a second process.** A duck has one control socket and
  robotd arbitrates nothing between clients, so a second process is two authors
  fighting over every channel. Riders compose onto `tick_seam` via `TickBus`;
  reachy-mini-cli deleted two standalone sense nouns to learn this.
- **Drop, don't block, and never silently.** A provider that raises degrades to
  `None`; a seam driver that raises is caught, counted as a named `TickFault` and
  logged as a `tick-driver-fault` drop while its siblings still run. The engine
  never dies from a consumer — and never no-ops invisibly either: every drop gets
  a named reason on `microduck.sense`, stderr-only, so stdout stays pure JSONL.
- **A heartbeat, not a flag file.** A flag cannot expire and a `SIGKILL`ed writer
  locks the operator out forever. `refuse_if_engine_live()` refuses (exit 1,
  "engine live") only when the stamp is fresh **and** its pid is alive; stale,
  absent or corrupt all mean "no evidence", are reported, and let the next engine
  start.
- **No wall-clock read anywhere in the loop.** `clock` and `sleep` are injected,
  which is what makes a 500-tick run bit-for-bit reproducible in a test. An
  overrunning tick is counted and sleeps zero — it is never compensated for by
  skipping the seam, because a skipped seam is a rule that silently did not run.

## The three sibling repos, and what to take from each

The duck domain is meant to be built by *composing* these, not by inventing a
fourth architecture. Read the named file before you start the corresponding work.

### `../neurosymbolic-system` — the later home, not a current dependency

The description says microduck-cli is "built on the neurosymbolic-system runtime":
senses, rules, arbitration and motion composed onto one 50 Hz tick, extracted from
reachy-mini-cli and imported as a library by robot CLIs. **That library does not
exist yet.** `neurosymbolic-system` 0.7.0 is the *same* bare template scaffold this
repo started as — `neurosymbolic_system/` contains only `cli/` and `explain/`, no
runtime modules.

**Decision c20 (recorded here in the first engine PR): the engine lives in
`microduck_cli/behavior/` now, and it is built extraction-first.** Blocking the
duck on a library nobody has written would have blocked the whole repo; the
alternative — write the tick here as if it will never move — would have made the
extraction impossible later. So it is written *behind the seams the extraction
needs*, and `neurosymbolic-system` gets extracted later from reachy-mini-cli plus
this engine, not written from scratch. The seams, all six of them load-bearing:

- **`TargetSink`** — a `write(pose)` Protocol. The only way a pose leaves the
  engine, and the only thing a transport has to satisfy.
- **`SenseProviders`** — peek callables in, `Sense` out. The only way a reading
  gets in.
- **`tick_seam`** — ONE per-tick integration point (`TickBus` fans it out). Every
  rider composes onto it.
- **Rules as data**, never code (`behavior/rules.py`), so an operator's overlay
  can migrate to a versioned schema upstream.
- **One admission registry** for rule-fired and CLI-injected intents alike.
- **Heartbeat liveness** (`behavior/liveness.py`), not a flag file.

The consequences of that decision, in the order a reviewer meets them:

- An engine loop, arbitration, or a sense driver in `microduck_cli/behavior/` is
  **expected**, not a defect to reject. What *is* a defect is a seam violation:
  nothing under `behavior/` may import a transport, an SDK, or a CLI module other
  than `cli/_errors` (and even that import is on the extraction bill — see
  `liveness.py`).
- `neurosymbolic-system` is still **not** a dependency and must not become one
  until it ships runtime modules; adding it is an explicit decision, not a
  drive-by (see Hard constraints).
- When the extraction happens, it is a *move*: the same seams, the same tests.
  Anything that would make a module hard to lift out — a hidden global, a
  wall-clock read in the loop, a CLI error type raised three layers down — is
  worth fixing now rather than at extraction time.

### `../reachy-mini-cli` — the architecture to follow

The mature robot CLI in the family (`reachy/`, ~2000-line `CLAUDE.md`). Its
`CLAUDE.md` "Architecture: the agent-first CLI" and "Noun internals" sections are
the reference. **Its ONE-tick-seam lesson has landed here as `microduck_cli/behavior/`**
(`engine.py` + `TickBus` + `compose.py`, the "Architecture: the behaviour engine"
section above) — read the load-bearing lessons below as "why it looks like this",
not as a roadmap:

- **One noun per capability, each with `overview`**, engine logic in a sibling
  package (`reachy/behavior/`, `reachy/motion/`…) and only argparse wiring in
  `_commands/`. Kept here: `microduck_cli/cli/_commands/` stays thin, `behavior/`
  holds every loop and composition decision.
- **The single-SDK-owner model.** The hardware exposes one client and one head;
  every sense process contends for them, and the loser throttles. So **compose
  senses onto ONE tick seam, never as two processes** (`_compose_run_seam` there,
  `compose.build_runtime()` + `TickBus` here). Two standalone sense nouns were
  deleted upstream for exactly this reason — the mistake was not recreated in
  duck form: `rules engine run` is the only process that ever opens the duck's
  control socket.
- **Don't arbitrate across processes on a flag file.** A flag cannot expire; a
  heartbeat in `state.json` can. `behavior/liveness.py`'s `refuse_if_engine_live()`
  refuses early instead of running a silently useless second process.
- **Sense-stage logging.** A named drop reason (`self-mute`, `cooldown`,
  `audio-muted`… there; `tick-driver-fault` and friends here) on a dedicated
  logger, stderr-only, so JSONL export on stdout stays pure. A layer whose drops
  are invisible is indistinguishable from one that silently no-ops.

### `../arm101-cli` — the hardware-safety patterns at this maturity

The closest peer: same template, same half-rename gotcha, a real hardware layer
grown on top (`arm101/hardware/`, `arm101/explore/`). **Both its shipped
disciplines have landed here, in `microduck_cli/duck/gate.py` and
`microduck_cli/behavior/release.py`:**

- **Gated motion.** Every verb that moves hardware confirms on a TTY, prints a
  zero-side-effect dry-run plan on a non-TTY without `--apply`, and proceeds on a
  non-TTY *with* `--apply` (agent mode) — `duck/gate.py`'s `consent()` and
  `Consent` tri-state, driving every gated `duck`/`policy`/`rules engine` verb.
- **Own what you energize.** Release on any abnormal exit (exception, bus fault,
  Ctrl-C), each actuator released independently so one failure doesn't abort the
  rest; leave torque untouched on a *clean* exit so a deliberate hold survives —
  `behavior/release.py`'s four independent sends, run from `rules engine run`'s
  exit path. State the limits you cannot cover (a bus that is physically gone)
  rather than implying safety you don't have.
- **Hardware deps go in an extra, lazy-imported** (`[seeed]` there), so the base
  install stays zero-dep and introspection works on a box with no robot attached.
  Not yet needed here — the duck layer speaks JSON-RPC over a unix socket, no
  hardware SDK to lazy-import — but the same discipline applies the moment one
  is.

## Hard constraints

- **Zero third-party runtime dependencies** (`dependencies = []`) — on purpose.
  `teken`, pytest and the lint stack are dev-only. Keep it that way unless the duck
  layer genuinely needs a hardware library, and if it does, put it behind an extra
  with a lazy import so the introspection CLI still imports clean on a bare box.
  Adding a *base* dep (including `neurosymbolic-system`, once it exists) is an
  explicit decision, not a drive-by.
- **Python ≥ 3.12** (`X | None`, `tomllib`).
- **Every PR bumps the version — even docs/config/CI-only changes.** The
  `version-check` job in `.github/workflows/tests.yml` compares `pyproject.toml`
  against `origin/main` and fails the PR when they match (a duplicate version would
  fail the PyPI publish on merge). Use the `version-bump` skill; it also prepends
  the Keep-a-Changelog entry.

## CI / release

- `.github/workflows/tests.yml`: `test` (pytest + coverage + SonarCloud), `lint`
  (the stack above + the rubric gate), `version-check` (PR-only).
- SonarCloud gates the `test` job (`sonar-project.properties`,
  `sonar.qualitygate.wait=true`) — but only when `SONAR_TOKEN` is set; token-less
  repos and fork PRs skip the scan and stay green. `coverage.run.relative_files =
  true` is load-bearing: without it `coverage.xml` paths don't map to
  `sonar.sources=microduck_cli` and coverage reports 0%.
- `publish.yml`: PyPI Trusted Publishing over OIDC — push to `main` → PyPI,
  same-repo PR → a `.devN` build to TestPyPI. No stored credentials.

## Skills (`.claude/skills/`) — cite-don't-import

The kit is **vendored** from `guildmaster` (a few from `colleague`/`devague`);
`docs/skill-sources.md` is the authoritative provenance ledger and holds the
per-skill re-sync procedure. **Do not hand-edit skill script bodies** — lift real
changes upstream and re-vendor; a copy that diverges silently is the failure mode
that rule exists to prevent. The sanctioned local edits are (a) consumer-identifying
prose in `SKILL.md` and (b) adding `type: command` to the frontmatter (load-bearing:
the culture backend's `core.skill_loader` silently skips any `SKILL.md` without it).
Every divergence beyond that gets a row in the ledger — `cicd`'s repo-specific
pre-PR steps and `ask-colleague`'s direct-from-colleague vendoring are the tracked
ones today. There are **no first-party skills here yet**; when you add one (a duck
wrapper would be the natural first, as `find-reachy` is in reachy-mini-cli), say so
in this section — otherwise a reviewer can't tell "ours" from "vendored".

Day to day: **`cicd`** (the PR lane; needs `devex` ≥0.21 on PATH),
**`communicate`** (cross-repo issues + mesh messages; needs `agtag`; posts auto-sign
`- microduck-cli (Claude)`), **`version-bump`**, **`run-tests`**, **`sonarclaude`**,
**`ask-colleague`**, and the devague chain (`scope` → `think` → `challenge` →
`spec-to-plan` → `assign-to-workforce` → `validate-delivery` → `summarize-delivery`,
with `deviate` as the mid-run escape hatch).

Reach for **`ask-colleague`** reflexively for a diverse second opinion —
`review`/`explore` are read-only and always safe; side-effecting `write --apply` /
`--pr` needs the user's go-ahead.

## Conventions and workflow

**Git worktrees live in `../.worktrees.microduck-cli/<name>/`.** ALL worktrees of
this repo, without exception — workforce fan-out lanes, `ask-colleague` throwaways,
scratch checkouts:

```bash
git worktree add ../.worktrees.microduck-cli/<name> -b <branch>
```

Never `/tmp`, never a shared `../worktrees/`. This workspace holds many sibling
projects, and a generic shared folder accumulates orphaned trees from several repos
with nothing indicating ownership — a stale-tree sweep can't tell a live lane from
junk. Use a branch prefix scoped to the work (`duck/t2`, not plain `agent/t2`, which
collides with leftovers from earlier fan-outs and makes `git worktree add -b` fail).
Remove with `git worktree remove <path>`; `git worktree prune` only clears metadata.
The vendored `assign-to-workforce` skill's fan-out example uses the shared path *and*
`agent/<task-id>` branches — it is cited verbatim and must not be edited, so override
both when following it.

**Memory discipline — recall before, remember after.** This repo's eidetic memory is
**in-repo and public**: records resolve to `<repo-root>/.eidetic/memory` (committed,
shared with the team and mesh peers — the `claude` and `colleague` backends read the
same `microduck-cli` scope). Note the `/remember` and `/recall` skill *descriptions*
still claim a private `~/.eidetic` default; the vendored wrappers here default to
`--visibility public`, and the script is what runs.

- **`/recall` before you start** a non-trivial task — prior decisions, gotchas,
  "have we done this before?" — so you build on what's known instead of re-deriving it.
- **`/remember` when something worth keeping surfaces** — a decision and its
  rationale, a constraint, a fix and *why*, a gotcha that cost time. Capture it as it
  happens.

Pass `--visibility private` to keep a record in `$HOME` (uncommitted); `/recall` reads
both stores and merges. In-repo routing needs `eidetic >= 0.10.0`. Don't store what the
repo already records (code structure, git history, this file, `CHANGELOG.md`) — store
what you'd have to re-derive.

**PR flow.** Branch (`fix/…`, `feat/…`, `docs/…`, `skill/…`), implement, bump the
version, then go straight to `workflow.sh open` — in AgentCulture the standing default
is always "push and create a Pull Request", no interactive merge/keep/discard menu.
Signatures resolve from `culture.yaml` via `devex`; don't hand-sign inside `cicd`.
