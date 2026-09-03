"""``microduck-cli duck`` — operate one duck, in ``robotctl``'s words.

Argparse wiring only: every verb resolves an address through
:mod:`microduck_cli.duck.addressing`, opens one
:class:`~microduck_cli.ipc.client.RobotClient`, and speaks
:mod:`microduck_cli.ipc.proto`. The recorder lives in
:mod:`microduck_cli.duck.record`; the motion gate in
:mod:`microduck_cli.duck.gate`. Nothing here reimplements either.

Where the verbs come from
-------------------------
The verb names mirror ``robotctl`` at the commit pinned in
``docs/upstream-pins.md`` (``pollen-robotics/microduck`` ``sim-remote-io`` @
``0cd676d6fbb6e90a762c84aa63abe7a02dbc9495``), read and transcribed, never
copied:

* its ``RobotCommand`` enum -> ``init``, ``enable``, ``relax``, ``do``,
  ``mode``, ``look``;
* its ``Namespace`` enum -> ``health``, ``version``, ``monitor``, ``quack``,
  ``configure``;
* ``robot.stop`` (the daemon method ``robotctl`` documents under ``relax`` but
  exposes no subcommand for) -> ``stop``.

Exactly two verbs are ours: ``move``, which drives ``robot.move``
notifications at intent rate for a bounded duration and then stops (upstream
drives that from the gamepad and the browser console, so no CLI verb mirrors
it), and ``record``, the JSONL recorder — a *recording* tool rather than a
robot command. ``tests/test_duck.py`` pins that difference as a set.

The gate
--------
``init``, ``relax``, ``enable``, ``do``, ``move``, ``mode --set`` and ``look``
go through :func:`microduck_cli.duck.gate.consent` before anything is opened:
a TTY without ``--apply`` confirms, a non-TTY without ``--apply`` prints the
plan and sends nothing (and never even dials the socket), ``--apply``
proceeds. ``relax`` additionally wants ``--yes``, as ``robotctl`` does.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from dataclasses import dataclass
from typing import Any, Callable, Iterable, Sequence

from microduck_cli.behavior import senselog
from microduck_cli.cli._commands.overview import emit_overview
from microduck_cli.cli._errors import EXIT_ENV_ERROR, EXIT_USER_ERROR, CliError
from microduck_cli.cli._output import emit_diagnostic, emit_result
from microduck_cli.duck.addressing import DuckAddress, resolve

# gate.py owns the verb -> SAFETY_* mapping; importing it keeps the sentence in the
# prompt and the JSON dry-run identical to the one render_dry_run() prints.
from microduck_cli.duck.gate import (
    HINT_APPLY,
    Consent,
    confirm_on_tty,
    consent,
    render_dry_run,
    safety_sentence,
)
from microduck_cli.duck.record import Recorder
from microduck_cli.explain.duck import CHEATSHEET, VERBS
from microduck_cli.ipc import proto
from microduck_cli.ipc.client import RobotClient, RpcError

_SUBJECT = "microduck-cli duck"
_PURPOSE = (
    "Operate the duck directly, in robotctl's words (init, enable, relax, move, look, do, stop)."
)
_STATUS = (
    "gated: init/relax/enable/do/move/mode --set/look confirm on a TTY, print a plan on a "
    "non-TTY, and send only with --apply"
)

#: ``robotctl robot do`` spells its skills in kebab case and the wire spells them
#: in snake case; ``SkillArg::as_skill`` at the pinned commit is this table.
SKILL_ARGS: dict[str, str] = {
    "ground-pick": "ground_pick",
    "kick-left": "kick_left",
    "kick-right": "kick_right",
    "sit": "sit_toggle",
    "roulade": "roulade",
}

#: ``robotctl quack`` sends ``robot.sound`` with ``SoundTag::Chirp`` — the duck's
#: own voice, seeded from its SoC serial. There is no ``quack`` tag on the wire.
QUACK_TAG = "chirp"

#: ``robot.move`` is a continuous intent with a deadman behind it: the daemon
#: stops the robot when the stream stops, so ``move`` refreshes it at intent rate
#: rather than sending one frame.
INTENT_HZ = 20.0

#: ``robotctl robot mode`` knows these two.
MODES = ("walk", "roller")

#: ``robotctl monitor``'s default frame rate.
DEFAULT_MONITOR_HZ = 10

#: Default recording length for ``duck record``.
DEFAULT_RECORD_SECONDS = 5.0

#: Default duration for ``duck move``.
DEFAULT_MOVE_SECONDS = 1.0

#: The verbs that route through the motion gate. ``mode`` only when ``--set`` is given.
GATED_VERBS = ("init", "relax", "enable", "do", "move", "mode", "look")


def _default_client(socket_path: str) -> RobotClient:
    """Build the real client. Replaced wholesale in tests via :data:`CLIENT_FACTORY`."""
    return RobotClient(socket_path, clock=time.monotonic)


#: The seam every verb opens its socket through. A test swaps this (or sets
#: ``args.client_factory``) to point at an in-process fake daemon.
CLIENT_FACTORY: Callable[[str], RobotClient] = _default_client


# ---------------------------------------------------------------------------
# addressing / connection
# ---------------------------------------------------------------------------


def _address(args: argparse.Namespace) -> DuckAddress:
    """Resolve ``--duck`` / ``--socket`` / ``--state`` to sockets. Opens nothing."""
    env = dict(os.environ)
    state = getattr(args, "state", None)
    if state:
        env["DUCK_SIM_STATE"] = state
    return resolve(
        getattr(args, "duck", None),
        socket=getattr(args, "socket", None),
        env=env,
        listdir=os.listdir,
    )


def _connect(
    args: argparse.Namespace, address: DuckAddress, *, verify_joints: bool = False
) -> RobotClient:
    """Open one client on ``address``. ``connect`` raises ``CliError`` (exit 2) itself."""
    senselog.install_logging()
    factory = getattr(args, "client_factory", None) or CLIENT_FACTORY
    client = factory(address.socket_path)
    client.connect(verify_joints=verify_joints)
    return client


def _request(client: RobotClient, method: str, params: Any = None) -> Any:
    """``client.request`` with the RPC error translated into the CLI's contract."""
    try:
        return client.request(method, params)
    except RpcError as exc:
        raise CliError(
            EXIT_ENV_ERROR,
            f"{method} failed on {client.socket_path}: {exc.message}",
            remediation=(
                "Check the daemon is running this API and the robot is in a state that "
                "accepts the call ('microduck-cli duck health')."
            ),
        ) from exc


def _outcome(result: Any) -> tuple[bool, str | None]:
    """Split an ``IntentResult``-shaped reply into ``(accepted, reason)``."""
    if not isinstance(result, dict):
        return (True, None)
    reason = result.get("reason")
    return (bool(result.get("accepted", True)), reason if isinstance(reason, str) else None)


def _refused(verb: str, reason: str | None) -> CliError:
    """The robot answered, and the answer was no. Exit 2: a verdict, not a usage error."""
    return CliError(
        EXIT_ENV_ERROR,
        f"the robot refused {verb}: {reason or 'no reason given'}",
        remediation=(
            "Ask 'microduck-cli duck health' and 'microduck-cli duck mode' for the robot's "
            "own account of why, then retry once the condition is cleared."
        ),
    )


# ---------------------------------------------------------------------------
# the gate
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _Call:
    """One JSON-RPC call a plan would send."""

    method: str
    params: Any = None
    note: str = ""

    def as_text(self) -> str:
        rendered = f"{self.method} {json.dumps(self.params, sort_keys=True)}"
        return f"{rendered}  ({self.note})" if self.note else rendered


def _plan(verb: str, address: DuckAddress, calls: Sequence[_Call], apply_command: str) -> dict:
    return {
        "verb": verb,
        "target": address.name or address.socket_path,
        "socket": address.socket_path,
        "calls": [call.as_text() for call in calls],
        "apply_command": apply_command,
    }


def _emit_plan(args: argparse.Namespace, plan: dict) -> int:
    """Print the zero-side-effect plan. Nothing is opened and nothing is sent."""
    text = render_dry_run(plan)
    if bool(getattr(args, "json", False)):
        emit_result(
            {
                "verb": plan["verb"],
                "duck": plan["target"],
                "socket": plan["socket"],
                "dry_run": True,
                "sent": False,
                "calls": list(plan["calls"]),
                "safety": safety_sentence(plan["verb"]),
                "apply_command": plan["apply_command"],
                "plan": text,
            },
            json_mode=True,
        )
    else:
        emit_result(text, json_mode=False)
    return 0


def _prompt(plan: dict) -> str:
    lines = [f"{plan['verb']} on {plan['target']} ({plan['socket']}) would send:"]
    lines += [f"  - {call}" for call in plan["calls"]]
    lines.append(f"Safety: {safety_sentence(plan['verb'])}")
    lines.append("Proceed? [y/N]")
    return "\n".join(lines)


def _gated(
    args: argparse.Namespace,
    verb: str,
    address: DuckAddress,
    calls: Sequence[_Call],
    apply_command: str,
    action: Callable[[], int],
) -> int:
    """Run *action* only once the gate says so; otherwise plan, or prompt.

    The socket is opened inside *action*, so the ``DRY_RUN`` branch performs no
    I/O at all — not a send, not a connect.
    """
    plan = _plan(verb, address, calls, apply_command)
    mode = consent(bool(getattr(args, "apply", False)))
    if mode is Consent.DRY_RUN:
        return _emit_plan(args, plan)
    if mode is Consent.PROMPT and not confirm_on_tty(_prompt(plan)):
        raise CliError(
            EXIT_USER_ERROR,
            f"{verb} aborted at the confirmation prompt; nothing was sent",
            remediation=HINT_APPLY,
        )
    return action()


def _sent(
    args: argparse.Namespace,
    verb: str,
    address: DuckAddress,
    calls: Sequence[_Call],
    summary: str,
    extra: dict[str, Any] | None = None,
) -> int:
    """Report a completed gated verb on stdout, in text or JSON."""
    payload: dict[str, Any] = {
        "verb": verb,
        "duck": address.name,
        "socket": address.socket_path,
        "dry_run": False,
        "sent": True,
        "calls": [call.as_text() for call in calls],
        "summary": summary,
    }
    payload.update(extra or {})
    if bool(getattr(args, "json", False)):
        emit_result(payload, json_mode=True)
    else:
        emit_result(summary, json_mode=False)
    return 0


# ---------------------------------------------------------------------------
# descriptive verbs
# ---------------------------------------------------------------------------


def duck_sections() -> list[dict[str, object]]:
    """Sections describing the ``duck`` noun (used by ``duck overview``)."""
    return [
        {"title": "Purpose", "items": [_PURPOSE]},
        {"title": "Verbs", "items": list(VERBS)},
        {"title": "Gated verbs", "items": [", ".join(GATED_VERBS)]},
        {"title": "Status", "items": [_STATUS]},
    ]


def cmd_duck_overview(args: argparse.Namespace) -> int:
    emit_overview(_SUBJECT, duck_sections(), json_mode=bool(getattr(args, "json", False)))
    return 0


def _health_text(address: DuckAddress, health: dict) -> str:
    loop = health.get("control_loop") or {}
    bus = health.get("bus") or {}
    imu = health.get("imu") or {}
    if health.get("healthy"):
        verdict = "healthy"
    elif health.get("degraded"):
        verdict = f"degraded: {health.get('reason') or 'no reason given'}"
    else:
        verdict = f"unhealthy: {health.get('reason') or 'no reason given'}"
    lines = [
        f"# {address.name or address.socket_path}: {verdict}",
        "",
        f"- socket   : {address.socket_path}",
        f"- loop     : target {loop.get('target_hz')} Hz, achieved {loop.get('achieved_hz')}, "
        f"{loop.get('ticks')} ticks, {loop.get('missed')} missed",
        f"- bus      : {bus.get('consecutive_errors')} consecutive errors, "
        f"{bus.get('startup_failures')} startup failures",
        f"- imu      : {'ready' if imu.get('ready') else 'not ready'}",
    ]
    battery = health.get("battery")
    if isinstance(battery, dict):
        lines.append(f"- battery  : {battery.get('percent')}% ({battery.get('volts')} V)")
    else:
        lines.append("- battery  : not measured")
    motors = health.get("motors")
    if isinstance(motors, dict):
        lines.append(f"- motors   : hottest {motors.get('hottest_c')} C")
    return "\n".join(lines)


def cmd_health(args: argparse.Namespace) -> int:
    """``robot.health``, rendered — and a non-zero exit when the answer is 'no'.

    Mirrors ``robotctl health``: the report is printed either way, and an
    unhealthy robot exits non-zero so a script can gate on it. Exit 2, the
    environment-error code, because the robot answered correctly and the answer
    was a verdict about the environment, not a usage mistake.
    """
    address = _address(args)
    client = _connect(args, address)
    try:
        answer = _request(client, proto.ROBOT_HEALTH)
    finally:
        client.close()
    health = answer if isinstance(answer, dict) else {}
    healthy = bool(health.get("healthy"))
    if bool(getattr(args, "json", False)):
        emit_result(
            {
                "duck": address.name,
                "socket": address.socket_path,
                "healthy": healthy,
                "degraded": bool(health.get("degraded")),
                "reason": health.get("reason"),
                "health": health,
            },
            json_mode=True,
        )
    else:
        emit_result(_health_text(address, health), json_mode=False)
    if not healthy:
        raise CliError(
            EXIT_ENV_ERROR,
            f"{address.name or address.socket_path} is not healthy: "
            f"{health.get('reason') or 'no reason given'}",
            remediation=(
                "Read the report above, then check the daemon's own logs "
                f"(journalctl -u robotd) for the failing subsystem — see {CHEATSHEET}"
            ),
        )
    return 0


def cmd_version(args: argparse.Namespace) -> int:
    """The daemon's ``hello``: API version, daemon version, build revision."""
    address = _address(args)
    client = _connect(args, address)
    try:
        daemon = client.daemon
        skew = client.api_skew
    finally:
        client.close()
    payload = {
        "duck": address.name,
        "socket": address.socket_path,
        "api_version": daemon.api_version,
        "daemon_version": daemon.daemon_version,
        "revision": daemon.revision,
        "client_api_version": proto.API_VERSION,
        "api_skew": list(skew) if skew else None,
    }
    if bool(getattr(args, "json", False)):
        emit_result(payload, json_mode=True)
        return 0
    lines = [
        f"# {address.name or address.socket_path}",
        "",
        f"- api_version    : {daemon.api_version} (this CLI speaks {proto.API_VERSION})",
        f"- daemon_version : {daemon.daemon_version}",
        f"- revision       : {daemon.revision or 'unreported'}",
    ]
    if skew:
        lines.append(f"- api skew       : daemon {skew[0]} vs client {skew[1]} — reported, not")
        lines.append("                   refused; a newer method may simply be missing")
    emit_result("\n".join(lines), json_mode=False)
    return 0


def _state_line(params: Any) -> str:
    """One human line per ``robot.state`` frame (the piped shape of ``robotctl monitor``)."""
    state = params if isinstance(params, dict) else {}
    loop = state.get("loop") or {}
    safety = state.get("safety") or {}
    move = state.get("move") or {}
    return (
        f"t={state.get('t')} hz={loop.get('hz')} missed={loop.get('missed')} "
        f"policy={state.get('policy')} fallen={safety.get('fallen')} "
        f"limp={safety.get('limp')} requested={move.get('requested')} "
        f"applied={move.get('applied')}"
    )


def cmd_monitor(args: argparse.Namespace) -> int:
    """Subscribe and print one line per state frame until Ctrl-C (or ``--frames``).

    ``--json`` makes stdout pure NDJSON — one ``robot.state`` params object per
    line, nothing else — so ``| jq`` and ``> log`` both behave. ``--frames`` is
    this CLI's addition to ``robotctl monitor``: a bounded run an agent can wait
    on without sending itself a signal.
    """
    address = _address(args)
    client = _connect(args, address)
    json_mode = bool(getattr(args, "json", False))
    limit = int(getattr(args, "frames", 0) or 0)
    seen: float | None = None
    frames = 0
    try:
        client.subscribe(int(args.hz))
        while limit == 0 or frames < limit:
            peeked = client.peek(proto.ROBOT_STATE)
            if peeked is not None and peeked[1] is not None and peeked[1] != seen:
                seen = peeked[1]
                frames += 1
                emit_result(peeked[0] if json_mode else _state_line(peeked[0]), json_mode=json_mode)
                continue
            time.sleep(0.002)
    except KeyboardInterrupt:
        emit_diagnostic("monitor: interrupted; the subscription is closed")
    except RpcError as exc:
        raise CliError(
            EXIT_ENV_ERROR,
            f"{proto.ROBOT_SUBSCRIBE} failed on {address.socket_path}: {exc.message}",
            remediation="Check the daemon is up ('microduck-cli duck health').",
        ) from exc
    finally:
        client.close()
    return 0


def cmd_stop(args: argparse.Namespace) -> int:
    """``robot.stop`` — zero the intents. Ungated, and deliberately: see the safety line."""
    address = _address(args)
    client = _connect(args, address)
    try:
        result = _request(client, proto.ROBOT_STOP)
    finally:
        client.close()
    accepted, reason = _outcome(result)
    if not accepted:
        raise _refused("stop", reason)
    safety = safety_sentence("stop")
    if bool(getattr(args, "json", False)):
        emit_result(
            {
                "verb": "stop",
                "duck": address.name,
                "socket": address.socket_path,
                "accepted": True,
                "safety": safety,
                "result": result,
            },
            json_mode=True,
        )
    else:
        emit_result(
            f"stopped {address.name or address.socket_path}\nSafety: {safety}", json_mode=False
        )
    return 0


def cmd_quack(args: argparse.Namespace) -> int:
    """``robot.sound`` with this duck's own voice — the loudest way to tell ducks apart.

    Routed through :meth:`RobotClient.send`, which follows the protocol's own
    classification: ``robot.sound`` is a *continuous* intent on the pinned API,
    so it goes as a notification and there is no acceptance to read. A daemon
    that does answer (a newer API, where the call is discrete) has its refusal
    surfaced with the robot's own reason.
    """
    address = _address(args)
    client = _connect(args, address)
    try:
        result = client.send(proto.ROBOT_SOUND, {"tag": QUACK_TAG})
        if not isinstance(result, dict):
            # The sound went out as a notification: queued, unanswered, and the
            # client's writer drains FIFO — so one *answered* question afterwards
            # proves the sound frame left the queue before we close the socket.
            # robot.mode is that question: upstream's own "changes nothing" call.
            _request(client, proto.ROBOT_MODE)
    finally:
        client.close()
    queued = result is not False
    accepted, reason = _outcome(result) if isinstance(result, dict) else (queued, None)
    if not accepted:
        raise _refused("quack", reason)
    if not queued:
        raise CliError(
            EXIT_ENV_ERROR,
            f"{proto.ROBOT_SOUND} was not sent to {address.socket_path}: the link is down or "
            "the write queue is full",
            remediation="Check the daemon is up ('microduck-cli duck health') and retry.",
        )
    if bool(getattr(args, "json", False)):
        emit_result(
            {
                "verb": "quack",
                "duck": address.name,
                "socket": address.socket_path,
                "tag": QUACK_TAG,
                "notification": not isinstance(result, dict),
                "accepted": accepted,
                "reason": reason,
            },
            json_mode=True,
        )
    else:
        emit_result(f"quack: {address.name or address.socket_path} (tag {QUACK_TAG})", False)
    return 0


def cmd_configure(args: argparse.Namespace) -> int:
    """``--list``: the params file **this CLI generated** for this duck, if any.

    ``robotctl configure`` edits the on-robot ``/etc/robot/robotd.toml`` in a
    TUI. This verb deliberately does neither: it never reads or writes the
    robot's own config (``tests/test_no_config_writes.py`` enforces that), it
    reports the laptop-side params file :mod:`microduck_cli.env.params` renders
    under the state directory, and it says plainly when there is none.
    """
    if not bool(getattr(args, "list", False)):
        raise CliError(
            EXIT_USER_ERROR,
            "duck configure supports only --list on this CLI",
            remediation=(
                "run 'microduck-cli duck configure --list'; editing the robot's own "
                "/etc/robot/robotd.toml is robotctl configure's job, not this CLI's — see "
                f"{CHEATSHEET}"
            ),
        )
    address = _address(args)
    path = os.path.join(address.state_dir, f"{address.name}.toml")
    try:
        with open(path, "r", encoding="utf-8") as handle:
            contents: str | None = handle.read()
    except OSError:
        contents = None
    if bool(getattr(args, "json", False)):
        emit_result(
            {
                "verb": "configure",
                "duck": address.name,
                "state_dir": address.state_dir,
                "params_file": path,
                "present": contents is not None,
                "contents": contents,
            },
            json_mode=True,
        )
    elif contents is None:
        emit_result(
            f"no generated params file for '{address.name}' in {address.state_dir} "
            f"(looked for {path})",
            json_mode=False,
        )
    else:
        emit_result(f"# {path}\n{contents.rstrip()}", json_mode=False)
    return 0


def cmd_record(args: argparse.Namespace) -> int:
    """Record every sense this duck reports as JSONL — stdout by default.

    Stdout carries records and nothing else; the summary and every drop go to
    stderr. ``--out FILE`` writes the records to a file instead and puts the
    summary on stdout, where a result belongs.
    """
    senselog.install_logging()
    address = _address(args)
    client = _connect(args, address)
    out = getattr(args, "out", None) or "-"
    seconds = float(getattr(args, "seconds", DEFAULT_RECORD_SECONDS))
    handle = None
    try:
        if out == "-":
            stream = sys.stdout
        else:
            handle = open(out, "w", encoding="utf-8")
            stream = handle
        summary = Recorder(client, stream, destination=out).run(seconds)
    except OSError as exc:
        raise CliError(
            EXIT_ENV_ERROR,
            f"could not write the recording to {out}: {exc}",
            remediation="Pass --out to a writable path, or drop it to record on stdout.",
        ) from exc
    finally:
        if handle is not None:
            handle.close()
        client.close()
    if out == "-":
        # stdout is the record stream: the summary is a diagnostic, not a result.
        emit_diagnostic(summary.as_text())
    elif bool(getattr(args, "json", False)):
        emit_result(summary.to_dict(), json_mode=True)
    else:
        emit_result(summary.as_text(), json_mode=False)
    return 0


# ---------------------------------------------------------------------------
# gated verbs
# ---------------------------------------------------------------------------


def _apply_command(verb: str, args: argparse.Namespace, extra: Iterable[str] = ()) -> str:
    parts = ["microduck-cli", "duck", verb, *extra]
    if getattr(args, "duck", None):
        parts += ["--duck", str(args.duck)]
    if getattr(args, "socket", None):
        parts += ["--socket", str(args.socket)]
    if getattr(args, "state", None):
        parts += ["--state", str(args.state)]
    parts.append("--apply")
    return " ".join(parts)


def _send_calls(args: argparse.Namespace, address: DuckAddress, calls: Sequence[_Call]) -> Any:
    """Connect, send each discrete call in order, return the last result."""
    client = _connect(args, address)
    try:
        result: Any = None
        for call in calls:
            result = _request(client, call.method, call.params)
            accepted, reason = _outcome(result)
            if not accepted:
                raise _refused(call.method, reason)
        return result
    finally:
        client.close()


def _simple_gated(
    args: argparse.Namespace,
    verb: str,
    calls: Sequence[_Call],
    summary: Callable[[Any], str],
    extra_argv: Iterable[str] = (),
) -> int:
    address = _address(args)

    def action() -> int:
        result = _send_calls(args, address, calls)
        return _sent(args, verb, address, calls, summary(result), {"result": result})

    return _gated(args, verb, address, calls, _apply_command(verb, args, extra_argv), action)


def cmd_init(args: argparse.Namespace) -> int:
    """``robot.init`` — power the joints and ramp to the home pose. Moves every joint."""
    calls = (_Call(proto.ROBOT_INIT, {}, "powers the joints, ramps to home over ~2 s"),)
    return _simple_gated(args, "init", calls, lambda _r: "init accepted: ramping to the home pose")


def cmd_relax(args: argparse.Namespace) -> int:
    """``robot.relax`` — cut power. The robot collapses if nothing holds it, so ``--yes``."""
    calls = (_Call(proto.ROBOT_RELAX, {}, "cuts power to every joint"),)
    if bool(getattr(args, "apply", False)) and not bool(getattr(args, "yes", False)):
        raise CliError(
            EXIT_USER_ERROR,
            "relax --apply also needs --yes: " + safety_sentence("relax"),
            remediation=(
                "re-run as 'microduck-cli duck relax --yes --apply' once the robot is held "
                "or on its stand"
            ),
        )
    # --yes is an acknowledgement, not a shortcut: it never substitutes for the gate.
    # On a TTY the prompt still happens (the gate is this CLI's layer, not robotctl's),
    # and on a non-TTY `relax --yes` still only prints the plan until --apply is given.
    return _simple_gated(args, "relax", calls, lambda _r: "relaxed: power cut, the robot is limp")


def cmd_enable(args: argparse.Namespace) -> int:
    """``robot.enable`` — hand the robot to its policy, or take it back (the Start button)."""
    if getattr(args, "toggle", False):
        params: dict[str, Any] = {"toggle": True}
        extra = ["--toggle"]
    elif getattr(args, "off", False):
        params, extra = {"on": False}, ["--off"]
    else:
        params, extra = {"on": True}, ["--on"]
    calls = (_Call(proto.ROBOT_ENABLE, params, "hands the robot to its policy"),)

    def summary(result: Any) -> str:
        _, reason = _outcome(result)
        return f"enable {json.dumps(params, sort_keys=True)}: {reason or 'accepted'}"

    return _simple_gated(args, "enable", calls, summary, extra)


def cmd_do(args: argparse.Namespace) -> int:
    """``robot.do`` — one skill, the same request the gamepad's buttons send."""
    wire = SKILL_ARGS[args.skill]
    calls = (_Call(proto.ROBOT_DO, {"skill": wire}, f"one-shot skill '{args.skill}'"),)

    def summary(result: Any) -> str:
        _, reason = _outcome(result)
        return f"do {args.skill} ({wire}): {reason or 'accepted'}"

    return _simple_gated(args, "do", calls, summary, [args.skill])


def cmd_mode(args: argparse.Namespace) -> int:
    """``robot.mode`` to read (ungated, changes nothing) and ``robot.setMode`` to write."""
    target = getattr(args, "set", None)
    address = _address(args)
    if target is None:
        client = _connect(args, address)
        try:
            result = _request(client, proto.ROBOT_MODE)
        finally:
            client.close()
        mode = result.get("mode") if isinstance(result, dict) else None
        if bool(getattr(args, "json", False)):
            emit_result(
                {"verb": "mode", "duck": address.name, "mode": mode, "result": result},
                json_mode=True,
            )
        else:
            emit_result(f"mode: {mode}", json_mode=False)
        return 0
    calls = (_Call(proto.ROBOT_SET_MODE, {"mode": target}, f"switches the drive mode to {target}"),)

    def summary(result: Any) -> str:
        _, reason = _outcome(result)
        return f"mode set to {target}: {reason or 'accepted'}"

    return _simple_gated(args, "mode", calls, summary, ["--set", target])


def cmd_look(args: argparse.Namespace) -> int:
    """``robot.look`` — point the camera at a trunk-frame point (X forward, Y left, Z up)."""
    params = {
        "x": float(args.x),
        "y": float(args.y),
        "z": float(args.z),
        "neck_pitch": float(args.neck_pitch),
    }
    calls = (_Call(proto.ROBOT_LOOK, params, "moves the head via the daemon's gaze IK"),)
    extra = [
        "--x",
        str(args.x),
        "--y",
        str(args.y),
        "--z",
        str(args.z),
        "--neck-pitch",
        str(args.neck_pitch),
    ]

    def summary(result: Any) -> str:
        head = result.get("head") if isinstance(result, dict) else None
        return f"looking at ({params['x']}, {params['y']}, {params['z']}); head {head}"

    return _simple_gated(args, "look", calls, summary, extra)


def cmd_move(args: argparse.Namespace) -> int:
    """Drive for a bounded time: ``robot.move`` at intent rate, then ``robot.stop``.

    ``robot.move`` is continuous and deadman-backed — one notification decays,
    so this refreshes it at :data:`INTENT_HZ` for ``--duration`` seconds and
    then sends ``robot.stop``. Ctrl-C stops too: the stop is in a ``finally``,
    so an interrupted drive never leaves the robot running.
    """
    address = _address(args)
    params = {"vx": float(args.vx), "vy": float(args.vy), "vyaw": float(args.vyaw)}
    duration = float(args.duration)
    frames = max(1, int(duration * INTENT_HZ))
    calls = (
        _Call(
            proto.ROBOT_MOVE,
            params,
            f"notification x{frames} at {INTENT_HZ:g} Hz for {duration:g}s (refreshes the deadman)",
        ),
        _Call(proto.ROBOT_STOP, None, "sent last, and on Ctrl-C"),
    )
    extra = [
        "--vx",
        str(args.vx),
        "--vy",
        str(args.vy),
        "--vyaw",
        str(args.vyaw),
        "--duration",
        str(args.duration),
    ]

    def action() -> int:
        client = _connect(args, address)
        sent = 0
        interrupted = False
        try:
            period = 1.0 / INTENT_HZ
            deadline = time.monotonic() + duration
            while time.monotonic() < deadline:
                if client.notify(proto.ROBOT_MOVE, params):
                    sent += 1
                time.sleep(period)
        except KeyboardInterrupt:
            interrupted = True
        finally:
            try:
                _request(client, proto.ROBOT_STOP)
            finally:
                client.close()
        summary = (
            f"moved {json.dumps(params, sort_keys=True)} for {duration:g}s "
            f"({sent} intents at {INTENT_HZ:g} Hz), then stopped"
        )
        if interrupted:
            summary += " — interrupted"
        return _sent(
            args,
            "move",
            address,
            calls,
            summary,
            {"intents_sent": sent, "interrupted": interrupted},
        )

    return _gated(args, "move", address, calls, _apply_command("move", args, extra), action)


# ---------------------------------------------------------------------------
# parser wiring
# ---------------------------------------------------------------------------


def _no_verb(args: argparse.Namespace) -> int:
    # `microduck-cli duck` with no sub-verb prints the noun's overview.
    return cmd_duck_overview(args)


def _common(p: argparse.ArgumentParser) -> argparse.ArgumentParser:
    """The addressing + output flags every duck verb carries."""
    p.add_argument(
        "--duck",
        metavar="NAME",
        help="Duck name (default: DUCK_SIM_DUCK, else the single duck in the state directory).",
    )
    p.add_argument("--socket", metavar="PATH", help="Robot control socket path; wins over --duck.")
    p.add_argument(
        "--state",
        metavar="DIR",
        help="State directory holding the sockets (default: DUCK_SIM_STATE, "
        "else ~/.cache/duck-sim).",
    )
    p.add_argument("--json", action="store_true", help="Emit structured JSON.")
    return p


def _gate_flag(p: argparse.ArgumentParser) -> argparse.ArgumentParser:
    p.add_argument(
        "--apply",
        action="store_true",
        help="Actually send. Without it a TTY confirms and a non-TTY prints the plan "
        "and sends nothing.",
    )
    return p


def register(sub: argparse._SubParsersAction) -> None:
    p = sub.add_parser(
        "duck",
        help="Operate the duck (see 'microduck-cli duck overview').",
    )
    p.add_argument("--json", action="store_true", help="Emit structured JSON.")
    p.set_defaults(func=_no_verb, json=False)
    # `p` is a _CliArgumentParser (the top-level subparsers were built with that
    # parser_class); propagate it so `duck <verb>` parse errors route through the
    # structured error contract instead of argparse's default stderr/exit 2.
    noun_sub = p.add_subparsers(dest="duck_command", parser_class=type(p))

    ov = noun_sub.add_parser("overview", help="Describe the duck noun.")
    ov.add_argument(
        "target",
        nargs="?",
        help="Ignored — overview always describes this noun. Accepted so a stray "
        "path argument never hard-fails.",
    )
    ov.add_argument("--json", action="store_true", help="Emit structured JSON.")
    ov.set_defaults(func=cmd_duck_overview)

    health = _common(
        noun_sub.add_parser("health", help="The robot's own verdict; exit 2 when not healthy.")
    )
    health.set_defaults(func=cmd_health)

    version = _common(noun_sub.add_parser("version", help="What the daemon says it is running."))
    version.set_defaults(func=cmd_version)

    monitor = _common(noun_sub.add_parser("monitor", help="Watch the control loop live."))
    monitor.add_argument(
        "--hz",
        type=int,
        default=DEFAULT_MONITOR_HZ,
        help="Frames per second (the robot decimates server-side).",
    )
    monitor.add_argument(
        "--frames", type=int, default=0, help="Stop after N frames (0 = until Ctrl-C)."
    )
    monitor.set_defaults(func=cmd_monitor)

    stop = _common(noun_sub.add_parser("stop", help="Zero the intents. Not an emergency stop."))
    stop.set_defaults(func=cmd_stop)

    quack = _common(noun_sub.add_parser("quack", help="Play this robot's own voice."))
    quack.set_defaults(func=cmd_quack)

    configure = _common(
        noun_sub.add_parser("configure", help="Report the generated params file for this duck.")
    )
    configure.add_argument(
        "--list",
        action="store_true",
        help="Print the generated params file (the only mode this CLI offers).",
    )
    configure.set_defaults(func=cmd_configure)

    record = _common(noun_sub.add_parser("record", help="Record every sense as JSONL."))
    record.add_argument(
        "--seconds", type=float, default=DEFAULT_RECORD_SECONDS, help="How long to record."
    )
    record.add_argument(
        "--out", metavar="FILE", default="-", help="Write records here ('-' = stdout, default)."
    )
    record.set_defaults(func=cmd_record)

    init = _gate_flag(
        _common(noun_sub.add_parser("init", help="Power the joints and ramp to the home pose."))
    )
    init.set_defaults(func=cmd_init)

    relax = _gate_flag(
        _common(
            noun_sub.add_parser("relax", help="Cut power; the robot collapses if nothing holds it.")
        )
    )
    relax.add_argument(
        "--yes",
        action="store_true",
        help="Let go without asking (robotctl's own flag; required alongside --apply).",
    )
    relax.set_defaults(func=cmd_relax)

    enable = _gate_flag(
        _common(
            noun_sub.add_parser("enable", help="Hand the robot to its policy, or take it back.")
        )
    )
    on_off = enable.add_mutually_exclusive_group()
    on_off.add_argument("--on", action="store_true", help="Drive (the default).")
    on_off.add_argument(
        "--off", action="store_true", help="Take it back; the robot holds its pose."
    )
    on_off.add_argument("--toggle", action="store_true", help="Flip whichever state it is in.")
    enable.set_defaults(func=cmd_enable)

    do = _gate_flag(_common(noun_sub.add_parser("do", help="Run a one-shot skill.")))
    do.add_argument("skill", choices=sorted(SKILL_ARGS), help="The skill to run.")
    do.set_defaults(func=cmd_do)

    mode = _gate_flag(
        _common(noun_sub.add_parser("mode", help="Read the drive mode, or set it with --set."))
    )
    mode.add_argument("--set", choices=MODES, help="Switch the drive mode (gated).")
    mode.set_defaults(func=cmd_mode)

    look = _gate_flag(
        _common(noun_sub.add_parser("look", help="Point the camera at a trunk-frame point."))
    )
    look.add_argument("--x", type=float, required=True, help="Forward, metres.")
    look.add_argument(
        "--y", type=float, default=0.0, help="Left, metres (default 0: straight ahead)."
    )
    look.add_argument("--z", type=float, default=0.0, help="Up, metres.")
    look.add_argument(
        "--neck-pitch",
        type=float,
        default=0.0,
        dest="neck_pitch",
        help="Neck posture to aim around, radians.",
    )
    look.set_defaults(func=cmd_look)

    move = _gate_flag(
        _common(noun_sub.add_parser("move", help="Drive for a bounded time, then stop."))
    )
    move.add_argument("--vx", type=float, default=0.0, help="Forward velocity.")
    move.add_argument("--vy", type=float, default=0.0, help="Lateral velocity.")
    move.add_argument("--vyaw", type=float, default=0.0, help="Yaw rate.")
    move.add_argument(
        "--duration",
        type=float,
        default=DEFAULT_MOVE_SECONDS,
        help="Seconds to drive before stopping.",
    )
    move.set_defaults(func=cmd_move)
