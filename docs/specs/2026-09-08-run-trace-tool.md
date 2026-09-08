# run-trace tool

> microduck-cli ships a run-trace tool: one command re-runs the tutorial's steps, traces every command, daemon log line and viewer frame with wall-clock stamps, and regenerates the same MicroDuck Run Trace page verbatim — as a standalone local HTML file and as a Claude artifact fragment
> instruction: verify with the success signal: import + render the committed example byte-for-byte, then one fresh windowed run

## Audience

- operators and agents driving the duck who want a shareable, honest replay of a session — the tutorial's authors and reviewers first, then anyone reporting a run
  - instruction: the overview and catalog text name this audience first

## Before → After

- Before: today the trace exists once, hand-built: a scratch rt.py in a session scratchpad, a hand-written page.html, PIL cropping, and manual bash chains — nobody can regenerate it, and the tier map's honesty rules live only in the page's prose
  - instruction: the CHANGELOG entry says what replaced the scratch tracer
- After: microduck trace run --plan tutorial.toml (or exec/import) produces a run directory that trace render turns into the MicroDuck Run Trace page: the three-tier map lighting up over a scrubbable timeline with the viewer frames, openable from file://, served with trace serve, or published as an artifact from trace.html — and rendering the same run dir twice gives identical bytes
  - instruction: this is the acceptance walk-through in the PR description

## Why it matters

- a verification record says what happened; the trace shows which tier did it and when, from the same timestamps — a reviewer of the tutorial or of a failed run can see the first env up die and the retry succeed instead of reading two log excerpts
  - instruction: the trace overview text carries this one-liner

## Requirements

- the step plan is data, not code: the tracer reads the tutorial's fenced bash commands (`check_tutorial.py`'s `extract_commands`, which already skips nocheck fences) plus a small sidecar (TOML) that says where to sleep, screenshot, retry once, or gate — so the same tutorial file regenerates the same run
  - instruction: share the extraction rules: move the fenced-block extractor into `microduck_cli`/trace/plan.py and have docs/tools/`check_tutorial.py` keep its own copy untouched (docs script, not a package importer); a test compares both on the tutorial fixture
  - honesty: trace plan --from-tutorial produces the same command list `check_tutorial.py` extracts (26 lines for the current tutorial), nocheck fences skipped
- two outputs from one generator: index.html (a complete document, opens from file:// or python3 -m http.server, all data and frames inlined as data URIs, fonts falling back to system stacks offline) and trace.html (the same body as an Artifact fragment without doctype/head/body, which the Artifact tool wraps)
  - instruction: render writes trace.html (fragment) then index.html = skeleton + fragment; test both properties
  - honesty: index.html opens from file:// with no network and shows the frames; trace.html has no doctype/html/head/body and starts with <title>
- screenshots without Pillow: capture the window only with gnome-screenshot -w (the tutorial's own recipe) after focusing the MuJoCo window, or capture full-screen and record the xwininfo geometry so the page crops with CSS object-position — no image library in the repo; today's rt.py used PIL, which is neither a dev dep nor in uv.lock
  - instruction: capture.py: focus the 'MuJoCo : scene' window if a focus tool exists, gnome-screenshot -w; else full-screen + geometry from xwininfo recorded on the shot event, and the page crops with object-position/object-fit from that geometry
  - honesty: no import of PIL anywhere under `microduck_cli`; a frame captured window-only is the MuJoCo window and nothing else
- every PR bumps the version and prepends a CHANGELOG entry (version-check job); this one is a patch bump with docs/tools, tests, CI and CLAUDE.md/README pointers
  - instruction: run the version-bump skill (patch) as the last step before cicd open
  - honesty: pyproject version differs from origin/main and CHANGELOG has the entry
- engine logic lives in `microduck_cli`/trace/ (events.py schema + recorder, plan.py, capture.py for frames, render.py for the page, serve.py) and cli/`_commands`/trace.py is thin argparse wiring for a noun group built with `parser_class`=type(p) so nested parse errors keep the CliError contract; the noun gets overview, catalog entries, the `_VERBS` list and learn's `_TEXT`/`_as_json_payload` in lockstep
  - instruction: mirror cli/`_commands`/cli.py for the noun group and the behavior/ package split for the engine; add catalog entries for (trace,) and each verb, the `_VERBS` row, and learn's text + JSON payload
  - honesty: nothing under `microduck_cli`/trace/ imports cli/ except cli/`_errors`, and `_commands`/trace.py contains no engine logic (the reachy split)
- verbs: trace overview; trace run --plan <toml> (execute a step plan, tracing each command's argv, exit, timing, stdout first line, timestamped stderr lines, the robotd/body log tails and viewer frames); trace exec -- <command> (trace one arbitrary command into the current run dir, so exploratory or post-run work is traced the same way); trace plan --from-tutorial <mdx> (derive a plan from the fenced bash commands, nocheck fences skipped, with an editable sidecar for sleeps/frames/retries); trace import <dir> (adopt an events.jsonl + shots directory produced elsewhere, e.g. today's scratch tracer); trace render <run-dir> (write index.html and trace.html); trace serve <run-dir> (stdlib http.server on the run dir); trace status/list of run dirs
  - instruction: one register(sub) per verb inside `_commands`/trace.py; run/exec share a Recorder; serve wraps http.server.ThreadingHTTPServer bound to 127.0.0.1 with a printed URL
  - honesty: every verb has --json, raises CliError on failure, and trace overview exits 0 on any argument (rubric)
- one event schema (t, wall, lane, kind, label, step, and per-kind fields) shared by run, exec and import — the renderer never knows which mode produced a record; kinds: cmd-start, cmd-end, stderr, log, shot, note
  - instruction: events.py defines the record dataclasses and a load()/append() pair used by run, exec, import and render; import validates each line and reports the first bad one as CliError
  - honesty: trace render accepts a run dir produced by run, exec or import without a mode flag
- the run directory defaults to <`DUCK_SIM_STATE`>/trace/<UTC-stamp>/ (--out overrides): events.jsonl, steps/NN-name.out|.err, shots/, plan.toml, index.html, trace.html; the same run dir renders byte-for-byte identical pages on every render (verbatim regeneration is a test)
  - instruction: render sorts everything it emits, embeds frames in filename order, and takes no clock; test renders twice and compares bytes
  - honesty: rendering the same run dir twice yields identical bytes — no wall-clock, random id or dict-order nondeterminism in render
- today's run is the committed example: docs/traces/2026-09-08-spark-tutorial/ holds events.jsonl, the 12 frames and the rendered index.html + trace.html, imported through trace import and re-rendered by trace render; a test renders the example dir and diffs against the committed page
  - instruction: copy the scratch run's events.jsonl and 12 web JPEGs to docs/traces/2026-09-08-spark-tutorial/, redact /home/<user> to ~, render, commit index.html + trace.html; the test re-renders and diffs
  - honesty: the committed example is imported from today's scratch events.jsonl unchanged except home-path redaction, and its page differs from the artifact only where the generator's template changed
- trace run and exec honour the repo's gate: commands that move hardware are passed through as typed (--apply must be in the plan); the tracer never injects --apply, never answers a TTY prompt, and a dry-run plan line is recorded as what it is
  - instruction: the plan's command strings are executed verbatim through bash -c with stdin from /dev/null; document that --apply belongs in the plan
  - honesty: a plan line without --apply on a pipe records a dry-run plan and moves nothing — the tracer is not a way around the gate

## Honesty conditions

- a windowed trace run on Spark and a render of the committed example both work from a clean checkout of the PR branch
- a run with a failed then retried command renders both spans, the failed one in the failure colour; a headless run renders the frame panel with the reason text
- uv run python -c 'import `microduck_cli`.trace' works in a venv with no extras and pyproject dependencies is still \[\]
- trace overview names operators, agents and tutorial reviewers as the audience
- the after-state walk-through in the PR description was executed and its commands appear in the PR
- the scratch rt.py and page.html are not copied into the repo; their content is re-expressed inside `microduck_cli`/trace
- the committed example page shows the 2026-09-08 first env up failure and its retry side by side
- every clause of the success signal maps to a named test or a command recorded in the PR body

## Success signals

- from a clean checkout: microduck trace import docs/traces/2026-09-08-spark-tutorial && microduck trace render <dir> reproduces the committed index.html byte-for-byte (a pytest asserts it); teken cli doctor --strict passes with the new noun; `test_every_catalog_path_resolves` covers trace; a fresh windowed trace run --plan on Spark produces a page a reviewer can scrub through
  - instruction: each clause is a test or a recorded command in the PR

## Scope / boundaries

- the page never smooths a run: a failed attempt stays on the timeline in red beside its retry, a headless run shows the frame panel empty with the reason, interpolated positions (trainer iterations) are labelled as interpolated — the honesty rules of the verification records apply to the picture
  - instruction: fixture events with rc=2 then rc=0 for the same label; assert both spans and the frame-panel text in the rendered HTML
- the trace noun adds no runtime dependency: stdlib only (json, subprocess, base64, http.server, threading); no Pillow — frames are captured window-only (gnome-screenshot -w after focusing the MuJoCo window via xdotool/wmctrl if present, else full-screen with recorded xwininfo geometry that the page crops with CSS); dependencies = \[\] is unchanged
  - instruction: grep test: no third-party import under `microduck_cli`/trace; pyproject dependencies stays \[\]

## Non-goals

- not a generic tracer: the tier map (microduck-cli / robotd / `microduck_rl`, operator, viewer, GPU) and its edges are MicroDuck-specific and hard-coded in the generator; reachy-mini-cli or neurosymbolic-system would cite the method, not import the map

## Assumptions

- the runner is Linux/GNOME-shaped: gnome-screenshot, xwininfo, loginctl and the session DISPLAY/XAUTHORITY/DBUS address are discovered from the gnome-shell process environment as the operate-microduck skill prescribes; with no graphical session it runs the tutorial headless and skips frames

## Scope exploration

- `s1` — `docs/tools/check_tutorial.py + tests/test_check_tutorial.py`: docs/tools/ is the established home for repo-side tooling that is not shipped in the wheel; its tests import the script by path via importlib, which the new tool's tests can copy
  - seeds: `c2` (rejected)
- `s2` — `microduck_cli/cli (three-places rule, teken rubric) + pyproject dependencies = []`: a CLI verb would cost the catalog/overview/learn lockstep and the rubric gate for a docs-time tool, and the wheel must stay zero-dep; the tool stays outside the package
  - seeds: `c3` (rejected)
- `s3` — `docs/tools/check_tutorial.py extract_commands / _iter_fenced_blocks`: the tutorial's command list is already extractable from the mdx with nocheck fences skipped; the tracer can reuse it instead of hand-listing steps, keeping the tutorial the single source of the run
  - seeds: `c4`
- `s4` — `Artifact tool contract (wraps a fragment; forbids doctype/html/head/body) vs a local static site`: one body serves both targets; only the outer skeleton differs, so the generator emits the fragment and a wrapped copy rather than two page sources
  - seeds: `c5`
- `s5` — `pyproject [dependency-groups].dev + uv.lock (no pillow) + .claude/skills/operate-microduck (screenshot recipe)`: the scratch tracer's PIL crop cannot land as-is; the dev group has no image library and the operator skill already prescribes gnome-screenshot -w for a window-only capture
  - seeds: `c6`
- `s6` — `.github/workflows/tests.yml lint job + [tool.coverage.run] source`: CI lints `microduck_cli` and tests only, so docs/tools would drift unlinted unless the paths are added; coverage is scoped to the package, so the tool's tests do not move the gate
  - seeds: `c7` (rejected)
- `s7` — `docs/verification/2026-09-07-spark-retest.md + 2026-09-08 record (honest-failure convention)`: records keep first attempts and failures verbatim; the generated page must keep the same discipline or it contradicts the record it illustrates
  - seeds: `c8`
- `s8` — `.gitignore + markdownlint globs (#.local)`: .local is already the convention for untracked local artefacts in this repo's lint config, so run directories belong there and stay out of PRs
  - seeds: `c9` (rejected)
- `s9` — `CLAUDE.md sibling-repos section (cite-don't-import, extraction seams)`: a generic trace library is the neurosymbolic-system extraction question again; keep the map duck-specific now and let a sibling cite the pattern
  - seeds: `c10`
- `s10` — `CHANGELOG.md + tests.yml version-check`: docs/tooling-only PRs still need the bump; the entry documents the tool and the two output files
  - seeds: `c11`
- `s11` — `.claude/skills/operate-microduck/SKILL.md (Watch it section) + Spark/Thor/Orin display state`: the display-owner discovery recipe is already written down; the tracer automates it and degrades to headless rather than failing
  - seeds: `c12`

## Decisions

- USER DECISION: trace is a CLI noun, microduck trace, with its own overview (rubric) — not a docs/tools script; it is relevant to operators and agents, not only to the tutorial
- USER DECISION: a trace covers ANY CLI work — a tutorial run, the post-run checks, an exploratory session, or an import of an existing run directory — not only tutorial steps 1-11
- USER DECISION: pausing the desktop auto-lock for a windowed run is opt-in (a flag), restored afterwards; without the flag the tool warns when lock is armed and proceeds
- USER DECISION: the PR ships the generator and today's page as a committed example; publishing to a Claude artifact stays an agent step on the fragment

## Open parks

- [unknown_nonblocking] the first env up --sim failure on 2026-09-08 (body exited silently) is unexplained; a tracer that retries once will keep hitting it and the page will keep showing it — root cause is outside this tool
- [unknown_nonblocking] whether the generated page should be a committed sample (docs/tools/sample-trace/index.html, ~600 KB with frames) or only regenerated on demand — repo weight vs a browsable example
