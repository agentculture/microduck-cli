# Build Plan — run-trace tool

slug: `run-trace-tool` · status: `exported` · from frame: `run-trace-tool`

> microduck-cli ships a run-trace tool: one command re-runs the tutorial's steps, traces every command, daemon log line and viewer frame with wall-clock stamps, and regenerates the same MicroDuck Run Trace page verbatim — as a standalone local HTML file and as a Claude artifact fragment

## Tasks

### t1 — trace/events.py — the one event schema (t, wall, lane, kind, label, step, per-kind fields), append()/load()/validate(), the run-dir layout helper, and `import_run`(src, dst) that adopts an external events.jsonl + shots dir (validating each line, first bad line as CliError); `microduck_cli`/trace/`__init__.py`; tests/`test_trace_events.py`

- covers: c19, h3, c22, h7
- acceptance:
  - a fixture events.jsonl round-trips through load()/append() unchanged
  - a line missing 'lane' is refused with a CliError naming the line number
  - `import_run` copies events + shots and rewrites /home/<user> to ~ in labels
  - no third-party import under `microduck_cli`/trace (grep test) and pyproject dependencies stays \[\]

### t2 — trace/plan.py — the TOML plan (ordered steps: step, label, cmd, `sleep_before`/after, shot, retry, note) and `from_tutorial`(`mdx_text`, sidecar) that extracts fenced bash commands with the same rules as docs/tools/`check_tutorial.py` (nocheck skipped, comments stripped, uv-run prefix kept as typed) and merges the sidecar's sleeps/frames/retries by command match; tests/`test_trace_plan.py`

- covers: c4, h9
- acceptance:
  - `from_tutorial` on a tutorial fixture yields exactly the command list `check_tutorial`.`extract_commands` yields for the same text
  - a sidecar entry that matches no command is reported, not silently dropped
  - plan round-trips: dumps() then loads() is equal

### t3 — trace/render.py — the MicroDuck Run Trace page as a deterministic template: render(`run_dir`) -> (`trace_html` fragment starting with <title>, `index_html` = skeleton + fragment), frames inlined as data URIs in filename order, CSS object-position crop from recorded geometry, the tier map + timeline + captions + step ladder + checks table, failed spans in the failure colour beside retries, headless frame panel with the reason, interpolated positions labelled; tests/`test_trace_render.py`

- covers: c5, h10, c8, h12, c20, h4
- acceptance:
  - rendering the same run dir twice yields identical bytes
  - trace.html has no doctype/html/head/body and index.html has them once
  - a fixture with rc=2 then rc=0 for the same label renders two spans, one in the failure colour
  - a fixture with no shot events renders the frame panel with its reason text
  - the render takes no clock and reads no environment

### t5 — trace/capture.py — display-owner discovery (DISPLAY/XAUTHORITY/DBUS from the gnome-shell process environ, seam-injected), window-only capture (focus 'MuJoCo : scene' with xdotool/wmctrl when present, then gnome-screenshot -w) with the fallback full-screen shot plus xwininfo geometry recorded on the shot event, headless detection, and the opt-in autolock pause/restore helper (idle-delay, lock-enabled) that always restores — USER DECISION c15: opt-in flag only; tests with fake runners

- covers: c6, h11
- acceptance:
  - with no graphical session capture() returns a 'headless' outcome and raises nothing
  - when the focus tool is absent the shot event carries the window geometry from xwininfo
  - `pause_autolock`() restores the two gsettings values even when the body raises
  - no PIL import anywhere under `microduck_cli`

### t6 — trace/runner.py — Recorder: run one command via bash -c with stdin=/dev/null, stdout to steps/NN-name.out, stderr streamed line by line to events with timestamps (\[SENSE lines to the engine lane), cmd-start/cmd-end with rc, elapsed, first stdout line; a log-tail thread for the robotd and body logs under `DUCK_SIM_STATE`; `run_plan`(plan, `run_dir`, shot=capture) executing steps with sleeps, frames and one retry when the plan says so; `exec_one`(argv, `run_dir`) so any CLI work is traced (USER DECISION c14); serve.py (ThreadingHTTPServer on 127.0.0.1, prints the URL); tests with fake commands

- depends on: t1, t5
- covers: c23, h6
- acceptance:
  - a plan line is executed verbatim; the recorder never appends --apply and never opens a TTY
  - a command's stderr lines carry increasing t and land in the right lane
  - a step with retry=1 that fails then passes records two cmd-end events with rc 2 then 0
  - run dir defaults to <`DUCK_SIM_STATE`>/trace/<UTC stamp> and --out overrides it

### t7 — cli/`_commands`/trace.py — the trace noun group (`parser_class`=type(p)) with overview, run, exec, plan, import, render, serve, list (USER DECISION c13); every verb has --json and raises CliError; explain/trace.py catalog entries for (trace,) and each verb; overview.py `_VERBS` row; learn.py `_TEXT` + `_as_json_payload`; tests/`test_cli_trace.py`; teken cli doctor --strict passes

- depends on: t1, t2, t3, t5, t6
- covers: c17, h1, c18, h2, c24, h14, c27, h17
- acceptance:
  - trace overview exits 0 with any positional and names operators, agents and tutorial reviewers
  - `test_every_catalog_path_resolves` passes with the trace entries
  - a bad nested argument yields the two-line error/hint contract with exit 1, in text and --json
  - `_commands`/trace.py contains no engine logic; it calls `microduck_cli`.trace public functions only
  - uv run teken cli doctor . --strict exits 0

### t8 — docs/traces/2026-09-08-spark-tutorial/ — the committed example (USER DECISION c16): import today's scratch events.jsonl and 12 frames (home paths redacted), render index.html + trace.html, and tests/`test_trace_example.py` that re-renders the directory and diffs byte-for-byte against the committed pages

- depends on: t1, t3
- covers: c21, h5, c26
- acceptance:
  - the test renders docs/traces/2026-09-08-spark-tutorial and asserts equality with the committed index.html and trace.html
  - the example events differ from the scratch events only in /home/<user> -> ~
  - the example page shows the first env up failure and its retry side by side

### t9 — docs: CLAUDE.md (trace package in the packages table, the noun in the architecture section, the autolock flag), README pointer, operate-microduck SKILL.md 'Watch it' pointing at trace run/exec, docs/traces/tutorial.sidecar.toml (the sleeps, frames and the single env up retry for the Jetson AI Lab tutorial), CHANGELOG entry via the version-bump skill (patch)

- depends on: t7, t8
- covers: c11, h13, h16
- acceptance:
  - pyproject version differs from origin/main and CHANGELOG names the trace noun and what replaced the scratch tracer
  - markdownlint-cli2 passes
  - docs/traces/tutorial.sidecar.toml loads through plan.`from_tutorial` against the fork's tutorial mdx

### t10 — acceptance on Spark: from the branch, microduck trace plan --from-tutorial <fork mdx> --sidecar docs/traces/tutorial.sidecar.toml, one fresh windowed trace run --plan (with --pause-autolock), trace render, trace serve; open the PR with the after-state walk-through and every success-signal clause mapped to a test or a recorded command; ask-colleague review; cicd open

- depends on: t9
- covers: c1, h8, c25, h15, c28, h18
- acceptance:
  - the fresh run's page renders and scrubs; its run dir is kept outside the tree
  - the PR body lists each success-signal clause with its test name or command
  - CI green: test, lint, version-check

## Risks

- [unknown_nonblocking] the first env up --sim failure of 2026-09-08 is unexplained; a plan with retry=1 keeps the page honest but the root cause is outside this plan
- [unknown_nonblocking] window focus for gnome-screenshot -w needs xdotool or wmctrl; neither is confirmed installed on Spark/Thor/Orin — the geometry fallback must be the tested default, focus an improvement (task t5)
- [unknown_nonblocking] the first env up --sim failure of 2026-09-08 is unexplained; a plan with retry=1 keeps the page honest but the root cause is outside this plan
- [unknown_nonblocking] the committed example weighs ~600 KB of frames + page in git history; every template change re-commits it — keep template changes to one commit per PR (task t8)
