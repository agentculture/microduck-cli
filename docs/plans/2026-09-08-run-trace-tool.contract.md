# run-trace tool — the module contract wave 1 builds to

Plan: `docs/plans/2026-09-08-run-trace-tool.md`. Package: `microduck_cli/trace/`
(stdlib only; may import `microduck_cli.cli._errors` for `CliError`/exit codes
and nothing else from `cli/`).

## Run directory

```text
<run-dir>/
  meta.json        {"t0_wall": float epoch, "title": str, "box": str, "notes": [str], ...free keys}
  events.jsonl     one JSON object per line, see below; append-only
  plan.toml        the plan that was run (absent for exec/import runs)
  steps/NN-<slug>.out|.err   captured stdout / stderr per command
  shots/<name>.jpg|png       viewer frames, named by the shot event's "file"
  trace.html       Artifact fragment (starts with <title>, no doctype/html/head/body)
  index.html       standalone document = skeleton + the same fragment
```

Default location: `<DUCK_SIM_STATE or ~/.cache/duck-sim>/trace/<YYYYMMDDTHHMMSSZ>/`.

## Event line

Required: `t` (float, seconds since `meta.t0_wall`), `wall` (float epoch),
`lane` in `operator|cli|engine|robotd|body|train|viewer|checks|microduck|rl`,
`kind` in `cmd-start|cmd-end|stderr|log|shot|note`, `label` (str ≤ 400 chars).
Optional: `step` (str or null).
Per kind:

- `cmd-end`: `rc` int, `elapsed` float, `stdout_first` str, `stdout_lines` int,
  `stderr_lines` int, `out` (path relative to run dir).
- `stderr` in lane `engine`: `ev` (the `event=` token), `stage`; optional `n`
  (a thinned count for one 0.2 s bucket).
- `shot`: `file` (relative path under `shots/`), `window` bool (true = the
  capture is the viewer window only), `size` [w, h]; optional `geometry`
  `{"x","y","w","h","screen":[W,H]}` when `window` is false (the page crops).
- `log`: raw daemon log line with ANSI stripped (lane `robotd` or `body`).

## Public names (import these; do not rename)

- `microduck_cli/trace/events.py`: `LANES`, `KINDS`, `class Event` (dataclass,
  `to_json()`/`from_dict()`), `class RunDir` (`path`, `.events_path`, `.steps`,
  `.shots`, `.meta_path`, `.read_meta()`, `.write_meta(dict)`, `.ensure()`),
  `new_run_dir(state_dir: str, now: float) -> RunDir`,
  `append_event(run_dir: RunDir, event: Event) -> None`,
  `load_events(run_dir: RunDir) -> list[Event]` (raises `CliError` naming the
  first bad line), `validate_line(obj: dict, lineno: int) -> Event`,
  `import_run(src: str, dst: RunDir, *, redact_home: bool = True) -> int`
  (copies events + shots + meta, rewrites `/home/<user>` → `~`, returns count).
- `microduck_cli/trace/plan.py`: `class Step` (`step: str`, `label: str`,
  `cmd: str`, `lane: str = "cli"`, `sleep_before: float = 0`,
  `sleep_after: float = 0`, `shot: str | None`, `retry: int = 0`,
  `note: str | None`, `timeout_s: float | None`), `class Plan` (`title`,
  `steps: list[Step]`, `meta: dict`), `load_plan(text: str) -> Plan`,
  `dump_plan(plan: Plan) -> str`, `from_tutorial(mdx_text: str,
  sidecar: dict | None = None) -> Plan` (fenced `bash`/`sh` commands, `nocheck`
  fences skipped, trailing `# comments` stripped, same rules as
  `docs/tools/check_tutorial.py::extract_commands`; sidecar keys match by exact
  command string; unmatched sidecar entries are returned in `plan.meta["unmatched"]`).
- `microduck_cli/trace/capture.py`: `class DisplayEnv` (`display`, `xauthority`,
  `dbus`), `discover_display(*, environ=os.environ, proc_environs=<callable>)
  -> DisplayEnv | None`, `class ShotOutcome` (`ok`, `file`, `window`, `size`,
  `geometry`, `reason`), `capture_frame(run_dir: RunDir, name: str, *, env:
  DisplayEnv | None, runner=subprocess.run) -> ShotOutcome` (headless → `ok=False,
  reason="headless"`, never raises), `pause_autolock(runner=subprocess.run)`
  (context manager; reads then restores `org.gnome.desktop.session idle-delay`
  and `org.gnome.desktop.screensaver lock-enabled`, restore in `finally`).
- `microduck_cli/trace/render.py`: `class Rendered` (`trace_html: str`,
  `index_html: str`), `render(run_dir: RunDir) -> Rendered` (pure: no clock, no
  environ, frames embedded as data URIs in sorted filename order),
  `write(run_dir: RunDir) -> tuple[str, str]` (writes both files, returns paths).
- wave 2, `microduck_cli/trace/runner.py`: `class Recorder(run_dir, clock=time.time)`,
  `run_plan(plan, run_dir, *, shot=capture_frame, ...)`, `exec_one(argv, run_dir)`;
  `serve.py`: `serve(run_dir, port=0) -> url`.

## Rules every task keeps

- Tests live in `tests/test_trace_<module>.py`; fakes for every subprocess.
- No third-party imports; `pyproject` `dependencies = []` unchanged.
- black/isort/flake8 at line length 100; bandit clean (subprocess with a list
  argv or `shell=True` only where the plan's command is meant to be a shell line).
- Do not touch `cli/`, `explain/`, `learn.py`, `overview.py`, `CLAUDE.md`,
  `CHANGELOG.md` or `pyproject.toml` in wave 1 — later tasks own them.
