"""``microduck-cli env`` — noun group for the MicroDuck environment.

Mirrors :mod:`microduck_cli.cli._commands.cli` — the nested subparsers are
built with ``parser_class=type(p)`` so a parse error under this noun keeps
the structured ``error:``/``hint:`` contract instead of argparse's default
exit 2, and a bare ``microduck-cli env`` prints this noun's overview.

Five verbs today: ``doctor`` (delegates to :mod:`microduck_cli.env.doctor`),
``up``/``down``/``status`` (drive :mod:`microduck_cli.env.stack`, or shell out
to ``<microduck clone>/scripts/duck-sim`` when ``--upstream-launcher`` is
passed and the clone has it), and ``hosts`` (delegates to
:mod:`microduck_cli.env.hosts`). Every clone path, the ONNX runtime path and
the per-duck params file are derived — never typed by the operator.

Test seams
----------
Every side effect this module performs is reachable through a module-level
attribute a test can monkeypatch, mirroring the injection style
``env/stack.py`` itself uses:

* ``_stack_runner`` / ``_stack_proc_cmdline`` / ``_stack_exists`` /
  ``_stack_kill`` / ``_stack_sleep`` / ``_stack_monotonic`` — passed straight
  into every :class:`~microduck_cli.env.stack.SimStack` this module builds.
* ``_default_probe`` — the ``env doctor`` probe factory
  (default: :func:`microduck_cli.env.doctor.default_probe`).
* ``_wait_for_healthy(socket_path, timeout) -> bool`` — the ``env up``
  post-bring-up health wait (default: :func:`_default_wait_for_healthy`).
* ``_hello_probe(socket_path) -> bool`` — the single-shot ``env status``
  handshake probe (default: :func:`_default_hello_probe`).
* ``_port_listening(port) -> bool`` — the ``env down`` TCP knock.
* ``_subprocess_run`` — the ``--upstream-launcher`` shell-out.

Verb summaries live in :mod:`microduck_cli.explain.env` (``VERBS``), which the
global ``overview``/``learn`` surfaces read too — so adding a verb here means
editing this file, ``explain/env.py`` and ``tests/test_env.py``, and nothing
else.
"""

from __future__ import annotations

import argparse
import json
import os
import socket as _socket
import subprocess  # nosec B404 - only used with a fixed argv, never shell=True
import time
from collections.abc import Callable

from microduck_cli.cli._commands.overview import emit_overview
from microduck_cli.cli._errors import EXIT_ENV_ERROR, EXIT_SUCCESS, CliError
from microduck_cli.cli._output import emit_diagnostic, emit_result
from microduck_cli.duck.addressing import DEFAULT_STATE_DIR
from microduck_cli.env import doctor as _doctor
from microduck_cli.env import hosts as _hosts
from microduck_cli.env.stack import (
    ProcCmdline,
    Runner,
    SimStack,
    default_proc_cmdline,
    default_runner,
)
from microduck_cli.explain.env import VERBS
from microduck_cli.ipc import proto as _proto

_SUBJECT = "microduck-cli env"
_PURPOSE = "Bring up and doctor the MicroDuck environment — the simulator stack or a real duck."

#: The upstream doc every env remediation points at.
_UPSTREAM_SIM_DOC = (
    "https://github.com/pollen-robotics/microduck/blob/sim-remote-io/docs/design/simulation.md"
)

#: Timeouts for the post-bring-up `hello` + `robot.health` wait in `env up`.
_FAKE_HEALTH_TIMEOUT_S = 60.0
_SIM_HEALTH_TIMEOUT_S = 120.0
_RPC_ROUNDTRIP_TIMEOUT_S = 2.0
_HEALTH_POLL_INTERVAL_S = 0.5

# ---------------------------------------------------------------------------
# Test seams — every side effect goes through one of these module attributes.
# ---------------------------------------------------------------------------

_stack_runner: Runner = default_runner
_stack_proc_cmdline: ProcCmdline = default_proc_cmdline
_stack_exists: Callable[[str], bool] = os.path.exists
_stack_kill: Callable[[int, int], None] = os.kill
_stack_sleep: Callable[[float], None] = time.sleep
_stack_monotonic: Callable[[], float] = time.monotonic
_subprocess_run: Callable[..., subprocess.CompletedProcess] = subprocess.run


def _make_stack(clone: str, rl: str, state_dir: str) -> SimStack:
    return SimStack(
        clone=clone,
        rl=rl,
        state_dir=state_dir,
        runner=_stack_runner,
        proc_cmdline=_stack_proc_cmdline,
        exists=_stack_exists,
        kill=_stack_kill,
        sleep=_stack_sleep,
        monotonic=_stack_monotonic,
    )


def _resolve_state_dir(explicit: str | None) -> str:
    if explicit:
        return explicit
    raw = os.environ.get("DUCK_SIM_STATE") or DEFAULT_STATE_DIR
    if raw.startswith("~"):
        return os.path.expanduser(raw)
    return raw


# ---------------------------------------------------------------------------
# A tiny private JSON-RPC roundtrip over a unix control socket.
#
# TODO(t10): swap this for microduck_cli.ipc.client once that JSON-RPC client
# lands — this is a minimal, private stand-in (stdlib socket + json only) so
# `env up`/`env status` don't have to wait on it.
# ---------------------------------------------------------------------------


def _rpc_roundtrip(
    socket_path: str,
    method: str,
    params: dict[str, object] | None,
    *,
    request_id: int,
    timeout: float,
) -> dict[str, object]:
    with _socket.socket(_socket.AF_UNIX, _socket.SOCK_STREAM) as sock:
        sock.settimeout(timeout)
        sock.connect(socket_path)
        payload: dict[str, object] = {
            "jsonrpc": _proto.JSONRPC_VERSION,
            "id": request_id,
            "method": method,
        }
        if params is not None:
            payload["params"] = params
        sock.sendall((json.dumps(payload) + "\n").encode("utf-8"))
        buf = b""
        while b"\n" not in buf:
            chunk = sock.recv(4096)
            if not chunk:
                break
            buf += chunk
    line = buf.split(b"\n", 1)[0]
    return json.loads(line.decode("utf-8"))


def _default_wait_for_healthy(socket_path: str, timeout: float) -> bool:
    """Poll `hello` then `robot.health` over `socket_path` until healthy or `timeout`."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            hello = _rpc_roundtrip(
                socket_path,
                _proto.HELLO,
                {"api_version": _proto.API_VERSION},
                request_id=1,
                timeout=_RPC_ROUNDTRIP_TIMEOUT_S,
            )
            if "error" not in hello:
                health = _rpc_roundtrip(
                    socket_path,
                    _proto.ROBOT_HEALTH,
                    None,
                    request_id=2,
                    timeout=_RPC_ROUNDTRIP_TIMEOUT_S,
                )
                if "error" not in health:
                    result = health.get("result")
                    if not isinstance(result, dict) or result.get("healthy", True):
                        return True
        except (OSError, ValueError):
            pass
        time.sleep(_HEALTH_POLL_INTERVAL_S)
    return False


def _default_hello_probe(socket_path: str) -> bool:
    """One-shot `hello` handshake — no retry loop (used by `env status`)."""
    try:
        response = _rpc_roundtrip(
            socket_path,
            _proto.HELLO,
            {"api_version": _proto.API_VERSION},
            request_id=1,
            timeout=1.0,
        )
    except (OSError, ValueError):
        return False
    return "error" not in response


def _default_port_listening(port: int) -> bool:
    try:
        with _socket.socket(_socket.AF_INET, _socket.SOCK_STREAM) as probe:
            probe.settimeout(0.2)
            return probe.connect_ex(("127.0.0.1", port)) == 0
    except OSError:
        return False


_default_probe: Callable[[], _doctor.EnvProbe] = _doctor.default_probe
_wait_for_healthy: Callable[[str, float], bool] = _default_wait_for_healthy
_hello_probe: Callable[[str], bool] = _default_hello_probe
_port_listening: Callable[[int], bool] = _default_port_listening


# ---------------------------------------------------------------------------
# overview
# ---------------------------------------------------------------------------


def env_sections() -> list[dict[str, object]]:
    """Sections describing the ``env`` noun (used by ``env overview``)."""
    return [
        {"title": "Purpose", "items": [_PURPOSE]},
        {"title": "Verbs", "items": list(VERBS)},
    ]


def cmd_env_overview(args: argparse.Namespace) -> int:
    emit_overview(
        _SUBJECT,
        env_sections(),
        json_mode=bool(getattr(args, "json", False)),
    )
    return 0


def _no_verb(args: argparse.Namespace) -> int:
    # `microduck-cli env` with no sub-verb prints the noun's overview.
    return cmd_env_overview(args)


# ---------------------------------------------------------------------------
# doctor
# ---------------------------------------------------------------------------


def cmd_env_doctor(args: argparse.Namespace) -> int:
    probe = _default_probe()
    report = _doctor.diagnose(probe)
    json_mode = bool(getattr(args, "json", False))
    if json_mode:
        emit_result(report, json_mode=True)
    else:
        emit_result(_doctor.render_text(report), json_mode=False)
    return EXIT_SUCCESS if report["healthy"] else EXIT_ENV_ERROR


# ---------------------------------------------------------------------------
# up
# ---------------------------------------------------------------------------

#: `scripts/duck-sim`'s documented env knobs (docs/specs, s18) — set from our
#: own flags so the operator never has to name a clone, a state dir or a port
#: twice.
_UPSTREAM_LAUNCHER_NAME = "duck-sim"


def _upstream_launcher_path(microduck_clone: str) -> str:
    return os.path.join(microduck_clone, "scripts", _UPSTREAM_LAUNCHER_NAME)


def _run_upstream_launcher(
    *,
    launcher: str,
    microduck_clone: str,
    rl_clone: str,
    mode: str,
    ducks: int,
    port: int,
    scene: str | None,
    state_dir: str,
) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    env["DUCK_SIM_STATE"] = state_dir
    env["DUCK_SIM_RL"] = rl_clone
    env["DUCK_SIM_PORT"] = str(port)
    env["DUCK_SIM_DUCKS"] = str(ducks)
    if scene:
        env["DUCK_SIM_SCENE"] = scene
    argv = [launcher, "up", "--fake" if mode == "fake" else "--sim"]
    return _subprocess_run(  # nosec B603 - fixed argv, shell=False
        argv, cwd=microduck_clone, env=env, capture_output=True, text=True, check=False
    )


def cmd_env_up(args: argparse.Namespace) -> int:
    json_mode = bool(getattr(args, "json", False))
    mode = args.mode
    state_dir = _resolve_state_dir(args.state)
    microduck_clone, rl_clone = _doctor.resolve_clone_paths(os.environ)

    if not microduck_clone or not rl_clone:
        raise CliError(
            code=EXIT_ENV_ERROR,
            message="microduck and/or microduck_rl clone not found",
            remediation=(
                "run `microduck-cli env doctor` to see what is missing, or set "
                "MICRODUCK_CLONE / DUCK_SIM_RL (or MICRODUCK_RL_CLONE), or clone them "
                f"beside this repo — see {_UPSTREAM_SIM_DOC}"
            ),
        )

    launcher = _upstream_launcher_path(microduck_clone)
    if getattr(args, "upstream_launcher", False) and os.path.isfile(launcher):
        result = _run_upstream_launcher(
            launcher=launcher,
            microduck_clone=microduck_clone,
            rl_clone=rl_clone,
            mode=mode,
            ducks=args.ducks,
            port=args.port,
            scene=args.scene,
            state_dir=state_dir,
        )
        if result.returncode != 0:
            raise CliError(
                code=EXIT_ENV_ERROR,
                message=f"{launcher} up exited {result.returncode}",
                remediation=(
                    f"run `{launcher} up` by hand in {microduck_clone} and read its output"
                ),
            )
        emit_result(
            (
                {"launcher": launcher, "mode": mode, "state_dir": state_dir}
                if json_mode
                else f"{launcher} up: ok (state dir {state_dir})"
            ),
            json_mode=json_mode,
        )
        return EXIT_SUCCESS

    stack = _make_stack(microduck_clone, rl_clone, state_dir)
    report = stack.up(
        mode=mode,
        ducks=args.ducks,
        port=args.port,
        scene=args.scene,
        headless=args.headless,
        skip_build=args.skip_build,
    )

    timeout = _SIM_HEALTH_TIMEOUT_S if mode == "sim" else _FAKE_HEALTH_TIMEOUT_S
    ducks_report: list[dict[str, object]] = []
    for process in report.processes:
        if not process.socket:
            continue
        emit_diagnostic(f"waiting for {process.name} to report healthy ({process.socket})...")
        healthy = _wait_for_healthy(process.socket, timeout)
        if not healthy:
            raise CliError(
                code=EXIT_ENV_ERROR,
                message=f"{process.name} did not report healthy within {timeout:g}s",
                remediation=f"read {process.log} for why it never came up",
            )
        ducks_report.append({"name": process.name, "socket": process.socket, "healthy": True})

    payload = {
        "mode": mode,
        "state_dir": state_dir,
        "ducks": ducks_report,
        "sockets": [d["socket"] for d in ducks_report],
        "healthy": True,
    }
    if json_mode:
        emit_result(payload, json_mode=True)
    else:
        lines = [f"microduck-cli env up: healthy ({mode})", f"state dir: {state_dir}"]
        for duck in ducks_report:
            lines.append(f"  {duck['name']}: {duck['socket']}")
        emit_result("\n".join(lines), json_mode=False)
    return EXIT_SUCCESS


# ---------------------------------------------------------------------------
# down
# ---------------------------------------------------------------------------


def cmd_env_down(args: argparse.Namespace) -> int:
    json_mode = bool(getattr(args, "json", False))
    state_dir = _resolve_state_dir(args.state)
    stack = _make_stack("", "", state_dir)
    results = stack.down()

    try:
        port = int(os.environ.get("DUCK_SIM_PORT") or _doctor.DEFAULT_BODY_PORT)
    except ValueError:
        port = _doctor.DEFAULT_BODY_PORT
    still_listening = _port_listening(port)

    payload = {
        "state_dir": state_dir,
        "stopped": [r.to_dict() for r in results],
        "body_port": port,
        "body_port_still_listening": still_listening,
    }
    if json_mode:
        emit_result(payload, json_mode=True)
    else:
        lines = [f"microduck-cli env down: state dir {state_dir}"]
        if not results:
            lines.append("  nothing tracked")
        for r in results:
            lines.append(f"  {r.name}: {r.outcome}" + (f" ({r.detail})" if r.detail else ""))
        if still_listening:
            lines.append(f"  warning: something is still listening on port {port}")
        emit_result("\n".join(lines), json_mode=False)
    return EXIT_SUCCESS


# ---------------------------------------------------------------------------
# status
# ---------------------------------------------------------------------------


def cmd_env_status(args: argparse.Namespace) -> int:
    json_mode = bool(getattr(args, "json", False))
    state_dir = _resolve_state_dir(args.state)
    stack = _make_stack("", "", state_dir)
    status = stack.status()

    socket_health = [
        {"socket": sock, "responding": _hello_probe(sock)} for sock in status["sockets"]
    ]
    payload = {**status, "socket_health": socket_health}

    if json_mode:
        emit_result(payload, json_mode=True)
    else:
        lines = [f"microduck-cli env status: state dir {state_dir}"]
        for proc in status["processes"]:
            state = "alive" if proc["alive"] else ("stale" if proc["stale"] else "not tracked")
            lines.append(f"  {proc['name']}: pid={proc['pid']} {state}")
        for entry in socket_health:
            reply = "responds to hello" if entry["responding"] else "no response"
            lines.append(f"  socket {entry['socket']}: {reply}")
        emit_result("\n".join(lines), json_mode=False)
    return EXIT_SUCCESS


# ---------------------------------------------------------------------------
# hosts
# ---------------------------------------------------------------------------


def cmd_env_hosts(args: argparse.Namespace) -> int:
    json_mode = bool(getattr(args, "json", False))
    info = _hosts.classify(_hosts.default_probe())
    payload = {
        "host_class": info.host_class,
        "display_name": info.display_name,
        "torch_source_applies": info.torch_source_applies,
        "remediation": info.remediation or "",
    }
    if json_mode:
        emit_result(payload, json_mode=True)
    else:
        lines = [
            f"microduck-cli env hosts: {info.display_name} ({info.host_class})",
            f"torch source applies: {info.torch_source_applies}",
        ]
        if info.remediation:
            lines.append(f"  hint: {info.remediation}")
        emit_result("\n".join(lines), json_mode=False)
    return EXIT_SUCCESS


# ---------------------------------------------------------------------------
# registration
# ---------------------------------------------------------------------------


def register(sub: argparse._SubParsersAction) -> None:
    p = sub.add_parser(
        "env",
        help="Environment bring-up and diagnosis (see 'microduck-cli env overview').",
    )
    p.add_argument("--json", action="store_true", help="Emit structured JSON.")
    p.set_defaults(func=_no_verb, json=False)
    # `p` is a _CliArgumentParser (the top-level subparsers were built with that
    # parser_class); propagate it so `env <verb>` parse errors route through the
    # structured error contract instead of argparse's default stderr/exit 2.
    noun_sub = p.add_subparsers(dest="env_command", parser_class=type(p))

    ov = noun_sub.add_parser("overview", help="Describe the env noun.")
    ov.add_argument(
        "target",
        nargs="?",
        help="Ignored — overview always describes this noun. Accepted so a stray "
        "path argument never hard-fails.",
    )
    ov.add_argument("--json", action="store_true", help="Emit structured JSON.")
    ov.set_defaults(func=cmd_env_overview)

    doc = noun_sub.add_parser(
        "doctor", help="Diagnose whether this box can run the sim/train lane."
    )
    doc.add_argument("--json", action="store_true", help="Emit structured JSON.")
    doc.set_defaults(func=cmd_env_doctor)

    up = noun_sub.add_parser(
        "up", help="Bring up the simulator (or fake) stack and wait for it to report healthy."
    )
    mode_group = up.add_mutually_exclusive_group()
    mode_group.add_argument(
        "--sim", dest="mode", action="store_const", const="sim", help="Use the MuJoCo simulator."
    )
    mode_group.add_argument(
        "--fake",
        dest="mode",
        action="store_const",
        const="fake",
        help="Use robotd --fake (no simulator; the default).",
    )
    up.add_argument("--ducks", type=int, default=1, help="How many ducks (--sim only).")
    up.add_argument(
        "--port", type=int, default=_doctor.DEFAULT_BODY_PORT, help="duck-body's base TCP port."
    )
    up.add_argument("--scene", default=None, help="A built-in scene name, or a path.")
    up.add_argument("--headless", action="store_true", help="Run duck-body without a viewer.")
    up.add_argument("--state", default=None, help="Override the state directory.")
    up.add_argument("--skip-build", action="store_true", help="Skip the cargo build step.")
    up.add_argument(
        "--upstream-launcher",
        action="store_true",
        help="Shell out to <clone>/scripts/duck-sim when it exists, instead of driving "
        "SimStack directly.",
    )
    up.add_argument("--json", action="store_true", help="Emit structured JSON.")
    up.set_defaults(mode="fake", func=cmd_env_up)

    down = noun_sub.add_parser("down", help="Stop the tracked simulator stack.")
    down.add_argument("--state", default=None, help="Override the state directory.")
    down.add_argument("--json", action="store_true", help="Emit structured JSON.")
    down.set_defaults(func=cmd_env_down)

    status = noun_sub.add_parser("status", help="Report the tracked simulator stack's status.")
    status.add_argument("--state", default=None, help="Override the state directory.")
    status.add_argument("--json", action="store_true", help="Emit structured JSON.")
    status.set_defaults(func=cmd_env_status)

    hosts_p = noun_sub.add_parser("hosts", help="Classify this host for the training lane.")
    hosts_p.add_argument("--json", action="store_true", help="Emit structured JSON.")
    hosts_p.set_defaults(func=cmd_env_hosts)
