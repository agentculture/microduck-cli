# Build Plan — microduck-cli: env, teach, operate, rules

slug: `microduck-cli-env-teach-operate-rules` · status: `exported` · from frame: `microduck-cli-env-teach-operate-rules`

> microduck-cli sets up a MicroDuck environment (sim or real), trains and publishes policies through `microduck_rl`, drives the duck directly over robotd's JSON-RPC, and runs a data-only rules layer (events -> rules -> actions) — one CLI that any agent or human drives, implementing the upstream pollen-robotics docs and pointing users at them.

## Tasks

### t1 — IPC protocol table — `microduck_cli`/ipc/proto.py: method names, `API_VERSION`, `JOINT_NAMES` (15), `POLICY_OBS_LEN` 61 / `ACTION_LEN` 14, JSON-RPC + duck error codes, and a continuous-vs-discrete classification (notification vs request) per method, transcribed from duck-ipc-proto/src/lib.rs at a pinned commit

- instruction: Own only `microduck_cli`/ipc/proto.py and its tests; transcribe from the pinned duck-ipc-proto commit recorded in docs/, never from memory; keep the module stdlib-only.
- covers: c4, h3
- acceptance:
  - A table test asserts every constant against a fixture copied from the pinned duck-ipc-proto commit and fails if the fixture and the table disagree
  - `is_notification`(method) returns True for robot.move/head/pose/mouth/sound-hold and False for robot.do/look/stop/enable/init/relax/setMode/loadPolicy
  - The module imports nothing outside the stdlib
- obligation: `o1` (criterion 1) [ipc/proto.py vs the pinned duck-ipc-proto fixture] every method name, `API_VERSION`, `JOINT_NAMES` and obs/action length equals the fixture

### t2 — In-process fake robotd for tests — tests/`fake_robotd.py`: a unix-socket JSON-RPC 2.0 NDJSON server speaking the proto table (hello, robot.health, robot.subscribe/state stream at a configurable rate, robot.policies/skills, init/enable/relax/do/look/stop/move/pose/mouth/sound, pad.input tap, tof.frame) that records every call in order, can be told to refuse a method with a reason, answer `METHOD_NOT_FOUND`, report fallen, wedge, or delay replies

- instruction: Own tests/`fake_robotd.py` and tests/`test_fake_robotd.py` only. Model the wire on duck-ipc-proto: JSON-RPC 2.0, one object per line, notifications have no id. Bind each instance to a short path under tempfile.mkdtemp (assert < 100 bytes). Expose: `call_log` (ordered, with a 'kind' of request|notification), refuse(method, code, message), wedge(), delay(ms), `set_state`(fallen=..., skills=\[...\]), and a state stream at a configurable Hz. No third-party imports.
- covers: h1
- acceptance:
  - A test opens the fake, sends hello and gets `API_VERSION` back; an unknown method returns JSON-RPC -32601 with the method name
  - Call log preserves order and distinguishes notifications (no id) from requests (id) so later tests can assert exact sequences
  - wedge() makes the server stop reading; delay(ms) delays replies; both are used by the client tests
  - Safe under pytest -n auto: each instance binds its own short temp socket path (< 100 bytes)
- obligation: `o2` (criterion 2) [tests/`fake_robotd.py` call log] records every call in arrival order and marks notifications (no id) apart from requests

### t3 — Rules data model — `microduck_cli`/behavior/rules.py + tests/`test_rules.py`: `schema_version`, \[\[react\]\] / \[\[inhibit\]\] / \[modes\], Predicate as field/op/value, RulesConfig.`from_dict` as the single fail-closed gate, per-id two-layer merge with enabled=false tombstones, `cooldown_s` / hysteresis / bounded `duration_s`, duck `SENSE_FIELDS` and ACTION vocabulary placeholders

- instruction: Own `microduck_cli`/behavior/rules.py and tests/`test_rules.py`. Mirror reachy-mini-cli reachy/behavior/rules.py's `from_dict` gate and `merge_rules` semantics (read it, do not copy prose). Add `schema_version` = 1 as a required top-level int; refuse absent or unknown with the expected version in the message. Sense field and action name sets are frozen sets imported from behavior/sense.py once t4 lands — until then define them locally and mark with a TODO(t4) that t11 removes.
- covers: c12, h11, c42, h31
- acceptance:
  - `from_dict` refuses unknown field, callable value, unknown predicate field or op, negative cooldown, duplicate id, unbounded looping action, and names the rule id in every message
  - `merge_rules`: overlay entry with a matching id replaces wholesale keeping position; a new id appends; enabled=false disables the shipped id
  - A file without `schema_version` or with an unknown one is refused naming the expected version
  - Module imports nothing from the CLI, the IPC client, or any transport
- obligation: `o3` (criterion 1) [RulesConfig.`from_dict`] refuses each documented bad shape naming the rule id; never loads a partially valid file

### t4 — Behavior model and sense snapshot — `microduck_cli`/behavior/model.py + sense.py + tests: duck channels (twist, head, pose, mouth, sound, skill), StopClass priorities, Lifetime, Behavior spec + contribution fn; Sense snapshot (fallen, gravity, `battery_frac`, `hottest_servo_c`, `loop_hz`, `pad_active`, `remote_session`, `tof_nearest_m`, skills, \*`_age_s`) and SenseProviders as injected zero-arg peek callables degrading to None

- instruction: Own `microduck_cli`/behavior/model.py, sense.py and their tests. Channels are the duck's intent families: twist, head, pose, mouth, sound, skill. Sense fields are provisional (risk on this task): fallen, gravity (3), `battery_frac`, `hottest_servo_c`, `loop_hz`, `pad_active`, `remote_session`, `tof_nearest_m`, skills, plus \*`_age_s` for each — expect a rename pass after t18's first real recording. No I/O, no transport, no CLI imports.
- covers: c23
- acceptance:
  - A provider that raises, returns None, or is missing yields the same None field and never propagates
  - Arbitration test: a higher StopClass owns a contested channel; PASSIVE only fills an unclaimed one; UNSTOPPABLE/STOPPING block admission
  - Every sense field a rule predicate can name is declared once in a frozen set the rules module imports
- obligation: `o4` (criterion 1) [SenseProviders peek boundary] a raising, missing or None provider yields None on the snapshot and never propagates into the tick

### t5 — Motion gate helper — `microduck_cli`/duck/gate.py + tests: the arm101 tri-state (TTY prompt / non-TTY dry-run plan with zero sends / non-TTY --apply proceeds), plus the four upstream safety sentences as constants rendered verbatim into every dry-run plan

- instruction: Own `microduck_cli`/duck/gate.py and tests/`test_gate.py`. Copy the tri-state semantics from arm101-cli calibrate.py / `set_baudrate.py` (read, do not paste): TTY without --apply prompts; non-TTY without --apply prints a plan and performs zero sends; non-TTY with --apply proceeds. The four safety sentences are module constants quoted verbatim from microduck docs/robot/cheatsheet.md and duckctl.md. Use a pty in tests for the TTY branch.
- covers: c7, h8
- acceptance:
  - consent(args, plan) returns PROMPT on a TTY without --apply, `DRY_RUN` on a non-TTY without --apply, APPLY with --apply; a pty test and a non-TTY test cover all three
  - Dry-run output contains the exact strings for init (moves every joint, on its stand), relax (collapses), stop (not an emergency stop) and community policies (verified by nobody)
- obligation: `o5` (criterion 1) [duck/gate.py consent()] non-TTY without --apply performs zero sends; --apply proceeds; TTY prompts

### t6 — Duck addressing — `microduck_cli`/duck/addressing.py + tests: name -> <state>/<name>.sock and <name>-tof.sock, `DUCK_SIM_STATE` / `DUCK_SIM_DUCK` / --duck / --socket precedence, the 108-byte socket path check, and a 'no such duck' CliError that lists the sockets present in the state dir

- instruction: Own `microduck_cli`/duck/addressing.py and tests/`test_addressing.py`. Precedence: --socket > --duck/`DUCK_SIM_DUCK` + `DUCK_SIM_STATE` (default ~/.cache/duck-sim) > first \*.sock in the state dir. Return both <name>.sock and <name>-tof.sock. Raise CliError exit 2 with the sockets present when the name is unknown, and when a path exceeds 100 bytes.
- covers: c5, h6
- acceptance:
  - resolve('duck-b') returns the expected paths with no network or subprocess call (asserted by monkeypatching socket and subprocess)
  - A resolved path longer than 100 bytes raises CliError exit 2 naming the limit and `DUCK_SIM_STATE`
- obligation: `o6` (criterion 1) [duck/addressing.resolve()] resolves a name to socket paths with no network or subprocess call

### t7 — Static guard tests and CI wiring — tests/`test_zero_deps.py` (dependencies == \[\] and no extra names mjlab/warp-lang/torch/better-actuator-models), tests/`test_no_hardware_paths.py` (no serial/rustypot/dynamixel import, no /dev/tty open under `microduck_cli`/), tests/`test_no_config_writes.py` (no write-open of robotd.toml/updater.toml), tests/`test_no_secrets_in_output.py` scaffold (sentinel env values never appear in captured stdout/stderr/argv), and .markdownlint-cli2.yaml ignoring docs/specs/\*\* and docs/plans/\*\*

- instruction: Own the four guard tests, .markdownlint-cli2.yaml, and only the lines of .github/workflows/tests.yml you add. Each guard writes a temporary violating module under a tmp copy of `microduck_cli`/ to prove it fails. `test_no_secrets_in_output` exports `assert_no_secrets`(`captured_text`, `argv_list`) reused by t14/t15/t18/t19. Add docs/specs/\*\* and docs/plans/\*\* to markdownlint ignores; run markdownlint on both exported files.
- covers: c3, h5, c9, h10, c34, h25, c40, h29
- acceptance:
  - Each guard test fails when a fixture module violating it is added under `microduck_cli`/ (asserted by writing a temp module inside the test)
  - markdownlint-cli2 passes on the exported spec in docs/specs/ with the new ignore
  - The secrets test exposes a helper `assert_no_secrets`(captured, `argv_log`) that later noun tests reuse
- obligation: `o7` (criterion 1) [static guard tests] a violating module under `microduck_cli`/ fails the matching guard (deps, hardware paths, config writes, secrets)

### t8 — Host detection for the train lane — `microduck_cli`/env/hosts.py + tests: classify GB10 (DGX Spark), Jetson Thor, Jetson AGX Orin, other aarch64, `x86_64` and 'hf-jobs' from uname/nvidia-smi/JetPack markers (all injected for tests), and report whether `microduck_rl`'s torch source (cu129 index, linux-aarch64) applies

- instruction: Own `microduck_cli`/env/hosts.py and tests/`test_hosts.py`. Inputs (uname -m, nvidia-smi name, /etc/`nv_tegra_release`, CUDA version) are injected callables. Classes: gb10, jetson-thor, jetson-agx-orin, aarch64-other, `x86_64`, hf-jobs, unknown. torch-source verdict follows `microduck_rl` pyproject \[tool.uv.sources\] (cu129 index only on linux-aarch64) — cite the file in a comment.
- covers: c25, h16
- acceptance:
  - Fixtures for each of the five host classes produce the expected class name and torch-source verdict
  - An unknown host yields class 'unknown' with a remediation string, never an exception
- obligation: `o8` (criterion 1) [env/hosts.classify()] each of the five host fixtures yields its class and torch-source verdict

### t9 — Noun scaffolds and lockstep docs test — register env/duck/policy/rules noun groups in cli/`__init__.py` with overview verbs only, split the explain catalog into per-noun modules (explain/env.py, duck.py, policy.py, rules.py merged into ENTRIES), make overview.`_VERBS` and learn read the per-noun verb lists, and add tests/`test_lockstep.py` asserting every registered verb has a catalog entry, an overview line and a learn mention

- instruction: Own cli/`__init__.py`, the four `_commands`/<noun>.py stubs (overview only), explain/{env,duck,policy,rules}.py, explain/catalog.py (merge per-noun ENTRIES), `_commands`/overview.py, `_commands`/learn.py, tests/`test_lockstep.py`. Each noun uses `parser_class`=type(p) per cli.py. After this task, wave-2 noun tasks may only edit their own `_commands`/<noun>.py, explain/<noun>.py, tests/`test_`<noun>.py. Keep teken cli doctor . --strict green. Decide the console-script rename question with the user before opening this PR (risk on this task).
- covers: c16, h12
- acceptance:
  - teken cli doctor . --strict passes with the four empty nouns registered
  - Adding a verb to a noun without its catalog entry fails `test_lockstep` naming the verb
  - Later noun tasks only touch their own `_commands`/<noun>.py, explain/<noun>.py and tests/`test_`<noun>.py
- obligation: `o9` (criterion 2) [tests/`test_lockstep.py`] a registered verb without a catalog entry, overview line and learn mention fails the test naming the verb

### t10 — JSON-RPC client — `microduck_cli`/ipc/client.py + tests: writer thread draining a bounded queue, reader thread correlating replies by id and fanning notifications (robot.state, pad.input, tof.frame, update.progress) into peek slots, hello handshake that records `API_VERSION` and refuses on `JOINT_NAMES` / obs / action mismatch naming both numbers, per-request timeouts, and named drops ipc-queue-full / ipc-down / method-not-found on stderr

- instruction: Own `microduck_cli`/ipc/client.py and tests/`test_client.py`. Writer thread + queue.Queue(maxsize=N); reader thread correlates by id and writes notifications into per-method peek slots (last value + monotonic timestamp). hello() records `API_VERSION` and compares `JOINT_NAMES` / obs / action lens from t1's table; mismatch raises CliError exit 2 naming both values. Drops go through a logger named microduck.sense (stderr-only) as \[SENSE stage=ipc event=<reason>\] lines. Test against tests/`fake_robotd.py` from t2.
- depends on: t1, t2
- covers: c4, h3, c36, h27
- acceptance:
  - Against the fake: notify() returns in under 1 ms with the socket wedged, and the drop counter shows ipc-queue-full once the queue is full
  - A method the fake answers with -32601 is logged as a named drop and does not raise into the caller's tick
  - A fake advertising a joint table of 14 names makes connect() raise CliError exit 2 whose message contains both 14 and 15
  - The module imports only stdlib
- obligation: `o10` (criterion 1) [ipc/client.notify() on the tick thread] returns in under 1 ms with the socket wedged and counts an ipc-queue-full drop instead of blocking

### t11 — Rule engine and the single admission registry — `microduck_cli`/behavior/`rule_engine.py` + intents.py + tests: evaluates react/inhibit rules against the Sense snapshot each tick with cooldown, hysteresis and duration bounds; KindRegistry validates and admits intents from BOTH rule firings and an injected spool/CLI path through the same validator with per-axis limits and duration caps; every refusal is a named drop

- instruction: Own `microduck_cli`/behavior/`rule_engine.py`, intents.py and tests. One KindRegistry class, one validate(kind, payload) entry; rule firings call the same admit() the CLI/spool path calls. Bounds are module constants with a cited precedent (reachy `goto_intent` `MAX_DURATION_S` pattern). Enforce cooldown/hysteresis/`duration_s` from t3. Never import ipc or cli modules.
- depends on: t3, t4
- covers: c24, h15
- acceptance:
  - The same over-limit payload submitted via a rule and via registry.inject() yields byte-identical refusal text
  - A static test asserts exactly one KindRegistry class and one validate() entry point exist under `microduck_cli`/behavior/
  - A rule with `cooldown_s`=5 fires at most once in 250 simulated ticks at 50 Hz
- obligation: `o11` (criterion 1) [the single KindRegistry] rule-fired and injected intents receive byte-identical refusal text for the same over-limit payload

### t12 — Engine core, tick seam, liveness, senselog, and the CLAUDE.md decision update — `microduck_cli`/behavior/engine.py (50 Hz loop with injected clock, engine.run(`tick_seam`=...) called once per tick), TickBus fault-isolating fan-out, liveness.py (state.json heartbeat, `refuse_if_engine_live`), senselog.py (stderr-only \[SENSE stage= source= event=\] lines); plus CLAUDE.md and cicd/SKILL.md rewritten to record decision c20 (engine lives here, extraction-first)

- instruction: Own `microduck_cli`/behavior/engine.py, liveness.py, senselog.py, tests/`test_engine.py`, CLAUDE.md, .claude/skills/cicd/SKILL.md (prose only, record the divergence in docs/skill-sources.md). engine.run(`tick_seam`=..., clock=..., `max_ticks`=...) calls seam(ctx) once per tick after the sink write; TickBus isolates driver exceptions. Heartbeat is state.json under the state dir with a monotonic stamp; `refuse_if_engine_live`() reads it. This is the first engine PR: rewrite the CLAUDE.md sibling section to record decision c20 and drop the 'engine belongs upstream' triage line from cicd.
- depends on: t4
- covers: c23, c26, h17
- acceptance:
  - With `max_ticks`=500 and a fake clock the loop is deterministic: two runs produce identical seam call sequences
  - A driver that raises is isolated by TickBus (logged, others still run) and the tick period is unchanged
  - A second engine started against the same state dir exits 1 with 'engine live' while the heartbeat is fresh, and starts once it is stale
  - The PR diff touches the CLAUDE.md neurosymbolic-system section and cicd/SKILL.md triage defaults, replacing the 'engine belongs upstream' guidance
- obligation: `o12` (criterion 3) [behavior/liveness.`refuse_if_engine_live`()] a second engine exits 1 while the heartbeat is fresh and starts once it is stale

### t13 — Sim stack params and lifecycle — `microduck_cli`/env/params.py (generate the robotd params toml naming policy files from the microduck clone, short state dir) and env/stack.py (build robotd/robotctl/tof/sounds with cargo, locate libonnxruntime in the RL venv, start duck-body on a port and robotd --sim or --fake with --socket, pidfiles, stop by pid only after /proc cmdline matches), all subprocess calls injected for tests

- instruction: Own `microduck_cli`/env/params.py, stack.py, tests/`test_stack.py`. Reproduce scripts/duck-sim from the pinned sim-remote-io commit step by step (read it, do not copy): cargo build -p robotd -p robotctl -p tof -p sounds; ORT = first libonnxruntime.so.\* under <rl>/.venv; params toml names policy files found in the clone; duck-body --port N \[--ducks --headless --scene\]; robotd --sim 127.0.0.1:N --socket <state>/<name>.sock --params <file>, or --fake. Pidfiles per process; stop only after /proc/<pid>/cmdline contains the expected binary name. All subprocess.Popen calls go through an injected runner.
- depends on: t6
- covers: c22, h14
- acceptance:
  - With injected fake subprocesses, up() issues the documented command lines in order and down() sends TERM only to pids whose cmdline matches, never by name
  - A stale pidfile whose pid now belongs to another process is removed without signalling it
  - The generated params toml round-trips through tomllib and names every policy file that exists in the clone
- obligation: `o13` (criterion 1) [env/stack.down()] signals only pids whose /proc cmdline matches the expected binary; never kills by name

### t14 — Train lane — `microduck_cli`/train/lane.py + artifacts.py + tests: argv builders for list-envs, smoke (64 envs / 5 iters), train (local or --hf-jobs with flavor/namespace/timeout), play, export via scripts/export.py, publish, and install (robotctl policy add|load); a smoke-gate record that refuses a long run without a passed smoke; wandb optional (only passes --wandb-run-path or a key when configured); secrets passed by environment only

- instruction: Own `microduck_cli`/train/lane.py, artifacts.py, tests/`test_lane.py`. Argv builders only — never import `mjlab_microduck`. Commands from `microduck_rl` README/AGENTS.md/scripts/hf/README.md at the pinned commit: uv run --project <rl> list-envs | train <task> --env.scene.num-envs 64 --agent.`max_iterations` 5 (smoke) | train ... \[--hf-jobs --flavor --namespace --timeout\] | play | scripts/export.py | publish; install = robotctl policy add|load via the IPC client later. Smoke-gate record lives under the state dir. Secrets only via env of the child; never on argv.
- depends on: t8
- covers: c8, h9, c40, h29
- acceptance:
  - Each builder's argv equals the command in `microduck_rl` README/AGENTS.md for a fixture task id (table test)
  - train() without a recorded smoke pass raises CliError exit 1 naming the smoke command
  - `assert_no_secrets` passes over every argv the lane builds with `HF_TOKEN` and a wandb key set to sentinels
  - grep finds no import of `mjlab_microduck` anywhere under `microduck_cli`/
- obligation: `o14` (criterion 2) [train/lane.train() smoke gate] refuses to build a long-run argv without a recorded smoke pass, naming the smoke command

### t15 — Env doctor — `microduck_cli`/env/doctor.py + tests: checks for the microduck clone on the pinned sim-remote-io commit, cargo >= 1.89, built daemons, RL venv with libonnxruntime, state dir length, free body port, host class (from hosts.py), hf auth / wandb / `DUCK_PIN` reported as set/unset only; each failing check names the upstream doc line; exit 2 when anything required is missing

- instruction: Own `microduck_cli`/env/doctor.py and tests/`test_env_doctor.py`. Checks (each a rubric-shaped dict): microduck clone present and at the pinned commit, cargo >= 1.89, built binaries, RL venv + libonnxruntime, state dir path length, body port free, host class from t8, hf auth / wandb / `DUCK_PIN` as set|unset. Every remediation carries a URL into the upstream docs. All probes injected for tests; exit 2 on any required failure.
- depends on: t6, t8
- covers: c5, h6, c25, h16, h20
- acceptance:
  - On a fixture box with nothing installed the report lists every missing item with a URL into pollen-robotics docs and exits 2; on a complete fixture it exits 0
  - Output never contains the sentinel values of `HF_TOKEN`, `DUCK_PIN` or a wandb key (uses `assert_no_secrets`)
  - Report is rubric-shaped {healthy, checks:\[{id, passed, severity, message, remediation}\]} in --json
- obligation: `o15` (criterion 1) [env/doctor report] every failing required check carries an upstream doc URL and the command exits 2

### t16 — Target sink, abnormal-exit release, human-driving gate, and idle base — `microduck_cli`/behavior/sink.py (intents -> IPC client, notifications for continuous channels, requests for discrete, no filtering), release.py (stop, pose active:false, mouth 0, sound hold:false — each independent, never relax), `human_gate.py` (pad.input activity or robot.remoteSessionActive withholds twist/head/look/pose/skill/mode as 'human-driving' drops while sound/mouth pass), idle.py (feel-alive base, silent when fallen/disabled/human)

- instruction: Own `microduck_cli`/behavior/sink.py, release.py, `human_gate.py`, idle.py and their tests. sink: continuous channels -> client.notify, discrete -> client.request, no smoothing or rate limit (h2). release: four independent best-effort sends in a finally block, never robot.relax, exit non-zero naming failures. `human_gate`: pad.input activity within N ms or remoteSessionActive => withhold twist/head/look/pose/skill/mode with a human-driving drop; sound and mouth pass. idle: PASSIVE base, silent when fallen, not enabled, or gated. Test against t2's fake with SIGINT delivered mid-behaviour.
- depends on: t10, t12
- covers: c31, h22, c35, h26, h2
- acceptance:
  - SIGINT mid-behaviour against the fake yields exactly stop, pose active:false, mouth 0, sound hold:false in the call log, no relax, and exit non-zero naming a release the fake was told to refuse
  - With a simulated pad.input stream the call log has zero motion calls over 200 ticks, a human-driving drop count on stderr, and sound/mouth calls continue
  - A rule's step command reaches the fake unchanged (no EMA or rate limit applied client-side)
  - Idle emits nothing while fallen=True, enabled=False, or the human gate is active
- obligation: `o16` (criterion 1) [behavior/release.py on abnormal exit] sends stop, pose active:false, mouth 0, sound hold:false independently and never robot.relax

### t17 — Shipped default rules, skill validation, and JSONL replay — `microduck_cli`/behavior/`default_rules.toml` (fallen-inhibit, low-battery-inhibit, at most two reactions; ids pinned by test), behavior/skills.py (validate rule actions against robot.policies or a recorded skills snapshot), behavior/replay.py (evaluate rules over a recorded JSONL stream offline)

- instruction: Own `microduck_cli`/behavior/`default_rules.toml`, skills.py, replay.py and their tests. Shipped rules: fallen-inhibit (disable all skills + idle), low-battery-inhibit, at most two reactions; document why each is safe in any room, ids pinned by test. skills.validate(rules, `skills_list`) with the exact message 'c not in \[a, b\]'. replay.run(rules, `jsonl_path`) evaluates tick by tick using t11's engine with an injected clock.
- depends on: t3, t11
- covers: c39, h28, c33, h24
- acceptance:
  - The shipped file has at most four rules and a test pins their ids and fields
  - Replaying a fixture with fallen=true shows every skill and the idle base inhibited within one tick
  - validate() against skills \[a, b\] refuses a rule naming c with the message 'c not in \[a, b\]' and the rule id, identically online (fake robot.policies) and from a snapshot
- obligation: `o17` (criterion 2) [behavior/replay.py on a recorded fall] every skill and the idle base are inhibited within one tick of fallen=true

### t18 — duck noun — `microduck_cli`/cli/`_commands`/duck.py + explain/duck.py + tests/`test_duck.py`: health, monitor (--json NDJSON), init, relax (--yes), enable, do, mode, look, stop, move (deadman-refreshed robot.move), quack, configure --list, and record (JSONL of state/health/pad/tof with monotonic timestamps, pure stdout); every verb in robotctl's words plus move; init/relax/enable/do/move/mode gated via gate.py; upstream durability and safety sentences verbatim

- instruction: Own `_commands`/duck.py, explain/duck.py, tests/`test_duck.py` only. Verb names and flags mirror robotctl (read robotctl/src/main.rs at the pinned commit); the only addition is move, which sends robot.move notifications at intent rate and exits on Ctrl-C with a stop. Gate init/relax/enable/do/move/mode through t5. record writes JSONL to stdout only; diagnostics to stderr. Reuse `assert_no_secrets`. Every verb: --json, catalog entry, pty + non-TTY tests.
- depends on: t5, t6, t10
- covers: c6, h7, c7, h8, c32, h23, c41, h30, c27, h18
- acceptance:
  - A table test pins the verb list against robotctl's Namespace/RobotCommand names with exactly one addition, move
  - Each gated verb passes the three gate tests (pty prompt, non-TTY dry-run with zero calls in the fake log, non-TTY --apply sends)
  - record against the fake writes one JSON object per line with monotonic ts and source tag, stdout contains nothing else, and replay.py accepts the file
  - Every verb runs unattended with --json --apply in a non-TTY test and prompts in a pty test
- obligation: `o18` (criterion 2) [duck noun gated verbs] init/relax/enable/do/move/mode each pass the three gate tests against the fake's call log

### t19 — policy noun — `microduck_cli`/cli/`_commands`/policy.py + explain/policy.py + tests/`test_policy.py`: list/load/add/remove/reset/search/check/update via robot.loadPolicy and policy.\* calls, pad bindings/bind/reset via pad.\*, the train sub-verbs (smoke, train, play, export, publish, install) driving train/lane.py, load output stating 'survives a reboot' and naming policy reset; gated per c7

- instruction: Own `_commands`/policy.py, explain/policy.py, tests/`test_policy.py` only. Slot and skill changes go through robot.loadPolicy / policy.\* / pad.\* calls on the client — never a file. Load output must include 'survives a reboot' and name 'policy reset'. Train sub-verbs call t14's builders through an injected runner; train refuses without a smoke record. Gate load/add/reset/bind through t5.
- depends on: t10, t14
- covers: c8, h9, c34, h25
- acceptance:
  - policy load issues robot.loadPolicy only and never opens a config file (`test_no_config_writes` covers the module); its text output contains 'survives a reboot' and 'policy reset'
  - policy train without a smoke record exits 1 naming the smoke command; with --hf-jobs the argv matches scripts/hf/README.md
  - Every verb takes --json and has a catalog entry (`test_lockstep`)
- obligation: `o19` (criterion 1) [policy load path] issues robot.loadPolicy only, never opens a config file, and prints 'survives a reboot' and 'policy reset'

### t20 — env noun — `microduck_cli`/cli/`_commands`/env.py + explain/env.py + tests/`test_env.py`: doctor, up (--sim | --fake, --ducks N, --scene, --headless), down, status, hosts; wraps scripts/duck-sim when the clone has it, otherwise drives env/stack.py; exit 2 on a failed doctor

- instruction: Own `_commands`/env.py, explain/env.py, tests/`test_env.py` only. If <clone>/scripts/duck-sim exists, shell out to it with the `DUCK_SIM_`\* env mapped from flags; otherwise drive t13. doctor delegates to t15 and exits 2 on failure. up waits for robot.health healthy via the client with a timeout and prints the socket path; down verifies nothing is left listening.
- depends on: t13, t15
- covers: c22, h14, h20
- acceptance:
  - env up --fake with injected subprocesses reaches a 'healthy' report from the fake within the timeout and env down leaves no tracked pid
  - env doctor output on the no-tools fixture matches the doctor module's report and exits 2
  - No verb requires a params file, ORT path or socket path from the operator; each is derived or reported
- obligation: `o20` (criterion 1) [env up --fake / env down] reaches healthy within the timeout and leaves no tracked pid after down

### t21 — rules noun with engine and intent verbs — `microduck_cli`/cli/`_commands`/rules.py + explain/rules.py + tests/`test_rules_cli.py`: overview, list, check (--replay <jsonl>, --skills <snapshot>), engine run|start|stop|status (start sequence connect -> hello -> health -> init -> enable -> armed, gated steps), and intent <kind> \[payload\] injecting through the one registry

- instruction: Own `_commands`/rules.py, explain/rules.py, tests/`test_rules_cli.py` only. check: exit 0 on content issues (rubric), naming rule ids; --replay and --skills use t17. engine run: connect -> hello -> health -> init (gated) -> enable (gated) -> armed, each logged; start/stop/status use t12's heartbeat; refuse a second engine. intent <kind> \[json\] calls the one registry from t11 and prints the admission or the refusal text verbatim.
- depends on: t11, t16, t17
- covers: c12, c24, h15, c33, h24, c32, h23
- acceptance:
  - rules check on a malformed file exits 0 with the content issue named by rule id; against the fake reporting skills \[a, b\] a rule naming c is refused with 'c not in \[a, b\]'
  - engine run against the fake logs the six start steps in order; a rule that fires a skill before enable is a named drop with no retry
  - rules intent with an over-limit payload gets the same refusal text a rule would (shared test with t11)
- obligation: `o21` (criterion 2) [rules engine run start sequence] connect, hello, health, init, enable, armed appear in order; a skill before enable is a named drop with no retry

### t22 — Lockstep docs and upstream links — README, explain root entry, overview and learn text for the four nouns; every robot/training explain entry and CliError remediation carries a URL into pollen-robotics/microduck or `microduck_rl` docs; a test asserts no explain body restates a robotctl command table longer than three lines; docs/skill-sources.md unchanged

- instruction: Own README.md, the explain root entry, overview/learn prose, docs/ and tests/`test_links.py`. Every explain body and remediation for a robot or training topic links to the owning upstream page (cheatsheet.md, duckctl.md, policy-manifest.md, architecture.md, `microduck_rl` AGENTS.md, scripts/hf/README.md). The test also fails on any explain body containing more than three consecutive robotctl command lines. Run markdownlint, `test_lockstep` and the rubric gate.
- depends on: t18, t19, t20, t21
- covers: c17, h13, c16, h12
- acceptance:
  - tests/`test_links.py` finds a pollen-robotics URL in every explain entry under duck/policy/env/rules and in every remediation string those modules raise
  - markdownlint-cli2 and `test_lockstep` pass; teken cli doctor . --strict passes
- obligation: `o22` (criterion 1) [tests/`test_links.py`] every explain entry and remediation under the four nouns carries a pollen-robotics docs URL

### t23 — On-box verification against robotd --fake and --sim, and the success-signal record — build the pinned sim-remote-io commit, run the six c30 checks on the dev box, record each command with its expected output in docs/verification/<date>-sim-bringup.md, add an optional CI job that runs the operate verbs against robotd --fake when cargo and the clone are available (skipped otherwise), and pin the upstream commit hashes in docs/

- instruction: Own docs/verification/<date>-sim-bringup.md, the optional CI job in .github/workflows/tests.yml, and the pinned-commit note in docs/. On this box: clone microduck at the pinned sim-remote-io commit and `microduck_rl` at its pinned commit, uv sync the RL repo with `UV_HTTP_TIMEOUT`=600, build, run env up --fake then --sim, and execute the six c30 checks, pasting exact outputs. Run the operate test-suite against the real robotd --fake and file any divergence from the Python fake as an issue before merge. The CI job must skip (not fail) when cargo or the clone is absent.
- depends on: t22
- covers: c1, h1, c28, h19, c29, h20, c30, h21
- acceptance:
  - docs/verification/ records the six checks with pass/fail and the exact output for --fake and --sim on this box, plus the microduck and `microduck_rl` commit hashes used
  - The operate verbs pass unchanged against robotd --fake (real binary) and against the in-process fake; any divergence is filed as an issue before merge
  - The optional CI job is green when it runs and 'skipped' (not failed) when cargo or the clone is absent
- obligation: `o23` (criterion 2) [operate verbs vs the real robotd --fake] the duck verb test-suite passes unchanged against the real daemon on this box, with any divergence filed

## Risks

- [follow_up] sim-remote-io is unmerged (32 ahead / 87 behind main on 2026-09-03); pin one commit for t13/t23 and re-validate when it merges — `API_VERSION` and body protocol may move (task t23)
- [unknown_nonblocking] Native aarch64 build of robotd/robotctl/tof/sounds and duck-body (warp/mujoco on GB10) unverified on this box; settle in t23 before anything depends on --sim timings (task t23)
- [unknown_nonblocking] CI cannot run the real robotd --fake without cargo and the upstream clone: the in-process Python fake (t2) is the CI surface and the real --fake run is the on-box step in t23; a behaviour the Python fake gets wrong stays invisible until t23 (task t2)
- [unknown_nonblocking] robot.state / robot.health payload field names are unread; t4's Sense fields are provisional until t18's first recording against the real daemon, after which t4/t17 may need a field-rename pass (task t4)
- [unknown_nonblocking] Jetson Thor / AGX Orin torch+warp path for `microduck_rl` is unverified; t8/t15 classify the host but cannot promise training runs there until tried on a board (task t8)
- [unknown_nonblocking] Upstream may ship the deferred WebSocket JSON-RPC surface (remote-webrtc.md §11); keep the transport behind the t10 client seam so a second transport is additive (task t10)
- [follow_up] Console-script half-rename (microduck vs microduck-cli) is still open; decide whether it rides with t9's scaffold PR or ships alone first — it touches pyproject, prog=, every `_commands` module, catalog, README and tests (task t9)
