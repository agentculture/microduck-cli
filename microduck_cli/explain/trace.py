"""Explain entries and verb list for the ``trace`` noun.

Adding a ``trace`` verb means editing this module — ``VERBS`` (what ``trace
overview`` lists) *and* ``ENTRIES`` (what ``explain trace <verb>`` renders) —
alongside ``cli/_commands/trace.py`` and ``tests/test_cli_trace.py``. Those are
the usual three, not a guarantee of "nothing else": the root ``explain`` page in
:mod:`microduck_cli.explain.catalog` also names this noun's verbs in prose, and a
verb that changes the noun's shape belongs there too. See
:mod:`microduck_cli.explain.env` for the shared conventions.

The engine behind these verbs lives in :mod:`microduck_cli.trace` (events, plan,
capture, render, runner, serve); the noun module is argparse wiring only.
"""

from __future__ import annotations

#: The one-liner that says what a trace adds to a verification record.
TAGLINE = (
    "a verification record says what happened; the trace shows which tier did it "
    "and when, from the same timestamps"
)

#: Who the noun is for. Kept as one string so the CLI overview and the catalog
#: entry cannot drift apart.
AUDIENCE = (
    "operators and agents driving the duck who want a shareable, honest replay "
    "of a session — the tutorial's authors and reviewers first"
)

VERBS: list[str] = [
    "trace overview — describe the trace noun (a shareable, honest replay of a session)",
    "trace plan — build a run plan from a tutorial's fenced commands, sidecar-augmented",
    "trace run — execute a plan, recording every command, log line and viewer frame",
    "trace exec — trace one arbitrary command into a run dir (new, newest or --run)",
    "trace import <src-dir> — adopt an external run directory (redacting home paths) and render it",
    "trace render <run-dir> — regenerate index.html and trace.html from the recorded events",
    "trace serve <run-dir> — serve a rendered run dir over loopback HTTP "
    "(--allow-remote to bind off-box: no auth, no TLS)",
    "trace list — the run dirs under <state>/trace, their event counts and whether they rendered",
]

_TRACE = f"""\
# microduck-cli trace

Noun group for the **run trace**: re-run a session's commands, record every
command, daemon log line and viewer frame with wall-clock stamps, and render
the MicroDuck Run Trace page from what was recorded — a standalone
`index.html` and a Claude Artifact fragment `trace.html`.

Audience: {AUDIENCE}.

Why it exists: {TAGLINE}.

## The run directory

    <run-dir>/
      meta.json      t0_wall, title, box, notes
      events.jsonl   one JSON object per line, append-only
      plan.toml      the plan that was run (absent for exec/import runs)
      steps/         per-command stdout / stderr
      shots/         viewer frames
      trace.html     Artifact fragment (no doctype/html/head/body)
      index.html     standalone document = skeleton + the same fragment

The default location is `<DUCK_SIM_STATE or ~/.cache/duck-sim>/trace/<UTC stamp>/`;
`--out` overrides it and `--state` overrides the state directory.

## Usage

    microduck-cli trace overview
    microduck-cli trace plan --from-tutorial tutorial.mdx --sidecar sidecar.toml
    microduck-cli trace run --plan plan.toml --headless
    microduck-cli trace exec -- microduck duck health
    microduck-cli trace import ./scratch-run --out ./docs/traces/example
    microduck-cli trace render ./docs/traces/example
    microduck-cli trace serve ./docs/traces/example --port 8000
    microduck-cli trace list --json

## Honesty rules

- A plan line runs **verbatim**: nothing appends `--apply`, and the recorder
  runs with `stdin=/dev/null` so no gated verb can prompt. A dry run on a pipe
  moved nothing, and the page records exactly that.
- A failed step is not a failed run: every attempt keeps its own
  `cmd-start`/`cmd-end` pair, so a first failure stays on the page beside its
  retry, and `trace run` still exits 0 (the page is the record).
- Frames are captured only when a display was found. Headless is recorded as a
  note with its reason, never silently skipped.

## See also

- `microduck-cli explain trace run`
- `microduck-cli explain trace import`
- The committed example: `docs/traces/2026-09-08-spark-tutorial/`
"""

_OVERVIEW = f"""\
# microduck-cli trace overview

Read-only description of the `trace` noun: who it is for
({AUDIENCE}), what it produces (a run directory plus the two rendered pages)
and which verbs exist. Descriptive, so it never hard-fails — a stray positional
argument is accepted and ignored, and the verb still exits 0.

## Usage

    microduck-cli trace overview
    microduck-cli trace overview --json
"""

_PLAN = """\
# microduck-cli trace plan

Build a run plan from a tutorial's fenced `bash`/`sh` commands, using the same
extraction rules as `docs/tools/check_tutorial.py` (a `nocheck` fence is
skipped, a whole-line `# comment` is dropped, a `$ ` prompt prefix and a
trailing backslash continuation are folded away, a `uv run` prefix is kept as
typed). Each command becomes one step, labelled with the command as written and
tagged with the enclosing `## Step N` heading.

A **sidecar** TOML is a table keyed by the *exact* command string, whose values
override that step's fields (`sleep_before`, `sleep_after`, `shot`, `retry`,
`note`, `timeout_s`, `lane`). A sidecar key that matches no extracted command is
never silently dropped: it is reported on stderr and listed in the plan's
`meta.unmatched`.

## Usage

    microduck-cli trace plan --from-tutorial tutorial.mdx
    microduck-cli trace plan --from-tutorial tutorial.mdx --sidecar sidecar.toml
    microduck-cli trace plan --from-tutorial tutorial.mdx --out plan.toml
    microduck-cli trace plan --from-tutorial tutorial.mdx --json

Without `--out` the plan TOML is printed to stdout; with it, the file is written
and its path is printed. `--json` emits the plan as an object
(`{"title", "steps": [...], "meta": {...}}`).

## Exit codes

- `0` the plan was built (unmatched sidecar keys are diagnostics, not failures)
- `1` the tutorial or sidecar could not be read or parsed
"""

_RUN = """\
# microduck-cli trace run

Execute a plan step by step into a run directory, recording each command's
`cmd-start`/`cmd-end` (with rc, elapsed and the first stdout line), its stderr
line by line, the robotd and body log tails under the state directory, and a
viewer frame wherever the plan asks for one.

Steps run **verbatim**. Nothing appends `--apply`; stdin is `/dev/null`, so a
gated verb in the plan can never prompt and every gate decision belongs to the
plan's author.

Display handling: unless `--headless`, the graphical session's display is
discovered (the current environment, else the `gnome-shell` process environ).
When no display is found the run continues headless and records a note saying
so — it is never a hard failure.

`--pause-autolock` is **opt-in**: it pauses `idle-delay` and `lock-enabled`
for the run and restores both in a `finally`, even if the run raises. On a TTY
you are asked to confirm before it touches your desktop settings; on a pipe the
flag itself is the explicit consent (that is why it is a flag and not a
default), so the run proceeds without a prompt.

## Usage

    microduck-cli trace run --plan plan.toml
    microduck-cli trace run --plan plan.toml --out ./run --headless
    microduck-cli trace run --plan plan.toml --state /tmp/duck --title "Spark tutorial"
    microduck-cli trace run --plan plan.toml --pause-autolock --json

## Exit codes

- `0` the plan ran — **including when steps failed**; the failures are on the
  page and in the printed summary (`steps run`, `failures`, `retried`)
- `1` the plan itself was unreadable or invalid, or the run dir could not be made
"""

_EXEC = """\
# microduck-cli trace exec

Trace one arbitrary command into a run directory, so exploratory or post-run
work lands in the same `events.jsonl` as a planned run. The renderer never
learns which of the two produced a record.

The run directory is `--run` when given, else the newest directory under
`<state>/trace`, else a fresh one.

## Usage

    microduck-cli trace exec -- microduck duck health
    microduck-cli trace exec --run ./run -- printf 'hi\\n'
    microduck-cli trace exec --state /tmp/duck --json -- microduck env status

Everything after `--` is the command, passed through unchanged.

## Exit codes

- `0` the command was traced — **whatever it exited with**. Its own exit status
  is reported as `rc` in the result (text and JSON), because the trace succeeded
  in recording a failure.
- `1` there was no command to run, or the run directory could not be used
"""

_IMPORT = """\
# microduck-cli trace import

Adopt an external run directory — an `events.jsonl`, optionally a `shots/`
directory and a `meta.json` — into a run directory of your own, then render it.
Every event line is validated first: the first bad line raises an error naming
its line number and nothing is written.

By default every `/home/<user>` occurrence in an event's label and per-kind
string fields is rewritten to `~`, which is what makes a scratch run safe to
commit. `--no-redact` keeps the paths verbatim.

## Usage

    microduck-cli trace import ./scratch-run
    microduck-cli trace import ./scratch-run --out ./docs/traces/example
    microduck-cli trace import ./scratch-run --no-redact --json

## Exit codes

- `0` the events were adopted and the pages rendered
- `1` the source had no `events.jsonl`, or a line failed validation
"""

_RENDER = """\
# microduck-cli trace render

Regenerate `index.html` and `trace.html` from a run directory's recorded events
and frames. The render is **pure**: it reads no clock and no environment, and
frames are embedded as data URIs in sorted filename order — so rendering the
same run directory twice yields identical bytes. That is what the committed
example (`docs/traces/2026-09-08-spark-tutorial/`) asserts in CI.

`trace.html` is the Claude Artifact fragment (it starts with `<title>` and
carries no doctype/html/head/body); `index.html` is the standalone document.

## Usage

    microduck-cli trace render ./docs/traces/2026-09-08-spark-tutorial
    microduck-cli trace render ./run --json

## Exit codes

- `0` both pages were written; their paths and sizes are printed
- `1` the run directory does not exist or its events are invalid
"""

_SERVE = """\
# microduck-cli trace serve

Serve a rendered run directory over HTTP from a loopback socket, so the page's
frames and scrubber work in a browser. The URL is printed to stdout **first**,
before the server blocks, so an agent reading the stream always has it. Port `0`
(the default) lets the OS pick a free port.

Ctrl-C stops the server cleanly and exits 0. `--check` binds, prints the URL and
stops immediately — the non-blocking form used in tests and in health checks.

## Binding off-box

The bind is loopback-only by default and a non-loopback `--host` (`0.0.0.0`, a
LAN address) is **refused** unless you also pass `--allow-remote`: this server
has no auth and no TLS, and a run dir is a whole session's output — commands,
stderr and viewer frames. `--allow-remote` is the flag that says you mean it;
use it only on a trusted network.

## Usage

    microduck-cli trace serve ./run
    microduck-cli trace serve ./run --port 8000 --host 127.0.0.1
    microduck-cli trace serve ./run --host 0.0.0.0 --allow-remote --port 8000
    microduck-cli trace serve ./run --check --json

## Exit codes

- `0` the server bound (and, without `--check`, was stopped by Ctrl-C)
- `1` the run directory does not exist, or a non-loopback `--host` was asked
  for without `--allow-remote`
- `2` the address could not be bound (port in use, host unavailable)
"""

_LIST = """\
# microduck-cli trace list

The run directories under `<state>/trace`, newest name last, each with its
recorded event count and whether it has been rendered (`index.html` present).
Descriptive: a missing state directory is reported as an empty list, not an
error.

## Usage

    microduck-cli trace list
    microduck-cli trace list --state /tmp/duck --json
"""

ENTRIES: dict[tuple[str, ...], str] = {
    ("trace",): _TRACE,
    ("trace", "overview"): _OVERVIEW,
    ("trace", "plan"): _PLAN,
    ("trace", "run"): _RUN,
    ("trace", "exec"): _EXEC,
    ("trace", "import"): _IMPORT,
    ("trace", "render"): _RENDER,
    ("trace", "serve"): _SERVE,
    ("trace", "list"): _LIST,
}
