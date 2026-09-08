"""``microduck-cli trace`` — record a session and render the MicroDuck Run Trace page.

Thin argparse wiring only. Every loop, subprocess, socket and rendering decision
lives in :mod:`microduck_cli.trace` (``events``, ``plan``, ``capture``,
``render``, ``runner``, ``serve``); this module resolves paths, calls one public
function per verb and formats the result. It starts no thread and runs no loop of
its own — ``trace serve`` waits on the handle the ``serve`` module already owns.

Mirrors :mod:`microduck_cli.cli._commands.cli` and
:mod:`~microduck_cli.cli._commands.rules`: the nested subparsers are built with
``parser_class=type(p)`` so a parse error under this noun keeps the structured
``error:``/``hint:`` contract instead of argparse's default exit 2, and a bare
``microduck-cli trace`` prints this noun's overview.

The verbs
---------

* ``trace plan`` — build a plan from a tutorial's fenced commands; an unmatched
  sidecar key is a stderr diagnostic and a ``meta.unmatched`` entry, never a
  silent drop.
* ``trace run`` — execute a plan into a run dir and render it. Exits **0 even
  when steps failed**: the failures are the record. Only an invalid plan (or an
  unusable run dir) is an error. Nothing ever appends ``--apply`` to a step.
* ``trace exec`` — trace one arbitrary command into a run dir. The command's own
  exit status is reported as ``rc``; the verb exits 0 because the *trace*
  succeeded.
* ``trace import`` — adopt an external run dir (home paths redacted by default)
  and render it.
* ``trace render`` / ``trace serve`` / ``trace list`` — regenerate the pages,
  serve them over loopback, and list what has been recorded.

What is deliberately *not* here
------------------------------
Nothing in this module lists a directory, counts events, edits a ``meta.json``
or decides how a run is wrapped. Those are the package's:
:func:`~microduck_cli.trace.events.list_runs`,
:func:`~microduck_cli.trace.events.count_shots`,
:func:`~microduck_cli.trace.events.update_meta` /
:func:`~microduck_cli.trace.events.append_note`,
:func:`~microduck_cli.trace.events.ensure_run_dir` and
:func:`~microduck_cli.trace.runner.run_traced`, which owns the
autolock-pause-and-display policy of a traced run. What stays here is what is
caller-facing: parsing argv, asking the operator for consent on a TTY, choosing
the exit code and formatting the result.

Test seams
----------
Module attributes a test monkeypatches, mirroring ``_commands/rules.py``:
``_clock`` (the wall clock a new run dir is stamped with), ``_discover_display``,
``_pause_autolock``, ``_run_traced``, ``_exec_one``, ``_serve`` and ``_isatty``.
"""

from __future__ import annotations

import argparse
import sys
import time
import tomllib
from pathlib import Path
from typing import Any, Callable

from microduck_cli.cli._commands.overview import emit_overview
from microduck_cli.cli._errors import EXIT_ENV_ERROR, EXIT_SUCCESS, EXIT_USER_ERROR, CliError
from microduck_cli.cli._output import PROG, STATE_DIR_HELP, emit_diagnostic, emit_result
from microduck_cli.duck.gate import Consent, confirm_on_tty, consent
from microduck_cli.explain.trace import AUDIENCE, TAGLINE, VERBS
from microduck_cli.trace.capture import capture_frame, discover_display, pause_autolock
from microduck_cli.trace.events import (
    TRACE_SUBDIR,
    RunDir,
    append_note,
    count_shots,
    ensure_run_dir,
    import_run,
    list_runs,
    new_run_dir,
    newest_run,
    remove_empty_run_dir,
    update_meta,
)
from microduck_cli.trace.plan import Plan, dump_plan, from_tutorial, load_plan
from microduck_cli.trace.render import write as render_write
from microduck_cli.trace.runner import default_state_dir, exec_one, run_traced
from microduck_cli.trace.serve import serve as serve_run_dir

_SUBJECT = f"{PROG} trace"
_PURPOSE = (
    "Re-run a session's commands, record every command, log line and viewer frame "
    "with wall-clock stamps, and render the MicroDuck Run Trace page from them."
)

# ---------------------------------------------------------------------------
# Test seams
# ---------------------------------------------------------------------------

_clock: Callable[[], float] = time.time
_discover_display = discover_display
_pause_autolock = pause_autolock
_run_traced = run_traced
_exec_one = exec_one
_serve = serve_run_dir


def _isatty() -> bool:
    return sys.stdin.isatty()


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _add_json_flag(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--json", action="store_true", help="Emit structured JSON.")


def _json_mode(args: argparse.Namespace) -> bool:
    return bool(getattr(args, "json", False))


def _state_dir(args: argparse.Namespace) -> str:
    return getattr(args, "state", None) or default_state_dir()


def _read_text(path: str, what: str) -> str:
    try:
        return Path(path).read_text(encoding="utf-8")
    except OSError as exc:
        raise CliError(
            EXIT_USER_ERROR,
            f"{what} unreadable: {path}: {exc.strerror or exc}",
            f"check the path and permissions, then re-run '{PROG} trace'",
        ) from exc


def _read_sidecar(path: str | None) -> dict[str, Any] | None:
    if not path:
        return None
    text = _read_text(path, "sidecar")
    try:
        data = tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:
        raise CliError(
            EXIT_USER_ERROR,
            f"invalid sidecar TOML: {path}: {exc}",
            "a sidecar is a table keyed by the exact command string; fix the syntax",
        ) from exc
    return data


def _existing_run_dir(path: str) -> RunDir:
    run_path = Path(path)
    if not run_path.is_dir():
        raise CliError(
            EXIT_USER_ERROR,
            f"no such run directory: {path}",
            f"list the recorded runs with '{PROG} trace list'",
        )
    return RunDir(run_path)


def _make_run_dir(out: str | None, args: argparse.Namespace) -> RunDir:
    """``--out`` when given (created), else a fresh ``<state>/trace/<stamp>``."""
    try:
        if out:
            return ensure_run_dir(out, _clock())
        return new_run_dir(_state_dir(args), _clock())
    except OSError as exc:
        raise CliError(
            EXIT_ENV_ERROR,
            f"cannot create the run directory: {exc.strerror or exc}",
            "pass a writable --out, or set --state to a writable directory",
        ) from exc


def _sizes(paths: tuple[str, str]) -> list[dict[str, Any]]:
    return [{"path": p, "bytes": Path(p).stat().st_size} for p in paths]


def _plan_payload(plan: Plan) -> dict[str, Any]:
    return {
        "title": plan.title,
        "steps": [
            {
                "step": s.step,
                "label": s.label,
                "cmd": s.cmd,
                "lane": s.lane,
                "sleep_before": s.sleep_before,
                "sleep_after": s.sleep_after,
                "shot": s.shot,
                "retry": s.retry,
                "note": s.note,
                "timeout_s": s.timeout_s,
            }
            for s in plan.steps
        ],
        "meta": dict(plan.meta),
    }


def _render(run_dir: RunDir) -> tuple[str, str]:
    """``render.write`` with every failure translated to a :class:`CliError`."""
    try:
        return render_write(run_dir)
    except CliError:
        raise
    except OSError as exc:
        raise CliError(
            EXIT_ENV_ERROR,
            f"cannot write the rendered pages: {exc.strerror or exc}",
            "check that the run directory is writable",
        ) from exc


# ---------------------------------------------------------------------------
# overview
# ---------------------------------------------------------------------------


def trace_sections() -> list[dict[str, object]]:
    """Sections describing the ``trace`` noun (used by ``trace overview``)."""
    return [
        {"title": "Purpose", "items": [_PURPOSE]},
        {"title": "Audience", "items": [AUDIENCE]},
        {"title": "Why", "items": [TAGLINE]},
        {"title": "Verbs", "items": list(VERBS)},
        {
            "title": "The run directory",
            "items": [
                f"default location: <state>/{TRACE_SUBDIR}/<UTC stamp>/ (--out overrides it)",
                "events.jsonl + meta.json + plan.toml + steps/ + shots/",
                "index.html (standalone) and trace.html (the Artifact fragment)",
            ],
        },
        {
            "title": "Honesty rules",
            "items": [
                "a step runs verbatim: nothing appends --apply, stdin is /dev/null",
                "a failed step keeps its own cmd-end beside its retry; the run still exits 0",
                "headless is recorded as a note with its reason, never silently skipped",
            ],
        },
    ]


def cmd_trace_overview(args: argparse.Namespace) -> int:
    emit_overview(_SUBJECT, trace_sections(), json_mode=_json_mode(args))
    return EXIT_SUCCESS


def _no_verb(args: argparse.Namespace) -> int:
    # `microduck-cli trace` with no sub-verb prints the noun's overview.
    return cmd_trace_overview(args)


# ---------------------------------------------------------------------------
# plan
# ---------------------------------------------------------------------------


def cmd_trace_plan(args: argparse.Namespace) -> int:
    json_mode = _json_mode(args)
    mdx = _read_text(args.from_tutorial, "tutorial")
    sidecar = _read_sidecar(args.sidecar)
    plan = from_tutorial(mdx, sidecar)

    for key in plan.meta.get("unmatched", []):
        emit_diagnostic(f"sidecar key matched no command: {key}")

    text = dump_plan(plan)
    out_path: str | None = None
    if args.out:
        try:
            Path(args.out).write_text(text, encoding="utf-8")
        except OSError as exc:
            raise CliError(
                EXIT_ENV_ERROR,
                f"cannot write the plan: {args.out}: {exc.strerror or exc}",
                "pass a writable --out path",
            ) from exc
        out_path = args.out

    if json_mode:
        payload = _plan_payload(plan)
        payload["out"] = out_path
        emit_result(payload, json_mode=True)
        return EXIT_SUCCESS

    if out_path:
        emit_result(f"plan: {out_path} ({len(plan.steps)} steps)", json_mode=False)
    else:
        emit_result(text, json_mode=False)
    return EXIT_SUCCESS


# ---------------------------------------------------------------------------
# run
# ---------------------------------------------------------------------------


def _display_for_run(args: argparse.Namespace, run_dir: RunDir) -> Any:
    """The display to capture through, or ``None`` (headless, with a note)."""
    if args.headless:
        append_note(run_dir, "capture disabled: --headless")
        return None
    display = _discover_display()
    if display is None:
        append_note(run_dir, "no display found: running headless (frames skipped)")
        emit_diagnostic("no display found — running headless; frames will be skipped")
    return display


def _autolock_allowed() -> bool:
    """Whether ``--pause-autolock`` may touch the desktop settings.

    The flag is the opt-in. On a pipe it is already explicit consent (that is why
    it is a flag and not a default), so it proceeds; on a TTY the operator is
    asked before their desktop settings change.
    """
    if consent(False, _isatty()) is not Consent.PROMPT:
        return True
    return confirm_on_tty("pause the desktop auto-lock for this run? [y/N]")


def cmd_trace_run(args: argparse.Namespace) -> int:
    json_mode = _json_mode(args)
    plan = load_plan(_read_text(args.plan, "plan"))
    run_dir = _make_run_dir(args.out, args)
    update_meta(run_dir, title=args.title or plan.title)

    display = _display_for_run(args, run_dir)
    # Consent stays here: it is the only part of the run that talks to a person.
    allowed = bool(args.pause_autolock and _autolock_allowed())
    traced = _run_traced(
        plan,
        run_dir,
        pause_autolock=allowed,
        display_env=display,
        state_dir=_state_dir(args),
        shot=capture_frame,
        pause=_pause_autolock,
    )
    result = traced.plan_result

    trace_path, index_path = _render(run_dir)
    payload = {
        "run_dir": str(run_dir.path),
        "index": index_path,
        "trace": trace_path,
        "steps_run": result.steps_run,
        "failures": result.failures,
        "retried": result.retried,
        "autolock_paused": traced.autolock_paused,
        "headless": traced.headless,
    }
    if json_mode:
        emit_result(payload, json_mode=True)
    else:
        emit_result(
            "\n".join(
                [
                    f"run dir : {payload['run_dir']}",
                    f"index   : {payload['index']}",
                    f"steps   : {payload['steps_run']} run, "
                    f"{payload['failures']} failed, {payload['retried']} retried",
                ]
            ),
            json_mode=False,
        )
    return EXIT_SUCCESS


# ---------------------------------------------------------------------------
# exec
# ---------------------------------------------------------------------------


def _exec_run_dir(args: argparse.Namespace) -> RunDir:
    if args.run:
        try:
            return ensure_run_dir(args.run, _clock())
        except OSError as exc:
            raise CliError(
                EXIT_ENV_ERROR,
                f"cannot use the run directory: {args.run}: {exc.strerror or exc}",
                "pass a writable --run path",
            ) from exc
    existing = newest_run(_state_dir(args))
    return existing if existing is not None else _make_run_dir(None, args)


def cmd_trace_exec(args: argparse.Namespace) -> int:
    argv = list(args.command)
    if argv and argv[0] == "--":
        argv = argv[1:]
    if not argv:
        raise CliError(
            EXIT_USER_ERROR,
            "no command to trace",
            f"put the command after '--', e.g. '{PROG} trace exec -- microduck duck health'",
        )

    run_dir = _exec_run_dir(args)
    result = _exec_one(argv, run_dir)
    payload = {
        "run_dir": str(run_dir.path),
        "rc": result.rc,
        "elapsed": result.elapsed,
        "out": result.out_path,
        "err": result.err_path,
    }
    if _json_mode(args):
        emit_result(payload, json_mode=True)
    else:
        emit_result(f"rc      : {result.rc}\nrun dir : {payload['run_dir']}", json_mode=False)
    return EXIT_SUCCESS


# ---------------------------------------------------------------------------
# import
# ---------------------------------------------------------------------------


def cmd_trace_import(args: argparse.Namespace) -> int:
    # An --out the operator already had is theirs; a destination this verb
    # created is ours to take back if the import never gets to write into it.
    ours = not args.out or not Path(args.out).exists()
    dst = _make_run_dir(args.out, args)
    try:
        count = import_run(args.src, dst, redact_home=not args.no_redact)
    except CliError:
        if ours:
            remove_empty_run_dir(dst)
        raise
    trace_path, index_path = _render(dst)
    payload = {
        "src": args.src,
        "run_dir": str(dst.path),
        "events": count,
        "shots": count_shots(dst),
        "index": index_path,
        "trace": trace_path,
        "redacted": not args.no_redact,
    }
    if _json_mode(args):
        emit_result(payload, json_mode=True)
    else:
        emit_result(
            "\n".join(
                [
                    f"run dir : {payload['run_dir']}",
                    f"events  : {count}",
                    f"shots   : {payload['shots']}",
                    f"index   : {index_path}",
                ]
            ),
            json_mode=False,
        )
    return EXIT_SUCCESS


# ---------------------------------------------------------------------------
# render
# ---------------------------------------------------------------------------


def cmd_trace_render(args: argparse.Namespace) -> int:
    run_dir = _existing_run_dir(args.run_dir)
    trace_path, index_path = _render(run_dir)
    files = _sizes((index_path, trace_path))
    if _json_mode(args):
        emit_result({"run_dir": str(run_dir.path), "files": files}, json_mode=True)
    else:
        emit_result(
            "\n".join(f"{f['path']} ({f['bytes']} bytes)" for f in files),
            json_mode=False,
        )
    return EXIT_SUCCESS


# ---------------------------------------------------------------------------
# serve
# ---------------------------------------------------------------------------


def cmd_trace_serve(args: argparse.Namespace) -> int:
    run_dir = _existing_run_dir(args.run_dir)
    try:
        handle = _serve(run_dir, port=args.port, host=args.host, allow_remote=args.allow_remote)
    except CliError as exc:
        # The package refuses a non-loopback bind in library terms
        # ("allow_remote=True"); at the CLI the operator's move is the flag.
        raise CliError(
            exc.code,
            exc.message,
            f"bind 127.0.0.1 (the default), or pass --allow-remote to serve on "
            f"{args.host} — the server has no auth or TLS",
        ) from exc
    except OSError as exc:
        raise CliError(
            EXIT_ENV_ERROR,
            f"cannot bind {args.host}:{args.port}: {exc.strerror or exc}",
            "pick another --port (0 lets the OS choose) or another --host",
        ) from exc

    # The URL goes out FIRST: an agent reading the stream must have it before
    # the verb blocks on the server.
    emit_result({"url": handle.url} if _json_mode(args) else handle.url, json_mode=_json_mode(args))
    sys.stdout.flush()

    if args.check:
        handle.stop()
        return EXIT_SUCCESS

    emit_diagnostic("serving; press Ctrl-C to stop")
    try:
        handle.thread.join()
    except KeyboardInterrupt:
        emit_diagnostic("stopping")
    finally:
        handle.stop()
    return EXIT_SUCCESS


# ---------------------------------------------------------------------------
# list
# ---------------------------------------------------------------------------


def cmd_trace_list(args: argparse.Namespace) -> int:
    state = _state_dir(args)
    root = Path(state) / TRACE_SUBDIR
    runs: list[dict[str, Any]] = [
        {
            "name": summary.stamp,
            "path": summary.path,
            "events": summary.events,
            "rendered": summary.rendered,
            "title": summary.title,
        }
        for summary in list_runs(state)
    ]
    if _json_mode(args):
        emit_result({"root": str(root), "runs": runs}, json_mode=True)
        return EXIT_SUCCESS
    if not runs:
        emit_result(f"no runs under {root}", json_mode=False)
        return EXIT_SUCCESS
    emit_result(
        "\n".join(
            f"{r['name']}  {r['events']:>6} events  "
            f"{'rendered' if r['rendered'] else 'not rendered'}  {r['path']}"
            for r in runs
        ),
        json_mode=False,
    )
    return EXIT_SUCCESS


# ---------------------------------------------------------------------------
# registration
# ---------------------------------------------------------------------------


def register(sub: argparse._SubParsersAction) -> None:
    p = sub.add_parser(
        "trace",
        help="Record a session and render its run-trace page " f"(see '{PROG} trace overview').",
    )
    _add_json_flag(p)
    p.set_defaults(func=_no_verb, json=False)
    # `p` is a _CliArgumentParser (the top-level subparsers were built with that
    # parser_class); propagate it so `trace <verb>` parse errors route through the
    # structured error contract instead of argparse's default stderr/exit 2.
    noun_sub = p.add_subparsers(dest="trace_command", parser_class=type(p))

    ov = noun_sub.add_parser("overview", help="Describe the trace noun.")
    ov.add_argument(
        "target",
        nargs="?",
        help="Ignored — overview always describes this noun. Accepted so a stray "
        "path argument never hard-fails.",
    )
    _add_json_flag(ov)
    ov.set_defaults(func=cmd_trace_overview)

    plan_p = noun_sub.add_parser("plan", help="Build a run plan from a tutorial's commands.")
    plan_p.add_argument(
        "--from-tutorial",
        required=True,
        metavar="MDX",
        help="The tutorial markdown/MDX whose fenced bash commands become steps.",
    )
    plan_p.add_argument(
        "--sidecar",
        default=None,
        metavar="TOML",
        help="A TOML table keyed by the exact command string, overriding that "
        "step's sleeps, frame, retry or note. An unmatched key is reported.",
    )
    plan_p.add_argument(
        "--out",
        default=None,
        metavar="PLAN",
        help="Write the plan TOML here instead of printing it.",
    )
    _add_json_flag(plan_p)
    plan_p.set_defaults(func=cmd_trace_plan)

    run_p = noun_sub.add_parser("run", help="Execute a plan and render the run-trace page.")
    run_p.add_argument("--plan", required=True, metavar="PLAN", help="The plan TOML to execute.")
    run_p.add_argument(
        "--out", default=None, metavar="DIR", help="Run directory (default: <state>/trace/<stamp>)."
    )
    run_p.add_argument("--state", default=None, help=STATE_DIR_HELP)
    run_p.add_argument(
        "--headless",
        action="store_true",
        help="Do not look for a display; skip frame capture and record why.",
    )
    run_p.add_argument(
        "--pause-autolock",
        action="store_true",
        help="Opt-in: pause the desktop idle-lock for the run and restore it "
        "afterwards (always, even if the run raises). On a TTY you are asked to "
        "confirm first; on a pipe this flag is itself the explicit consent.",
    )
    run_p.add_argument("--title", default=None, help="Title for the rendered page.")
    _add_json_flag(run_p)
    run_p.set_defaults(func=cmd_trace_run)

    exec_p = noun_sub.add_parser("exec", help="Trace one arbitrary command into a run dir.")
    exec_p.add_argument(
        "--run", default=None, metavar="DIR", help="Run directory (default: the newest, else new)."
    )
    exec_p.add_argument("--state", default=None, help=STATE_DIR_HELP)
    _add_json_flag(exec_p)
    exec_p.add_argument(
        "command",
        nargs=argparse.REMAINDER,
        metavar="-- COMMAND ...",
        help="The command to trace, after '--'; passed through unchanged.",
    )
    exec_p.set_defaults(func=cmd_trace_exec)

    import_p = noun_sub.add_parser("import", help="Adopt an external run dir and render it.")
    import_p.add_argument("src", metavar="SRC", help="Directory holding events.jsonl (+ shots/).")
    import_p.add_argument(
        "--out", default=None, metavar="DIR", help="Run directory (default: <state>/trace/<stamp>)."
    )
    import_p.add_argument("--state", default=None, help=STATE_DIR_HELP)
    import_p.add_argument(
        "--no-redact",
        action="store_true",
        help="Keep /home/<user> paths verbatim instead of rewriting them to ~.",
    )
    _add_json_flag(import_p)
    import_p.set_defaults(func=cmd_trace_import)

    render_p = noun_sub.add_parser("render", help="Regenerate index.html and trace.html.")
    render_p.add_argument("run_dir", metavar="RUN_DIR", help="The run directory to render.")
    _add_json_flag(render_p)
    render_p.set_defaults(func=cmd_trace_render)

    serve_p = noun_sub.add_parser("serve", help="Serve a rendered run dir over loopback HTTP.")
    serve_p.add_argument("run_dir", metavar="RUN_DIR", help="The run directory to serve.")
    serve_p.add_argument(
        "--port", type=int, default=0, help="Port to bind (0 = let the OS choose)."
    )
    serve_p.add_argument("--host", default="127.0.0.1", help="Host to bind (default: loopback).")
    serve_p.add_argument(
        "--allow-remote",
        action="store_true",
        help="Bind a non-loopback host; the server has no auth or TLS — only on "
        "a trusted network.",
    )
    serve_p.add_argument(
        "--check",
        action="store_true",
        help="Bind, print the URL and stop immediately (non-blocking check).",
    )
    _add_json_flag(serve_p)
    serve_p.set_defaults(func=cmd_trace_serve)

    list_p = noun_sub.add_parser("list", help="List the recorded run directories.")
    list_p.add_argument("--state", default=None, help=STATE_DIR_HELP)
    _add_json_flag(list_p)
    list_p.set_defaults(func=cmd_trace_list)
