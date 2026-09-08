# Delivery Summary — run-trace tool

plan: `run-trace-tool` · run: `complete` · date: `2026-09-08`
baseline: `devague summary skeleton` (t9/t10 quoted from `devague plan show`, which the skeleton omitted)

## Intent

Ship the run-trace method as a first-class CLI noun: `microduck trace` records any CLI
work (a tutorial run, post-run checks, an exploratory session, or an imported run
directory) as one timestamped event stream and renders it as the MicroDuck Run Trace
page, standalone (`index.html`) and as an Artifact fragment (`trace.html`), byte-for-byte
reproducible. Spec `docs/specs/2026-09-08-run-trace-tool.md`; plan
`docs/plans/2026-09-08-run-trace-tool.md`; contract
`docs/plans/2026-09-08-run-trace-tool.contract.md`. Executed by `/assign-to-workforce`
in five waves with TDD-gated merges, then one review round.

## Planned Work

Quoted verbatim from the `devague summary` skeleton (`t9`, `t10` from `devague plan show`):

- `t1` — trace/events.py — the one event schema (t, wall, lane, kind, label, step, per-kind fields), append()/load()/validate(), the run-dir layout helper, and `import_run`(src, dst) that adopts an external events.jsonl + shots dir (validating each line, first bad line as CliError); `microduck_cli`/trace/`__init__.py`; tests/`test_trace_events.py`
- `t2` — trace/plan.py — the TOML plan (ordered steps: step, label, cmd, `sleep_before`/after, shot, retry, note) and `from_tutorial`(`mdx_text`, sidecar) that extracts fenced bash commands with the same rules as docs/tools/`check_tutorial.py` (nocheck skipped, comments stripped, uv-run prefix kept as typed) and merges the sidecar's sleeps/frames/retries by command match; tests/`test_trace_plan.py`
- `t3` — trace/render.py — the MicroDuck Run Trace page as a deterministic template: render(`run_dir`) -> (`trace_html` fragment starting with <title>, `index_html` = skeleton + fragment), frames inlined as data URIs in filename order, CSS object-position crop from recorded geometry, the tier map + timeline + captions + step ladder + checks table, failed spans in the failure colour beside retries, headless frame panel with the reason, interpolated positions labelled; tests/`test_trace_render.py`
- `t4` — rejected during planning (a duplicate of `t3`)
- `t5` — trace/capture.py — display-owner discovery (DISPLAY/XAUTHORITY/DBUS from the gnome-shell process environ, seam-injected), window-only capture (focus 'MuJoCo : scene' with xdotool/wmctrl when present, then gnome-screenshot -w) with the fallback full-screen shot plus xwininfo geometry recorded on the shot event, headless detection, and the opt-in autolock pause/restore helper (idle-delay, lock-enabled) that always restores — USER DECISION c15: opt-in flag only; tests with fake runners
- `t6` — trace/runner.py — Recorder: run one command via bash -c with stdin=/dev/null, stdout to steps/NN-name.out, stderr streamed line by line to events with timestamps (\[SENSE lines to the engine lane), cmd-start/cmd-end with rc, elapsed, first stdout line; a log-tail thread for the robotd and body logs under `DUCK_SIM_STATE`; `run_plan`(plan, `run_dir`, shot=capture) executing steps with sleeps, frames and one retry when the plan says so; `exec_one`(argv, `run_dir`) so any CLI work is traced (USER DECISION c14); serve.py (ThreadingHTTPServer on 127.0.0.1, prints the URL); tests with fake commands
- `t7` — cli/`_commands`/trace.py — the trace noun group (`parser_class`=type(p)) with overview, run, exec, plan, import, render, serve, list (USER DECISION c13); every verb has --json and raises CliError; explain/trace.py catalog entries for (trace,) and each verb; overview.py `_VERBS` row; learn.py `_TEXT` + `_as_json_payload`; tests/`test_cli_trace.py`; teken cli doctor --strict passes
- `t8` — docs/traces/2026-09-08-spark-tutorial/ — the committed example (USER DECISION c16): import today's scratch events.jsonl and 12 frames (home paths redacted), render index.html + trace.html, and tests/`test_trace_example.py` that re-renders the directory and diffs byte-for-byte against the committed pages
- `t9` — docs: CLAUDE.md (trace package in the packages table, the noun in the architecture section, the autolock flag), README pointer, operate-microduck SKILL.md 'Watch it' pointing at trace run/exec, docs/traces/tutorial.sidecar.toml (the sleeps, frames and the single env up retry for the Jetson AI Lab tutorial), CHANGELOG entry via the version-bump skill (patch)
- `t10` — acceptance on Spark: from the branch, microduck trace plan --from-tutorial <fork mdx> --sidecar docs/traces/tutorial.sidecar.toml, one fresh windowed trace run --plan (with --pause-autolock), trace render, trace serve; open the PR with the after-state walk-through and every success-signal clause mapped to a test or a recorded command; ask-colleague review; cicd open

## Actual Delivery

| Plan task | Status | What actually landed |
|-----------|--------|----------------------|
| `t1` | delivered | `microduck_cli/trace/events.py` (`1bacd2f`), hardened in `1e09855` (same-second run dirs, self-import refusal, stale-frame clearing, validate-before-write, meta redaction, `remove_empty_run_dir`, `list_runs`/`update_meta` added by `5aa992a`) |
| `t2` | delivered | `microduck_cli/trace/plan.py` (`5d6efd7`); `_validate_step` on load and after sidecar merge (`0361879`) |
| `t3` | delivered | `microduck_cli/trace/render.py` + `template.html` (`4eaeadc`); crop fix `ece3bbf`; `_page_meta` normalisation, one event loader, script split (`c095e9a`) |
| `t4` | dropped | rejected at planning as a duplicate of `t3`; nothing owed |
| `t5` | delivered | `microduck_cli/trace/capture.py` (`d604a1b`); shot-name validation, display env for autolock, real focus check, signed geometry (`0e58260`) |
| `t6` | delivered | `microduck_cli/trace/runner.py` + `serve.py` (`fb43894`); line-buffered log tail, process-group kill, loopback-only serve, `run_traced` (`62f392e`, `5aa992a`) |
| `t7` | delivered | `microduck_cli/cli/_commands/trace.py`, `explain/trace.py`, catalog/overview/learn lockstep (`bbafda9`); thin-wiring split, failed-import cleanup, `--allow-remote` (`5aa992a`) |
| `t8` | delivered | `docs/traces/2026-09-08-spark-tutorial/` with `tests/test_trace_example.py` (`6125b87`); re-rendered twice for template changes (`ece3bbf`, `c095e9a`) |
| `t9` | delivered | CLAUDE.md, README, operate-microduck skill, `docs/traces/tutorial.sidecar.toml`, CHANGELOG 0.9.7 (`a00d58c`, `f0b2610`) |
| `t10` | delivered | fresh windowed run `~/.cache/duck-sim/trace/20260908T153635Z` (23 steps, 0 failures, 0 retries, 9 frames); PR #11 opened with the success-signal map; colleague review folded in; Qodo round answered |

## Mid-work Decisions

No `/deviate` records were written during this run; the decisions below are captured directly.

- The wave-1 `__init__.py` stub was created by four agents in parallel; the merge kept `t1`'s and discarded the others — a file-disjointness gap the contract did not name.
- `t3` generalised the page beyond the plan: hard-coded Spark strings and the Jerusalem clock offset became `meta.json` fields; the operator cwd-slip span was dropped from the committed example events (995 lines, not 997).
- `t6` records a timeout as a `note` plus `cmd-end rc=-9` rather than a `timeout` kind, because the event schema (`t1`) had no such kind — the contract's two halves disagreed and the schema won.
- `t9` attached the "before env down" frame to `rules intent stop`, because the runner only shoots after a step.
- `t10` edited the derived plan by hand before running it: Step 1 installs 0.9.6 (the current release) instead of the tutorial's 0.9.4 pin, and the two `env up` variants plus the Thor-tab smoke were dropped as alternatives, not a sequence. The edits are recorded in the plan's `meta.operator_edits`.
- The one look at the rendered fresh run found a real defect (the Artifact host's `img{max-width:100%}` capped geometry-cropped frames) and it was fixed before the PR opened.
- A review round the plan did not schedule (colleague + Qodo + SonarCloud) produced six fix-up branches merged with the same TDD gate; `trace serve` gained `--allow-remote` and refuses non-loopback hosts by default.

## Drift From Plan

| Plan item | Reason for divergence | Classification |
|-----------|-----------------------|----------------|
| `t3` | page strings and clock moved into `meta.json`; the example events drop one operator-slip span | acceptable |
| `t6` | no `timeout` event kind; recorded as note + `rc=-9` | acceptable |
| `t9` | "before env down" frame attached to the preceding step | acceptable |
| `t10` | the traced plan was hand-edited (wheel version, three alternative steps removed) before the run | acceptable |
| `t10` | the plan named `ask-colleague review; cicd open` in that order; the PR opened while the review was still running and its findings landed as a follow-up commit | acceptable |
| `t5`/`t6`/`t7` | behaviour added after review (shot-name validation, loopback-only serve, process-group kill, thin-wiring split) that the confirmed tasks did not specify | acceptable |

## Evidence

- tests: `uv run pytest -n auto -q --cov=microduck_cli` at `f0b2610` — 1364 passed, 93 % (gate 60)
- tests: `tests/test_trace_example.py` — pass (byte-for-byte re-render of the committed example)
- lint: `black --check`, `isort --check-only`, `flake8`, `bandit -c pyproject.toml -r microduck_cli` — clean; `teken cli doctor . --strict` — 26/26; `markdownlint-cli2` — 0 errors
- CI on PR #11 at `f0b2610`: test, lint, version-check, test-publish, GitGuardian, SonarCloud Code Analysis — pass; SonarCloud Quality Gate OK
- reviews: colleague work item `ffde017a0177` (no blocking defects; two suggestions applied); Qodo 33 inline threads over two rounds, all replied to and resolved (one pushback)
- commits: `09d0c7e..2a9b963` on `feat/trace-noun` (42 commits)
- PRs: #11
- runs: `~/.cache/duck-sim/trace/20260908T153635Z` (outside the tree); artifact of that run: <https://claude.ai/code/artifact/1bc089d7-449f-4ba1-963a-e74e6e511634>

## Delivery Claims

| Claim | Confidence | Evidence |
|-------|------------|----------|
| `microduck trace` exists with eight verbs and passes the rubric | high | `microduck_cli/cli/_commands/trace.py` · `teken cli doctor . --strict` 26/26 · `tests/test_cli_trace.py` |
| the committed example re-renders byte-for-byte | high | `tests/test_trace_example.py` · `docs/traces/2026-09-08-spark-tutorial/` |
| rendering is deterministic (no clock, environ or random) | high | `tests/test_trace_render.py` twice-render and no-environ tests |
| the package is stdlib-only and the wheel keeps `dependencies = []` | high | `tests/test_trace_events.py` import walk · `pyproject.toml` |
| steps run verbatim and nothing injects `--apply` | high | `tests/test_trace_runner.py` (source grep + recorded label) |
| a fresh windowed `trace run --plan` on Spark yields a scrubbable page | high | run dir above · the artifact above (one look taken after the crop fix) |
| the page's frame crop is correct on a box without a focus tool | medium | one screenshot of the artifact after `ece3bbf`; no automated pixel test |
| `--pause-autolock` pauses and restores the real session lock over SSH | medium | `autolock_paused: true` in the run result and gsettings back at 300/true afterwards; the display-env plumbing (`0e58260`) landed after that run and is covered by fakes only |
| window-only capture via xdotool/wmctrl works | unverified | neither tool is installed on Spark; only the geometry fallback ran for real |

## Remaining Work / Follow-up

- Qodo's second round posted 17 threads (15 bugs, 2 rule violations); 16 were fixed on this branch in four more file-disjoint branches (`f466ce6`, `6844aae`, `5f9d92b`, `b77fef8`) and one (overlong CLI lines) was pushed back with evidence — no line exceeds 100 characters and black/flake8 pass. All threads replied to and resolved.
- The jetson-ai-lab tutorial still pins `microduck-cli==0.9.4` in Step 1; bump to the current release in the fork branch (separate PR).
- Frames captured full-screen are ~400 KB PNGs each (4.9 MB page for the fresh run); a downscale option needs an image library and is deliberately out of this PR.
- The first `env up --sim` failure from the morning run (body exited silently) remains unexplained; it did not recur in the traced run.
- Window-only capture with a focus tool is untested on real hardware; install `xdotool` on one box and record a run to verify.
