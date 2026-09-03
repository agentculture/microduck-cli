"""``microduck-cli rules`` — the rules layer, the engine that runs it, and intents.

Thin argparse wiring only. Everything with a loop, a socket or a composition
decision lives in :mod:`microduck_cli.behavior` — :mod:`microduck_cli.behavior.compose`
is the composition root this module drives (the reachy-mini-cli split).

Mirrors :mod:`microduck_cli.cli._commands.cli` — the nested subparsers are built
with ``parser_class=type(p)`` so a parse error under this noun keeps the
structured ``error:``/``hint:`` contract instead of argparse's default exit 2,
and a bare ``microduck-cli rules`` prints this noun's overview. ``rules engine``
is a noun of its own for the same reason and carries its own ``overview``.

The verbs
---------

* ``rules list`` — the merged config (shipped defaults + the box-local overlay
  at ``<state>/rules.toml``, or ``--rules PATH``) rendered by id, kind,
  predicate, action, cooldown and ORIGIN (``shipped``/``overlay``/``tombstoned``).
* ``rules check`` — validate that config, then its actions against a skills
  snapshot (``--skills``, else a live duck, else skipped with a diagnostic), then
  optionally replay a recorded JSONL stream through the rule engine. It is a
  DESCRIPTIVE verb: a content problem is reported by rule id and the command
  still **exits 0** (the agent-first rubric — a descriptive verb never hard-fails
  on content). Only a broken invocation (an unreadable ``--replay`` file) errors.
* ``rules engine run|start|stop|status`` — the foreground run and its background
  siblings. ``run`` is gated: :func:`microduck_cli.duck.gate.consent` decides
  prompt / dry-run / ``--apply``, and the dry-run prints all six start steps and
  sends nothing at all — no socket is even opened.
* ``rules intent <kind>`` — one intent through the ONE registry. With an engine
  live it goes on the spool and the engine's acknowledgement is printed; with no
  engine it is validated and the would-be admission (or the refusal text,
  verbatim) is printed, sending nothing.

Test seams
----------
Every side effect is a module attribute a test monkeypatches, mirroring
``_commands/env.py``:

* ``_client_factory(socket_path) -> RobotClient`` — a connected client;
* ``_clock`` / ``_sleep`` — the engine's injected clock and sleep, so a whole run
  is deterministic and ``--max-ticks`` terminates without waiting;
* ``_spawn(argv) -> Popen`` — ``rules engine start``'s detached child;
* ``_proc_cmdline(pid)`` / ``_kill(pid, sig)`` — ``rules engine stop``'s
  pid-identity check, the same discipline ``env/stack.py`` uses (a pid is not an
  identity: signal only after ``/proc/<pid>/cmdline`` still names the run).

Verb summaries live in :mod:`microduck_cli.explain.rules` (``VERBS``), which the
global ``overview``/``learn`` surfaces read too — so adding a verb here means
editing this file, ``explain/rules.py`` and ``tests/test_rules_cli.py``, and
nothing else.
"""

from __future__ import annotations

import argparse
import json
import os
import signal
import subprocess  # nosec B404 - fixed argv only, never shell=True
import sys
import time
from pathlib import Path
from typing import Any, Callable

from microduck_cli.behavior import compose, liveness, release, senselog, skills
from microduck_cli.behavior.defaults import load_shipped_rules
from microduck_cli.behavior.human_gate import HumanGate
from microduck_cli.behavior.idle import register as register_idle_kind
from microduck_cli.behavior.intents import ORIGIN_CLI, default_registry
from microduck_cli.behavior.replay import replay as replay_records
from microduck_cli.behavior.rules import RulesConfig, load_rules, merge_rules
from microduck_cli.cli._commands.overview import emit_overview
from microduck_cli.cli._errors import EXIT_ENV_ERROR, EXIT_SUCCESS, EXIT_USER_ERROR, CliError
from microduck_cli.cli._output import emit_diagnostic, emit_result
from microduck_cli.duck import addressing
from microduck_cli.duck.gate import Consent, confirm_on_tty, consent, render_dry_run
from microduck_cli.explain.rules import VERBS
from microduck_cli.ipc.client import RobotClient, RpcError

_SUBJECT = "microduck-cli rules"
_PURPOSE = (
    "The data-only rules layer (events -> rules -> actions) and the engine that evaluates it."
)
_ENGINE_SUBJECT = "microduck-cli rules engine"
_ENGINE_PURPOSE = "Run the 50 Hz tick engine that evaluates the rules against a live duck."

#: The box-local overlay's name inside the duck's state directory.
OVERLAY_NAME = "rules.toml"

#: The API version from which ``robot.policies`` exists (deviation d1 — the
#: pinned daemon answers API 16 and reports its skills through
#: ``robot.subscribe`` instead). Matches ``tests/fake_robotd.py``.
POLICY_API_VERSION = 18

#: The substring ``/proc/<pid>/cmdline`` must still contain before ``rules engine
#: stop`` signals that pid. A pid is not an identity.
ENGINE_MARKER = "rules engine run"

#: How long ``rules intent`` waits for a live engine's acknowledgement.
INTENT_WAIT_S = 2.0
_INTENT_POLL_S = 0.02

# ---------------------------------------------------------------------------
# Test seams
# ---------------------------------------------------------------------------


def _default_client_factory(socket_path: str) -> RobotClient:
    """A connected client. ``verify_joints=False`` — see compose.py's docstring."""
    client = RobotClient(socket_path, clock=_clock)
    return client.connect(verify_joints=False)


def _default_spawn(argv: list[str]) -> Any:
    return subprocess.Popen(  # nosec B603 - fixed argv built here, never shell=True
        argv,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
        close_fds=True,
    )


def _default_proc_cmdline(pid: int) -> str | None:
    """``/proc/<pid>/cmdline`` as a space-joined string, or ``None``."""
    try:
        raw = Path(f"/proc/{pid}/cmdline").read_bytes()
    except OSError:
        return None
    return raw.replace(b"\0", b" ").decode("utf-8", "replace").strip()


_clock: Callable[[], float] = time.monotonic
_sleep: Callable[[float], None] = time.sleep
_client_factory: Callable[[str], Any] = _default_client_factory
_spawn: Callable[[list[str]], Any] = _default_spawn
_proc_cmdline: Callable[[int], "str | None"] = _default_proc_cmdline
_kill: Callable[[int, int], None] = os.kill


# ---------------------------------------------------------------------------
# Addressing / rules loading helpers
# ---------------------------------------------------------------------------


def _env_with_state(args: argparse.Namespace) -> dict[str, str]:
    env = dict(os.environ)
    state = getattr(args, "state", None)
    if state:
        env["DUCK_SIM_STATE"] = state
    return env


def _resolve_duck(args: argparse.Namespace) -> addressing.DuckAddress:
    return addressing.resolve(
        name=getattr(args, "duck", None),
        socket=getattr(args, "socket", None),
        env=_env_with_state(args),
        listdir=os.listdir,
    )


def _state_dir(args: argparse.Namespace, address: Any = None) -> str:
    explicit = getattr(args, "state", None)
    if explicit:
        return explicit
    if address is not None and getattr(address, "state_dir", ""):
        return str(address.state_dir)
    raw = os.environ.get("DUCK_SIM_STATE") or addressing.DEFAULT_STATE_DIR
    return os.path.expanduser(raw) if raw.startswith("~") else raw


def _overlay_path(args: argparse.Namespace, state_dir: str) -> str | None:
    """The overlay TOML to layer over the shipped defaults, or ``None``.

    ``--rules PATH`` is used even when it does not exist (a typo must be an
    error, not a silent fall-back to the defaults); the implicit
    ``<state>/rules.toml`` is used only when it is actually there.
    """
    explicit = getattr(args, "rules", None)
    if explicit:
        return explicit
    candidate = os.path.join(state_dir, OVERLAY_NAME)
    return candidate if os.path.isfile(candidate) else None


def _merged_rules(overlay_path: str | None) -> tuple[RulesConfig, RulesConfig, RulesConfig | None]:
    """``(merged, shipped, overlay)``. Raises ``CliError`` on a content problem."""
    shipped = load_shipped_rules()
    if overlay_path is None:
        return shipped, shipped, None
    overlay = load_rules(Path(overlay_path))
    return merge_rules(shipped, overlay), shipped, overlay


def _predicate_text(rule: Any) -> str:
    pred = rule.when
    if pred.value is None:
        return f"{pred.field} {pred.op}"
    return f"{pred.field} {pred.op} {pred.value!r}"


def _rule_rows(merged: RulesConfig, overlay: RulesConfig | None) -> list[dict[str, object]]:
    """One row per rule in the merged config, plus one per tombstoned id."""
    overlay_ids = (
        {r.id for r in (*overlay.react, *overlay.inhibit)} if overlay is not None else set()
    )
    tombstoned = merged.disabled | (overlay.disabled if overlay is not None else frozenset())
    rows: list[dict[str, object]] = []
    for rule in (*merged.react, *merged.inhibit):
        rows.append(
            {
                "id": rule.id,
                "kind": rule.kind,
                "predicate": _predicate_text(rule),
                "action": rule.action or ", ".join(sorted(rule.disable)),
                "cooldown_s": rule.cooldown_s,
                "origin": "overlay" if rule.id in overlay_ids else "shipped",
            }
        )
    known = {row["id"] for row in rows}
    for rule_id in sorted(tombstoned - known):
        rows.append(
            {
                "id": rule_id,
                "kind": "-",
                "predicate": "-",
                "action": "-",
                "cooldown_s": None,
                "origin": "tombstoned",
            }
        )
    return rows


# ---------------------------------------------------------------------------
# overview (the rules noun and the engine sub-noun)
# ---------------------------------------------------------------------------


def rules_sections() -> list[dict[str, object]]:
    """Sections describing the ``rules`` noun (used by ``rules overview``)."""
    return [
        {"title": "Purpose", "items": [_PURPOSE]},
        {"title": "Verbs", "items": list(VERBS)},
        {
            "title": "Layering",
            "items": [
                "shipped defaults (behavior/default_rules.toml, inside the wheel)",
                f"box-local overlay at <state>/{OVERLAY_NAME}, or --rules PATH",
                "merged per rule id: an overlay entry replaces wholesale, enabled=false tombstones",
            ],
        },
    ]


def engine_sections() -> list[dict[str, object]]:
    """Sections describing the ``rules engine`` sub-noun."""
    return [
        {"title": "Purpose", "items": [_ENGINE_PURPOSE]},
        {"title": "Verbs", "items": [v for v in VERBS if v.startswith("rules engine")]},
        {"title": "Start sequence", "items": list(compose.START_STEPS)},
        {
            "title": "One engine at a time",
            "items": [
                "liveness is a heartbeat in <state>/state.json, never a flag file",
                "a second run is refused (exit 1, 'engine live') before any socket is opened",
            ],
        },
    ]


def cmd_rules_overview(args: argparse.Namespace) -> int:
    emit_overview(_SUBJECT, rules_sections(), json_mode=bool(getattr(args, "json", False)))
    return EXIT_SUCCESS


def cmd_rules_engine_overview(args: argparse.Namespace) -> int:
    emit_overview(_ENGINE_SUBJECT, engine_sections(), json_mode=bool(getattr(args, "json", False)))
    return EXIT_SUCCESS


def _no_verb(args: argparse.Namespace) -> int:
    # `microduck-cli rules` with no sub-verb prints the noun's overview.
    return cmd_rules_overview(args)


def _no_engine_verb(args: argparse.Namespace) -> int:
    # `microduck-cli rules engine` with no sub-verb prints the sub-noun's overview.
    return cmd_rules_engine_overview(args)


# ---------------------------------------------------------------------------
# list
# ---------------------------------------------------------------------------


def cmd_rules_list(args: argparse.Namespace) -> int:
    json_mode = bool(getattr(args, "json", False))
    state_dir = _state_dir(args)
    overlay_path = _overlay_path(args, state_dir)
    merged, _shipped, overlay = _merged_rules(overlay_path)
    rows = _rule_rows(merged, overlay)

    payload = {
        "overlay": overlay_path,
        "schema_version": merged.schema_version,
        "active_mode": merged.active_mode,
        "modes": sorted(merged.modes),
        "rules": rows,
    }
    if json_mode:
        emit_result(payload, json_mode=True)
        return EXIT_SUCCESS

    lines = [
        "# rules list",
        f"overlay: {overlay_path or '(none — shipped defaults only)'}",
        f"schema_version: {merged.schema_version}",
        "",
    ]
    for row in rows:
        cooldown = "-" if row["cooldown_s"] is None else f"{row['cooldown_s']:g}s"
        lines.append(
            f"- {row['id']} [{row['kind']}] when {row['predicate']} -> {row['action']} "
            f"(cooldown {cooldown}, origin {row['origin']})"
        )
    if not rows:
        lines.append("- (no rules)")
    emit_result("\n".join(lines), json_mode=False)
    return EXIT_SUCCESS


# ---------------------------------------------------------------------------
# check
# ---------------------------------------------------------------------------


def _live_snapshot(args: argparse.Namespace) -> tuple[Any, str]:
    """A skills snapshot from a live duck, per its API version (deviation d1)."""
    address = _resolve_duck(args)
    client = _client_factory(address.socket_path)
    try:
        api = getattr(getattr(client, "daemon", None), "api_version", None) or 0
        if api >= POLICY_API_VERSION:
            result = client.request("robot.policies")
            return (
                skills.skills_from_policies_result(result, api_version=api),
                f"robot.policies (api {api})",
            )
        sub = client.subscribe()
        return (
            skills.skills_from_subscribe_result(sub.raw, api_version=api),
            f"robot.subscribe (api {api})",
        )
    finally:
        client.close()


def _resolve_snapshot(args: argparse.Namespace) -> tuple[Any, str, str]:
    """``(snapshot | None, source, note)`` — never raises for a missing duck."""
    snapshot_path = getattr(args, "skills", None)
    if snapshot_path:
        return skills.load_snapshot(snapshot_path), f"snapshot {snapshot_path}", ""
    if not (getattr(args, "duck", None) or getattr(args, "socket", None)):
        return (
            None,
            "skipped",
            "no --skills snapshot and no --duck/--socket: action names were not checked",
        )
    try:
        snapshot, source = _live_snapshot(args)
    except (CliError, RpcError, OSError) as exc:
        message = getattr(exc, "message", None) or str(exc)
        return None, "skipped", f"no live duck to ask ({message}): action names were not checked"
    return snapshot, source, ""


def _read_records(path: str) -> list[dict]:
    """Parse a replay JSONL file. A broken invocation IS an error (exit 1)."""
    try:
        text = Path(path).read_text(encoding="utf-8")
    except OSError as exc:
        raise CliError(
            EXIT_USER_ERROR,
            f"replay file {path} could not be read: {exc}",
            remediation="check the path — a recorded sense stream is one JSON object per line",
        ) from exc
    records: list[dict] = []
    for number, line in enumerate(text.splitlines(), start=1):
        if not line.strip():
            continue
        try:
            entry = json.loads(line)
        except ValueError as exc:
            raise CliError(
                EXIT_USER_ERROR,
                f"replay file {path} line {number} is not valid JSON: {exc}",
                remediation="each line must be one JSON record: {'ts', 'source', 'params'}",
            ) from exc
        records.append(entry)
    return records


def _replay_payload(config: RulesConfig, path: str) -> dict[str, object]:
    result = replay_records(config, _read_records(path))
    ticks = [
        {
            "ts": tick.ts,
            "source": tick.source,
            "fires": [f.rule_id for f in tick.result.fires],
            "drops": [
                {"reason": d.reason, "rule_id": d.rule_id, "detail": d.detail}
                for d in tick.result.drops
            ],
            "inhibited": dict(tick.result.inhibited),
        }
        for tick in result.ticks
    ]
    return {"file": path, "summary": dict(result.summary), "ticks": ticks}


def cmd_rules_check(args: argparse.Namespace) -> int:
    """Validate the merged rules. DESCRIPTIVE: content problems still exit 0."""
    json_mode = bool(getattr(args, "json", False))
    state_dir = _state_dir(args)
    overlay_path = _overlay_path(args, state_dir)

    issues: list[str] = []
    problems: list[str] = []
    config: RulesConfig | None = None
    try:
        config, _shipped, _overlay = _merged_rules(overlay_path)
    except CliError as exc:
        # A malformed rules file is CONTENT, not a broken invocation: the message
        # already names the offending rule id, and a descriptive verb reports it
        # rather than hard-failing (the agent-first rubric).
        issues.append(exc.message)

    snapshot = None
    source = "skipped"
    note = "the rules file did not parse, so no action name could be checked"
    if config is not None:
        snapshot, source, note = _resolve_snapshot(args)
        if snapshot is not None:
            problems = skills.validate_rule_actions(config, snapshot)

    replay_report: dict[str, object] | None = None
    replay_path = getattr(args, "replay", None)
    if replay_path and config is not None:
        replay_report = _replay_payload(config, replay_path)

    payload: dict[str, object] = {
        "overlay": overlay_path,
        "ok": not issues and not problems,
        "issues": issues,
        "skills_source": source,
        "skills": list(snapshot.skills) if snapshot is not None else None,
        "problems": problems,
        "replay": replay_report,
    }
    if note:
        payload["note"] = note
        emit_diagnostic(f"rules check: {note}")

    if json_mode:
        emit_result(payload, json_mode=True)
    else:
        emit_result(_render_check(payload), json_mode=False)
    return EXIT_SUCCESS


def _render_check(payload: dict[str, object]) -> str:
    lines = ["# rules check", f"overlay: {payload['overlay'] or '(none)'}", "", "## content"]
    lines.extend([f"- {issue}" for issue in payload["issues"]] or ["- ok"])
    lines += ["", f"## actions ({payload['skills_source']})"]
    lines.extend([f"- {problem}" for problem in payload["problems"]] or ["- ok"])
    replay_report = payload.get("replay")
    if isinstance(replay_report, dict):
        summary = replay_report["summary"]
        lines += ["", f"## replay ({replay_report['file']})"]
        lines.append(
            f"- ticks {summary['ticks']}, fires {summary['fires']}, drops {summary['drops']}"
        )
        for reason, count in sorted(dict(summary["drops_by_reason"]).items()):
            lines.append(f"- drop {reason}: {count}")
        inhibited = list(summary["inhibited_actions"])
        lines.append(f"- inhibited actions: {', '.join(inhibited) if inhibited else '(none)'}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# engine run
# ---------------------------------------------------------------------------


def _apply_command(args: argparse.Namespace) -> str:
    parts = ["microduck", "rules", "engine", "run"]
    for flag in ("duck", "socket", "state", "rules"):
        value = getattr(args, flag, None)
        if value:
            parts += [f"--{flag}", str(value)]
    parts += ["--hz", f"{args.hz:g}"]
    if getattr(args, "max_ticks", None):
        parts += ["--max-ticks", str(args.max_ticks)]
    if getattr(args, "no_idle", False):
        parts.append("--no-idle")
    parts.append("--apply")
    return " ".join(parts)


def _gate_or_plan(args: argparse.Namespace, address: Any, overlay_path: str | None) -> bool:
    """The motion gate for ``engine run``. ``False`` means "the plan was printed"."""
    state = consent(bool(getattr(args, "apply", False)))
    if state is Consent.APPLY:
        return True
    plan = compose.start_plan(
        address,
        hz=args.hz,
        apply_command=_apply_command(args),
        rules_path=overlay_path,
    )
    if state is Consent.DRY_RUN:
        body = render_dry_run(plan)
        if bool(getattr(args, "json", False)):
            emit_result({"mode": "dry_run", "plan": plan, "text": body}, json_mode=True)
        else:
            emit_result(body, json_mode=False)
        return False
    question = f"{render_dry_run(plan)}\n\nStart the engine and drive this duck? [y/N]"
    if not confirm_on_tty(question):
        raise CliError(
            EXIT_USER_ERROR,
            "rules engine run cancelled",
            remediation="re-run and confirm, or pass --apply for non-interactive (agent) mode",
        )
    return True


def cmd_rules_engine_run(args: argparse.Namespace) -> int:
    senselog.install_logging()
    json_mode = bool(getattr(args, "json", False))
    address = _resolve_duck(args)
    state_dir = _state_dir(args, address)
    overlay_path = _overlay_path(args, state_dir)
    config, _shipped, _overlay = _merged_rules(overlay_path)

    if not _gate_or_plan(args, address, overlay_path):
        return EXIT_SUCCESS

    # BEFORE the client is constructed: a refused second engine must never
    # contend for the duck it was told not to drive.
    liveness.refuse_if_engine_live(state_dir, verb="rules engine run", now=_clock)

    runtime = compose.build_runtime(
        address,
        config,
        client_factory=_client_factory,
        clock=_clock,
        state_dir=state_dir,
        hz=args.hz,
        sleep=_sleep,
        register_idle=not bool(getattr(args, "no_idle", False)),
    )
    ticks = 0
    interrupted: BaseException | None = None
    report = None
    try:
        with release.owning(runtime.client) as owner:
            compose.arm(runtime)
            ticks = runtime.run(max_ticks=getattr(args, "max_ticks", None))
        runtime.heartbeat.clear()
    except (KeyboardInterrupt, release.SignalExit) as exc:
        interrupted, report = exc, owner.report
    finally:
        runtime.close()

    if interrupted is not None:
        raise _interrupted_error(interrupted, report)

    payload = {
        "duck": address.name,
        "socket": address.socket_path,
        "state_dir": state_dir,
        "steps": runtime.steps,
        "ticks": ticks,
        "metrics": runtime.engine.metrics.snapshot(),
        "gate": runtime.gate.snapshot(),
        "sink_drops": dict(runtime.robot_sink.drops),
        "intents": {
            "drained": runtime.spool.drained,
            "admitted": runtime.spool.admitted,
            "refused": runtime.spool.refused,
        },
    }
    if json_mode:
        emit_result(payload, json_mode=True)
    else:
        emit_result(_render_run(payload), json_mode=False)
    return EXIT_SUCCESS


def _interrupted_error(exc: BaseException, report: Any) -> CliError:
    """Turn an interrupt into a non-zero exit that NAMES what release achieved."""
    detail = (
        "the run was interrupted before anything could be released"
        if report is None
        else report.describe()
    )
    return CliError(
        EXIT_ENV_ERROR,
        f"rules engine run interrupted ({type(exc).__name__}): {detail}",
        remediation=(
            "robotd's own deadman expires the velocity command only — check the duck "
            "before starting another engine"
        ),
    )


def _render_run(payload: dict[str, object]) -> str:
    metrics = payload["metrics"]
    lines = [
        "# rules engine run",
        f"duck: {payload['duck']} ({payload['socket']})",
        "",
        "## start sequence",
    ]
    for step in payload["steps"]:
        mark = "ok" if step["ok"] else "FAILED"
        lines.append(f"- {step['step']}: {mark} {step['detail']}".rstrip())
    lines += [
        "",
        "## run",
        f"- ticks: {payload['ticks']} at {metrics['achieved_hz']:.2f} Hz achieved",
        f"- overruns: {metrics['overruns']}",
        f"- intents: {payload['intents']}",
        f"- sink drops: {payload['sink_drops'] or '(none)'}",
    ]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# engine start / stop / status
# ---------------------------------------------------------------------------


def _run_argv(args: argparse.Namespace) -> list[str]:
    """The child's argv: this interpreter running ``rules engine run ... --apply``.

    ``sys.executable -m microduck_cli`` rather than the bare ``microduck``
    console script, so a checkout that is not on ``PATH`` still starts and the
    cmdline ``stop`` matches on always contains :data:`ENGINE_MARKER`.
    """
    argv = [sys.executable, "-m", "microduck_cli", "rules", "engine", "run"]
    for flag in ("duck", "socket", "state", "rules"):
        value = getattr(args, flag, None)
        if value:
            argv += [f"--{flag}", str(value)]
    argv += ["--hz", f"{args.hz:g}"]
    if getattr(args, "max_ticks", None):
        argv += ["--max-ticks", str(args.max_ticks)]
    if getattr(args, "no_idle", False):
        argv.append("--no-idle")
    argv.append("--apply")
    return argv


def cmd_rules_engine_start(args: argparse.Namespace) -> int:
    json_mode = bool(getattr(args, "json", False))
    address = _resolve_duck(args)
    state_dir = _state_dir(args, address)
    liveness.refuse_if_engine_live(state_dir, verb="rules engine start", now=_clock)

    argv = _run_argv(args)
    child = _spawn(argv)
    pid = getattr(child, "pid", None)
    payload = {
        "duck": address.name,
        "state_dir": state_dir,
        "pid": pid,
        "argv": argv,
        "liveness": os.path.join(state_dir, liveness.STATE_FILENAME),
    }
    if json_mode:
        emit_result(payload, json_mode=True)
    else:
        emit_result(
            "\n".join(
                [
                    f"rules engine start: pid {pid}",
                    f"duck: {address.name}",
                    f"liveness: {payload['liveness']} (the heartbeat IS the liveness record)",
                    "the child is detached with its output on /dev/null — run in the "
                    "foreground to watch the sense log",
                ]
            ),
            json_mode=False,
        )
    return EXIT_SUCCESS


def _stop_outcome(state_dir: str) -> dict[str, object]:
    """Decide (and perform) what ``rules engine stop`` may do to the recorded pid."""
    state = liveness.read_state(state_dir, report=False)
    if state is None or state.pid is None:
        return {
            "pid": None,
            "outcome": "nothing-to-stop",
            "detail": f"no usable heartbeat in {liveness.state_path(state_dir)}",
        }
    cmdline = _proc_cmdline(state.pid)
    if cmdline is None:
        return {"pid": state.pid, "outcome": "gone", "detail": f"pid {state.pid} no longer exists"}
    if ENGINE_MARKER not in cmdline:
        # A pid is not an identity: pids are recycled, and signalling a recycled
        # one has taken out unrelated sessions upstream (env/stack.py's scar).
        return {
            "pid": state.pid,
            "outcome": "stale",
            "detail": f"pid {state.pid} is not a {ENGINE_MARKER!r} process ({cmdline})",
        }
    _kill(state.pid, signal.SIGTERM)
    return {
        "pid": state.pid,
        "outcome": "signalled",
        "detail": f"SIGTERM sent to pid {state.pid}",
    }


def cmd_rules_engine_stop(args: argparse.Namespace) -> int:
    json_mode = bool(getattr(args, "json", False))
    state_dir = _state_dir(args)
    payload: dict[str, object] = {"state_dir": state_dir, **_stop_outcome(state_dir)}
    if json_mode:
        emit_result(payload, json_mode=True)
    else:
        emit_result(
            f"rules engine stop: {payload['outcome']} — {payload['detail']}", json_mode=False
        )
    return EXIT_SUCCESS


def _hello_probe(socket_path: str) -> dict[str, object]:
    """One handshake against the duck: is a daemon there, and which API?"""
    try:
        client = _client_factory(socket_path)
    except (CliError, RpcError, OSError) as exc:
        return {"reachable": False, "detail": getattr(exc, "message", None) or str(exc)}
    try:
        return {
            "reachable": True,
            "api_version": getattr(getattr(client, "daemon", None), "api_version", None),
        }
    finally:
        client.close()


def cmd_rules_engine_status(args: argparse.Namespace) -> int:
    json_mode = bool(getattr(args, "json", False))
    try:
        address = _resolve_duck(args)
    except CliError:
        address = None
    state_dir = _state_dir(args, address)

    state = liveness.read_state(state_dir, report=False)
    live = liveness.engine_is_live(state_dir, now=_clock, report=False)
    payload: dict[str, object] = {
        "state_dir": state_dir,
        "heartbeat": str(liveness.state_path(state_dir)),
        "live": live is not None,
        "pid": state.pid if state else None,
        "pid_alive": bool(state and state.pid and liveness.pid_is_alive(state.pid)),
        "tick": state.tick if state else None,
        "hz": state.hz if state else None,
        "achieved_hz": state.achieved_hz if state else None,
        "overruns": state.overruns if state else None,
        "daemon": _hello_probe(address.socket_path) if address is not None else None,
    }
    if json_mode:
        emit_result(payload, json_mode=True)
        return EXIT_SUCCESS
    emit_result(_render_status(payload, present=state is not None), json_mode=False)
    return EXIT_SUCCESS


def _render_status(payload: dict[str, object], *, present: bool) -> str:
    verdict = "live" if payload["live"] else ("stale/absent" if present else "no heartbeat")
    lines = [
        "# rules engine status",
        f"state dir: {payload['state_dir']}",
        f"engine: {verdict} (pid {payload['pid']}, alive={payload['pid_alive']})",
        f"tick {payload['tick']} at {payload['hz']} Hz "
        f"(achieved {payload['achieved_hz']}, overruns {payload['overruns']})",
    ]
    daemon = payload["daemon"]
    if not isinstance(daemon, dict):
        lines.append("daemon: not probed (no duck resolved)")
    elif daemon.get("reachable"):
        lines.append(f"daemon: reachable (api {daemon.get('api_version')})")
    else:
        lines.append(f"daemon: unreachable — {daemon.get('detail')}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# intent
# ---------------------------------------------------------------------------


def _payload_arg(args: argparse.Namespace) -> dict:
    raw = getattr(args, "payload", None)
    if not raw:
        return {}
    try:
        value = json.loads(raw)
    except ValueError as exc:
        raise CliError(
            EXIT_USER_ERROR,
            f"--payload is not valid JSON: {exc}",
            remediation="pass a JSON object, e.g. --payload '{\"vx\": 0.1}'",
        ) from exc
    if not isinstance(value, dict):
        raise CliError(
            EXIT_USER_ERROR,
            f"--payload must be a JSON object (got {value!r})",
            remediation="pass a JSON object of parameters",
        )
    return value


def _one_registry():
    """The same registry the engine composes: the default kinds plus the real idle."""
    registry = default_registry()
    register_idle_kind(registry, HumanGate())
    return registry


def _wait_for_ack(spool: compose.IntentSpool, intent_id: str) -> dict | None:
    waited = 0.0
    while waited < INTENT_WAIT_S:
        for record in spool.records():
            if record.get("id") == intent_id:
                return record
        _sleep(_INTENT_POLL_S)
        waited += _INTENT_POLL_S
    return None


def cmd_rules_intent(args: argparse.Namespace) -> int:
    json_mode = bool(getattr(args, "json", False))
    state_dir = _state_dir(args)
    payload = _payload_arg(args)
    registry = _one_registry()
    live = liveness.engine_is_live(state_dir, now=_clock, report=False)

    if live is None:
        # No engine: the ONE registry still judges it, and nothing is sent.
        admission = registry.inject(args.kind, payload, now=_clock(), active=(), origin=ORIGIN_CLI)
        return _emit_admission(
            {
                "kind": args.kind,
                "payload": payload,
                "engine": None,
                "admitted": bool(admission.admitted),
                "reason": admission.reason,
                "sent": False,
            },
            json_mode=json_mode,
        )

    spool = compose.IntentSpool(
        Path(state_dir) / compose.INTENT_SPOOL_NAME,
        Path(state_dir) / compose.INTENT_LOG_NAME,
        registry,
    )
    intent_id = spool.submit(args.kind, payload)
    record = _wait_for_ack(spool, intent_id)
    if record is None:
        raise CliError(
            EXIT_ENV_ERROR,
            f"the engine (pid {live.pid}) did not acknowledge intent {intent_id} "
            f"within {INTENT_WAIT_S:g}s",
            remediation=(
                "check 'microduck rules engine status' — the intent is still on the spool "
                "and a running engine drains it on its next tick"
            ),
        )
    return _emit_admission(
        {
            "kind": args.kind,
            "payload": payload,
            "engine": live.pid,
            "id": intent_id,
            "admitted": bool(record.get("admitted")),
            "reason": str(record.get("reason", "")),
            "sent": True,
        },
        json_mode=json_mode,
    )


def _emit_admission(payload: dict[str, object], *, json_mode: bool) -> int:
    """Print the admission, or raise the refusal text VERBATIM (exit 1)."""
    if not payload["admitted"]:
        raise CliError(
            EXIT_USER_ERROR,
            str(payload["reason"]),
            remediation=(
                "the ONE registry judges a rule-fired and an injected intent identically; "
                "fix the payload and resubmit"
            ),
        )
    if json_mode:
        emit_result(payload, json_mode=True)
    else:
        emit_result(f"intent {payload['kind']}: {payload['reason']}", json_mode=False)
    return EXIT_SUCCESS


# ---------------------------------------------------------------------------
# registration
# ---------------------------------------------------------------------------


def _add_duck_flags(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--duck", default=None, help="Duck name (default: DUCK_SIM_DUCK).")
    parser.add_argument("--socket", default=None, help="Explicit robot control socket path.")
    parser.add_argument("--state", default=None, help="Override the state directory.")


def _add_run_flags(parser: argparse.ArgumentParser) -> None:
    _add_duck_flags(parser)
    parser.add_argument(
        "--rules", default=None, help="Overlay rules TOML (default: <state>/rules.toml)."
    )
    parser.add_argument("--hz", type=float, default=compose.DEFAULT_HZ, help="Tick rate.")
    parser.add_argument("--max-ticks", type=int, default=None, help="Stop after N ticks.")
    parser.add_argument("--no-idle", action="store_true", help="Do not register the idle base.")
    parser.add_argument("--json", action="store_true", help="Emit structured JSON.")


def _register_engine(noun_sub: argparse._SubParsersAction, parser_class: type) -> None:
    engine = noun_sub.add_parser(
        "engine", help="Run the tick engine (see 'microduck-cli rules engine overview')."
    )
    engine.add_argument("--json", action="store_true", help="Emit structured JSON.")
    engine.set_defaults(func=_no_engine_verb, json=False)
    engine_sub = engine.add_subparsers(dest="rules_engine_command", parser_class=parser_class)

    ov = engine_sub.add_parser("overview", help="Describe the rules engine sub-noun.")
    ov.add_argument(
        "target",
        nargs="?",
        help="Ignored — overview always describes this noun. Accepted so a stray "
        "path argument never hard-fails.",
    )
    ov.add_argument("--json", action="store_true", help="Emit structured JSON.")
    ov.set_defaults(func=cmd_rules_engine_overview)

    run = engine_sub.add_parser("run", help="Run the engine in the foreground (gated).")
    _add_run_flags(run)
    run.add_argument("--apply", action="store_true", help="Send the gated calls (agent mode).")
    run.set_defaults(func=cmd_rules_engine_run)

    start = engine_sub.add_parser("start", help="Spawn a detached 'engine run --apply'.")
    _add_run_flags(start)
    start.set_defaults(func=cmd_rules_engine_start)

    stop = engine_sub.add_parser("stop", help="SIGTERM the engine named by the heartbeat.")
    _add_duck_flags(stop)
    stop.add_argument("--json", action="store_true", help="Emit structured JSON.")
    stop.set_defaults(func=cmd_rules_engine_stop)

    status = engine_sub.add_parser("status", help="Report engine liveness and daemon reachability.")
    _add_duck_flags(status)
    status.add_argument("--json", action="store_true", help="Emit structured JSON.")
    status.set_defaults(func=cmd_rules_engine_status)


def register(sub: argparse._SubParsersAction) -> None:
    p = sub.add_parser(
        "rules",
        help="The data-only rules layer and its engine (see 'microduck-cli rules overview').",
    )
    p.add_argument("--json", action="store_true", help="Emit structured JSON.")
    p.set_defaults(func=_no_verb, json=False)
    # `p` is a _CliArgumentParser (the top-level subparsers were built with that
    # parser_class); propagate it so `rules <verb>` parse errors route through the
    # structured error contract instead of argparse's default stderr/exit 2.
    noun_sub = p.add_subparsers(dest="rules_command", parser_class=type(p))

    ov = noun_sub.add_parser("overview", help="Describe the rules noun.")
    ov.add_argument(
        "target",
        nargs="?",
        help="Ignored — overview always describes this noun. Accepted so a stray "
        "path argument never hard-fails.",
    )
    ov.add_argument("--json", action="store_true", help="Emit structured JSON.")
    ov.set_defaults(func=cmd_rules_overview)

    listing = noun_sub.add_parser("list", help="Render the merged rules config.")
    listing.add_argument("--rules", default=None, help="Overlay rules TOML.")
    listing.add_argument("--state", default=None, help="Override the state directory.")
    listing.add_argument("--json", action="store_true", help="Emit structured JSON.")
    listing.set_defaults(func=cmd_rules_list)

    check = noun_sub.add_parser("check", help="Validate the rules, their actions, and a replay.")
    _add_duck_flags(check)
    check.add_argument("--rules", default=None, help="Overlay rules TOML.")
    check.add_argument("--skills", default=None, help="A skills snapshot JSON file.")
    check.add_argument("--replay", default=None, help="A recorded sense JSONL to replay.")
    check.add_argument("--json", action="store_true", help="Emit structured JSON.")
    check.set_defaults(func=cmd_rules_check)

    _register_engine(noun_sub, type(p))

    intent = noun_sub.add_parser("intent", help="Submit one intent through the one registry.")
    intent.add_argument("kind", help="The intent kind (do, look, move, sound, stop, mode, idle).")
    intent.add_argument("--payload", default=None, help="A JSON object of parameters.")
    intent.add_argument("--state", default=None, help="Override the state directory.")
    intent.add_argument("--json", action="store_true", help="Emit structured JSON.")
    intent.set_defaults(func=cmd_rules_intent)
