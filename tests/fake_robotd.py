"""An in-process fake ``robotd`` for tests.

A unix-socket JSON-RPC 2.0 server that speaks NDJSON (one JSON object per line),
the way the real MicroDuck daemons do: a message carrying an ``id`` is a request
and gets exactly one reply; a message without one is a notification and gets no
reply at all — not even when its params are wrong. Server-to-client pushes
(``robot.state``, ``pad.report``, ``tof.frame``) are notifications too, so they
never carry an ``id``.

Every shape below is implemented from two sources, never copied from either:

* ``duck-ipc-proto/src/lib.rs`` at the commit pinned in ``docs/upstream-pins.md``
  (``0cd676d`` of ``pollen-robotics/microduck``) — param field names, transcribed
  here. Method names, ``API_VERSION``, ``JOINT_NAMES`` and the error codes come
  from ``microduck_cli.ipc.proto``, which transcribes that same file: the fake
  and the client under test must not be able to drift apart.
* A probe of the real ``robotd`` 0.10.0 built from that commit and run with
  ``--fake`` — the exact reply payloads, and the serde-style ``-32602`` messages.

The defaults deliberately mirror ``robotd --fake``: **no policy is configured**,
so ``robot.subscribe`` reports ``unavailable``, ``robot.do`` refuses every skill,
``robot.setMode`` refuses, and ``robot.health`` carries no battery or thermal
section. Use :meth:`FakeRobotd.set_state` to give the fake a robot that can do
more than a bare ``--fake`` daemon.

Why the fake exists: CI cannot build and run the real ``robotd --fake``, so this
is the CI surface for every client/behaviour test. The on-box run against the
real daemon is a separate, later step.

Usage::

    with FakeRobotd() as fake:
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.connect(fake.socket_path)
        ...
        assert [rec.method for rec in fake.call_log] == ["hello", "robot.move"]
"""

from __future__ import annotations

import json
import os
import shutil
import socket
import tempfile
import threading
import time
from dataclasses import dataclass, field
from itertools import count
from typing import Any, Callable, Iterable

from microduck_cli.ipc import proto

# --- the method table -------------------------------------------------------
#
# Method names, API_VERSION, JOINT_NAMES and the JSON-RPC error codes come from
# ``microduck_cli.ipc.proto`` — the real transcription of the pinned
# duck-ipc-proto commit — rather than from a second copy here. A fake that
# disagreed with the table the client indexes against would test nothing.
#
# Two things stay local on purpose:
#
# * :data:`PARAM_FIELDS` / :data:`REQUIRED_FIELDS` / :data:`INTEGER_FIELDS`.
#   proto.py transcribes constant *values*, not the shape of the Rust param
#   structs, so there is nothing upstream to import; these are transcribed from
#   the same ``lib.rs`` and from the probed daemon's serde error messages.
# * :data:`POLICY_METHODS`. ``robot.policies`` / ``robot.skills`` /
#   ``robot.loadPolicy`` / ``robot.reloadPolicies`` / ``robot.setSkill`` /
#   ``robot.removeSkill`` do not exist on the pinned commit at all (the probed
#   daemon answers METHOD_NOT_FOUND for all six), so proto.py deliberately
#   carries no constant for them — see its "Deviations" docstring. They stay
#   string literals here until a build that has them is pinned.

#: ``pub const API_VERSION: u32 = 16`` on the pinned commit, and what the probed
#: daemon answers. A client's own ``api_version`` is never refused — skew is
#: reported by the answer, not rejected at the door.
API_VERSION = proto.API_VERSION

#: The API version from which ``robot.policies`` / ``robot.skills`` /
#: ``robot.loadPolicy`` exist. They are METHOD_NOT_FOUND on the pinned build.
POLICY_API_VERSION = 18

DAEMON_VERSION = "0.10.0"

JOINT_NAMES: tuple[str, ...] = proto.JOINT_NAMES
assert len(JOINT_NAMES) == 15, "the pinned proto declares exactly 15 joints"

#: JSON-RPC 2.0 error codes, as the pinned proto's ``code`` module names them.
PARSE_ERROR = proto.PARSE_ERROR
INVALID_REQUEST = proto.INVALID_REQUEST
METHOD_NOT_FOUND = proto.METHOD_NOT_FOUND
INVALID_PARAMS = proto.INVALID_PARAMS
INTERNAL_ERROR = proto.INTERNAL_ERROR

#: Methods sent as notifications by a client (continuous intents: no id, no reply).
#: The fake never relies on this to decide whether to answer — the presence of an
#: ``id`` on the wire does — but it records the classification the proto documents.
CONTINUOUS_METHODS: frozenset[str] = proto.NOTIFICATION_METHODS

#: Methods a client sends as answered requests (discrete intents and questions).
DISCRETE_METHODS: frozenset[str] = frozenset(
    {
        proto.HELLO,
        proto.ROBOT_HEALTH,
        proto.ROBOT_SUBSCRIBE,
        proto.ROBOT_INIT,
        proto.ROBOT_ENABLE,
        proto.ROBOT_RELAX,
        proto.ROBOT_DO,
        proto.ROBOT_LOOK,
        proto.ROBOT_STOP,
        proto.ROBOT_SET_MODE,
        proto.ROBOT_MODE,
        proto.ROBOT_MODEL_API,
        proto.ROBOT_SESSION_ACTIVE,
        proto.ROBOT_SAFE_TO_RESTART,
        proto.PAD_INPUT,
        proto.TOF_STREAM,
    }
)

#: Requests that exist only from :data:`POLICY_API_VERSION` onward. Literals: the
#: pinned proto has no constant for any of them (see the block comment above).
#: ``robot.reloadPolicies`` (main v19), ``robot.setSkill`` / ``robot.removeSkill``
#: (main v22) are gated at the same ``POLICY_API_VERSION`` as the rest of the
#: family for simplicity — ``microduck_cli/cli/_commands/policy.py``'s own
#: ``POLICY_API_VERSION`` constant does the same. All three are served by
#: ``robotd``'s own socket per main's ``destination()`` table
#: (``Call::RobotSkills | Call::RobotSetSkill(_) | Call::RobotRemoveSkill(_) =>
#: (Robot, Slow)``), the same one every other method in this fake answers on.
POLICY_METHODS: frozenset[str] = frozenset(
    {
        "robot.policies",
        "robot.skills",
        "robot.loadPolicy",
        "robot.reloadPolicies",
        "robot.setSkill",
        "robot.removeSkill",
    }
)

#: Server -> client push method names. Never carry an id.
STATE_NOTIFICATION = proto.ROBOT_STATE
PAD_NOTIFICATION = proto.PAD_REPORT
TOF_NOTIFICATION = proto.TOF_FRAME

#: Param field names, exactly as the pinned proto's param structs declare them.
#: Every one of those structs is ``deny_unknown_fields``, which is where the
#: ``-32602 unknown field`` answers come from. Local by necessity: proto.py
#: transcribes constants, not param structs.
PARAM_FIELDS: dict[str, tuple[str, ...]] = {
    proto.HELLO: ("api_version",),
    proto.ROBOT_ENABLE: ("on", "toggle"),
    proto.ROBOT_DO: ("skill",),
    proto.ROBOT_LOOK: ("x", "y", "z", "neck_pitch"),
    proto.ROBOT_SET_MODE: ("mode",),
    proto.ROBOT_SUBSCRIBE: ("hz",),
    # Continuous intents. Listed for the record and for tests to build valid
    # payloads from; a notification is never answered, so nothing validates them.
    proto.ROBOT_MOVE: ("vx", "vy", "vyaw"),
    proto.ROBOT_HEAD: ("neck_pitch", "head_pitch", "head_yaw", "head_roll"),
    proto.ROBOT_POSE: ("z", "roll", "pitch", "active"),
    proto.ROBOT_MOUTH: ("open",),
    proto.ROBOT_SOUND: ("tag", "hold"),
    # Policy-channel methods (main v18/v22, not in the pinned proto — see the
    # POLICY_METHODS comment above). Transcribed from duck-ipc-proto/src/lib.rs's
    # LoadPolicyParams / SkillParams / SkillNameParams, all `deny_unknown_fields`.
    "robot.loadPolicy": ("slot", "path"),
    "robot.setSkill": (
        "name",
        "path",
        "duration",
        "command",
        "unwind",
        "unwind_s",
        "chain",
        "action_scale",
        "gain_ratio",
        "overridden",
    ),
    "robot.removeSkill": ("name",),
}

#: Params with no serde ``default``: absent means ``missing field``.
REQUIRED_FIELDS: dict[str, tuple[str, ...]] = {
    proto.HELLO: ("api_version",),
    proto.ROBOT_DO: ("skill",),
    # SkillNameParams has no container-level `#[serde(default)]`, unlike
    # LoadPolicyParams and SkillParams (both `#[serde(default, ...)]`, so every
    # field of theirs is optional on the wire) — `name` is genuinely required.
    "robot.removeSkill": ("name",),
}

#: Params typed as a Rust integer (``u32``). A JSON float is a *type* error there, not a
#: roundable number: the probed daemon answers ``-32602 invalid type: floating point
#: `50.0`, expected u32`` to ``robot.subscribe {"hz": 50.0}``. Recorded here because a
#: fake that quietly accepted a float would hide exactly that bug in a client.
INTEGER_FIELDS: dict[str, tuple[str, ...]] = {
    proto.HELLO: ("api_version",),
    proto.ROBOT_SUBSCRIBE: ("hz",),
}

#: The ``Skill`` enum's wire variants — a value outside this set is a serde
#: unknown-variant error, distinct from a skill with no policy behind it.
SKILLS: tuple[str, ...] = (
    "ground_pick",
    "kick_left",
    "kick_right",
    "sit_toggle",
    "roulade",
)

#: The ``SoundTag`` enum's wire variants.
SOUND_TAGS: tuple[str, ...] = (
    "alarm",
    "greet",
    "inquire",
    "peck",
    "chirp",
    "coo",
    "wheee",
)

MODES: tuple[str, ...] = ("walk", "roller")

_MAX_SOCKET_PATH = 100


@dataclass(frozen=True)
class CallRecord:
    """One message the fake received, in arrival order."""

    seq: int
    method: str
    params: Any
    kind: str  # 'request' | 'notification'
    id: Any = None

    @property
    def is_notification(self) -> bool:
        return self.kind == "notification"


@dataclass
class _Refusal:
    code: int
    message: str


@dataclass(frozen=True)
class _Invalid:
    """A handler's way of asking for an INVALID_PARAMS reply."""

    message: str


@dataclass
class _RobotState:
    """What the fake reports about itself, shaped by :meth:`FakeRobotd.set_state`.

    The defaults are a bare ``robotd --fake``: healthy, upright, not driving, no
    policy of any kind loaded, and no battery or thermal reading.
    """

    api_version: int = API_VERSION
    daemon_version: str = DAEMON_VERSION
    revision: str | None = None
    healthy: bool = True
    degraded: bool = False
    reason: str | None = None
    fallen: bool = False
    enabled: bool = False
    #: ``None`` means "not measured" — which is what ``--fake`` reports. A float
    #: in 0..1 adds the ``battery`` section to ``robot.health``.
    battery_frac: float | None = None
    #: ``None`` means "not measured"; adds ``motors`` to ``robot.health``.
    hottest_servo_c: float | None = None
    #: ``robot.health``'s ``control_loop.achieved_hz``. ``None`` until a window
    #: closes, which is what a freshly started daemon reports.
    achieved_hz: float | None = None
    #: ``robot.state``'s ``loop.hz``.
    loop_hz: float = 50.0
    ticks: int = 1000
    mode: str = "walk"
    policy: str = "held"
    gain: int | None = 200
    #: Configured policy slots. ``None`` is ``--fake``'s "no policy configured".
    walk_policy: str | None = None
    stand_policy: str | None = None
    unavailable: str | None = "no policy configured; holding the startup pose"
    remote_session: bool = False
    #: Which skills have a policy behind them. Empty on ``--fake``. Used by
    #: ``robot.do``, ``robot.subscribe``'s file fields and ``robot.policies``'
    #: skills list — set directly via :meth:`set_state`, not by ``robot.setSkill``.
    skills: tuple[str, ...] = ()
    joint_names: tuple[str, ...] = JOINT_NAMES
    imu_ready: bool = False
    tof_sensor: str | None = "VL53L8CX"
    pad_attached: bool = False
    #: The live ``[[policy.skill]]`` table ``robot.setSkill``/``robot.removeSkill``
    #: write to and ``robot.skills`` reads from — main's v22 skill channel. Keyed
    #: by name; each value is a ``SkillParams``-shaped dict. Distinct from
    #: :attr:`skills` above (that tuple is the API-16-era "which skills have a
    #: policy" list a test seeds directly; this table is what the newer wire
    #: methods actually mutate).
    skill_entries: dict[str, dict[str, Any]] = field(default_factory=dict)
    extra: dict[str, Any] = field(default_factory=dict)


class FakeRobotd:
    """A thread-based fake robotd listening on a short unix socket path.

    Every instance gets its own ``tempfile.mkdtemp`` directory, so any number of
    them can run concurrently under ``pytest -n auto``. All threads are daemon
    threads and :meth:`close` joins them with a bounded timeout, so a leaked fake
    can never hang the interpreter at exit.
    """

    def __init__(
        self,
        *,
        state_hz: float = 50.0,
        autostart: bool = True,
        sock_name: str = "r.sock",
    ) -> None:
        self._tmpdir = tempfile.mkdtemp(prefix="mdfake")
        self._socket_path = os.path.join(self._tmpdir, sock_name)
        if len(self._socket_path.encode()) >= _MAX_SOCKET_PATH:
            shutil.rmtree(self._tmpdir, ignore_errors=True)
            raise RuntimeError(f"socket path too long for AF_UNIX: {self._socket_path!r}")

        self.state = _RobotState()
        self.default_state_hz = state_hz

        self._lock = threading.RLock()
        self._seq = count(1)
        self._log: list[CallRecord] = []
        self._refusals: dict[str, _Refusal] = {}
        self._delay_s = 0.0
        self._wedged = threading.Event()
        self._closing = threading.Event()

        self._server: socket.socket | None = None
        self._threads: list[threading.Thread] = []
        self._conns: list[_Connection] = []

        if autostart:
            self.start()

    # -- lifecycle ----------------------------------------------------------

    @property
    def socket_path(self) -> str:
        """Path of the listening unix socket. Always shorter than 100 bytes."""
        return self._socket_path

    def start(self) -> "FakeRobotd":
        if self._server is not None:
            return self
        server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        server.bind(self._socket_path)
        server.listen(8)
        server.settimeout(0.05)
        self._server = server
        self._spawn(self._accept_loop, name="fake-robotd-accept")
        return self

    def close(self) -> None:
        """Stop serving and release every resource. Never blocks indefinitely."""
        self._closing.set()
        self._wedged.clear()
        server, self._server = self._server, None
        if server is not None:
            try:
                server.close()
            except OSError:  # pragma: no cover - defensive
                pass
        with self._lock:
            conns = list(self._conns)
            self._conns.clear()
        for conn in conns:
            conn.shutdown()
        for thread in list(self._threads):
            thread.join(timeout=2.0)
        self._threads.clear()
        shutil.rmtree(self._tmpdir, ignore_errors=True)

    def __enter__(self) -> "FakeRobotd":
        return self.start()

    def __exit__(self, *_exc: object) -> None:
        self.close()

    def _spawn(self, target: Callable[[], None], *, name: str) -> threading.Thread:
        thread = threading.Thread(target=target, name=name, daemon=True)
        with self._lock:
            self._threads.append(thread)
        thread.start()
        return thread

    # -- test controls ------------------------------------------------------

    @property
    def call_log(self) -> list[CallRecord]:
        """Every message received, oldest first. A snapshot, safe to iterate."""
        with self._lock:
            return list(self._log)

    def methods_called(self) -> list[str]:
        return [rec.method for rec in self.call_log]

    def clear_log(self) -> None:
        with self._lock:
            self._log.clear()

    def refuse(self, method: str, code: int = INTERNAL_ERROR, message: str = "refused") -> None:
        """Make ``method`` answer a JSON-RPC error until :meth:`allow` clears it."""
        with self._lock:
            self._refusals[method] = _Refusal(code=code, message=message)

    def allow(self, method: str | None = None) -> None:
        """Clear one refusal, or every refusal when ``method`` is None."""
        with self._lock:
            if method is None:
                self._refusals.clear()
            else:
                self._refusals.pop(method, None)

    def wedge(self) -> None:
        """Stop reading from the socket, the way a hung daemon does."""
        self._wedged.set()

    def unwedge(self) -> None:
        self._wedged.clear()

    @property
    def wedged(self) -> bool:
        return self._wedged.is_set()

    def delay(self, ms: float) -> None:
        """Delay every reply by ``ms`` milliseconds. ``0`` restores no delay."""
        with self._lock:
            self._delay_s = max(0.0, float(ms)) / 1000.0

    def set_state(self, **fields: Any) -> None:
        """Shape what health / state / policies report.

        Accepts any field of the internal state record: ``fallen``, ``skills``,
        ``enabled``, ``healthy``, ``battery_frac`` (``None`` = not measured, as on
        ``--fake``), ``api_version`` (raise it to :data:`POLICY_API_VERSION` to
        turn the policy methods on), ``joint_names`` (so a joint-table mismatch
        can be simulated), ``walk_policy``, ``stand_policy``, ``unavailable``,
        ``loop_hz``, ``achieved_hz``, ``mode``, ``policy``, ``remote_session``,
        ``reason``, ``hottest_servo_c``, ``tof_sensor``, ``pad_attached``.
        """
        with self._lock:
            for key, value in fields.items():
                if not hasattr(self.state, key):
                    raise AttributeError(f"unknown fake state field: {key!r}")
                if key in ("skills", "joint_names") and isinstance(value, Iterable):
                    value = tuple(value)
                setattr(self.state, key, value)

    # -- pushed streams -----------------------------------------------------

    def feed_pad_report(self, payload: dict[str, Any]) -> int:
        """Push one ``pad.report`` notification to every ``pad.input`` subscriber."""
        return self._push(PAD_NOTIFICATION, payload, "pad")

    def feed_tof_frame(self, payload: dict[str, Any]) -> int:
        """Push one ``tof.frame`` notification to every ``tof.stream`` subscriber."""
        return self._push(TOF_NOTIFICATION, payload, "tof")

    def feed_state(self, payload: dict[str, Any] | None = None) -> int:
        """Push one ``robot.state`` notification outside the timed stream."""
        return self._push(STATE_NOTIFICATION, payload or self._state_frame(), "state")

    def _push(self, method: str, payload: Any, channel: str) -> int:
        message = {"jsonrpc": "2.0", "method": method, "params": payload}
        sent = 0
        with self._lock:
            conns = list(self._conns)
        for conn in conns:
            if conn.subscribed(channel):
                if conn.send(message):
                    sent += 1
        return sent

    # -- serving ------------------------------------------------------------

    def _accept_loop(self) -> None:
        while not self._closing.is_set():
            server = self._server
            if server is None:
                return
            try:
                raw, _ = server.accept()
            except socket.timeout:
                continue
            except OSError:
                return
            conn = _Connection(raw)
            with self._lock:
                self._conns.append(conn)
            self._spawn(lambda c=conn: self._serve(c), name="fake-robotd-conn")

    def _serve(self, conn: _Connection) -> None:
        buffer = b""
        try:
            while not self._closing.is_set():
                if self._wedged.is_set():
                    # A wedged daemon does not drain its socket; the client's
                    # writes back up and no reply ever comes.
                    time.sleep(0.005)
                    continue
                try:
                    chunk = conn.recv()
                except socket.timeout:
                    continue
                except OSError:
                    return
                if not chunk:
                    return
                # A recv already in flight when wedge() fired must not be
                # processed either, or wedging would be a race with the client.
                while self._wedged.is_set() and not self._closing.is_set():
                    time.sleep(0.005)
                if self._closing.is_set():
                    return
                buffer += chunk
                while b"\n" in buffer:
                    line, buffer = buffer.split(b"\n", 1)
                    if line.strip():
                        self._handle_line(conn, line)
        finally:
            conn.shutdown()

    def _handle_line(self, conn: _Connection, line: bytes) -> None:
        try:
            message = json.loads(line)
        except json.JSONDecodeError:
            self._error(conn, None, PARSE_ERROR, "parse error")
            return
        if not isinstance(message, dict) or "method" not in message:
            self._error(conn, None, INVALID_REQUEST, "invalid request")
            return

        method = message["method"]
        params = message.get("params")
        has_id = "id" in message and message["id"] is not None
        record = CallRecord(
            seq=next(self._seq),
            method=method,
            params=params,
            kind="request" if has_id else "notification",
            id=message.get("id") if has_id else None,
        )
        with self._lock:
            self._log.append(record)

        if not has_id:
            # A notification is never answered — not even an unknown one, and not
            # even one whose params are malformed. That is the JSON-RPC 2.0 rule,
            # and the probed daemon sent nothing back for any of them.
            self._apply_notification(method, params)
            return

        with self._lock:
            refusal = self._refusals.get(method)
        if refusal is not None:
            self._error(conn, record.id, refusal.code, refusal.message)
            return

        handler = self._handlers().get(method)
        if handler is None:
            self._error(conn, record.id, METHOD_NOT_FOUND, f'unknown method "{method}"')
            return

        payload = params if isinstance(params, dict) else {}
        complaint = _validate(method, params if isinstance(params, dict) else None)
        if complaint is not None:
            self._error(conn, record.id, INVALID_PARAMS, complaint)
            return

        result = handler(conn, payload)
        if isinstance(result, _Invalid):
            self._error(conn, record.id, INVALID_PARAMS, result.message)
            return
        self._reply(conn, {"jsonrpc": "2.0", "id": record.id, "result": result})

    def _reply(self, conn: _Connection, message: dict[str, Any]) -> None:
        with self._lock:
            delay = self._delay_s
        if delay:
            time.sleep(delay)
        conn.send(message)

    def _error(self, conn: _Connection, request_id: Any, code: int, message: str) -> None:
        self._reply(conn, {"jsonrpc": "2.0", "id": request_id, "error": _err(code, message)})

    def _apply_notification(self, method: str, params: Any) -> None:
        """Fold a continuous intent into the reported state, minimally."""
        if not isinstance(params, dict):
            return
        if method == proto.ROBOT_POSE:
            with self._lock:
                self.state.extra["pose"] = params
        elif method == proto.ROBOT_MOUTH:
            with self._lock:
                self.state.extra["mouth"] = params.get("open", 0.0)

    # -- handlers -----------------------------------------------------------

    def _handlers(self) -> dict[str, Callable[[_Connection, dict[str, Any]], Any]]:
        handlers: dict[str, Callable[[_Connection, dict[str, Any]], Any]] = {
            proto.HELLO: self._h_hello,
            proto.ROBOT_HEALTH: self._h_health,
            proto.ROBOT_SUBSCRIBE: self._h_subscribe,
            proto.ROBOT_INIT: self._h_accepted,
            proto.ROBOT_ENABLE: self._h_enable,
            proto.ROBOT_RELAX: self._h_relax,
            proto.ROBOT_DO: self._h_do,
            proto.ROBOT_LOOK: self._h_look,
            proto.ROBOT_STOP: self._h_accepted,
            proto.ROBOT_SET_MODE: self._h_set_mode,
            proto.ROBOT_MODE: self._h_mode,
            proto.ROBOT_MODEL_API: self._h_model_api,
            proto.ROBOT_SESSION_ACTIVE: self._h_session_active,
            proto.ROBOT_SAFE_TO_RESTART: self._h_safe_to_restart,
            proto.PAD_INPUT: self._h_pad_input,
            proto.TOF_STREAM: self._h_tof_stream,
        }
        # robot.policies / robot.loadPolicy / robot.reloadPolicies / robot.skills /
        # robot.setSkill / robot.removeSkill do not exist on the pinned build —
        # the probed daemon answers METHOD_NOT_FOUND for all six, and ipc/proto.py
        # carries no constant for them for that reason. They arrive on a newer
        # main (v18 for the first three, v22 for the skill trio), the fake gates
        # them all on the one reported API version so a test can cover both
        # daemons, keying them by literal until a build that defines them is
        # pinned (docs/upstream-pins.md).
        with self._lock:
            api_version = self.state.api_version
        if api_version >= POLICY_API_VERSION:
            handlers["robot.policies"] = self._h_policies
            handlers["robot.skills"] = self._h_skills
            handlers["robot.loadPolicy"] = self._h_load_policy
            handlers["robot.reloadPolicies"] = self._h_reload_policies
            handlers["robot.setSkill"] = self._h_set_skill
            handlers["robot.removeSkill"] = self._h_remove_skill
        return handlers

    def _h_hello(self, _conn: _Connection, _params: dict[str, Any]) -> dict[str, Any]:
        # A client's own api_version is recorded in the call log and never
        # refused: skew is the answer's business, not the door's.
        with self._lock:
            state = self.state
            return {
                "api_version": state.api_version,
                "daemon_version": state.daemon_version,
                "revision": state.revision,
            }

    def _h_health(self, _conn: _Connection, _params: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            state = self.state
            answer: dict[str, Any] = {
                "healthy": state.healthy,
                "control_loop": {
                    "target_hz": 50.0,
                    "achieved_hz": state.achieved_hz,
                    "ticks": state.ticks,
                    "missed": 0,
                    "last_tick_age_ms": 4,
                },
                "bus": {"consecutive_errors": 0, "startup_failures": 0},
                "imu": {
                    "ready": state.imu_ready,
                    "stale_blocks": 0,
                    "consecutive_stale_blocks": 0,
                },
            }
            # Measurements are reported only when measured. Absent is "not known
            # yet" — never zero, which would render as a flat pack.
            if state.degraded:
                answer["degraded"] = True
            if state.reason is not None:
                answer["reason"] = state.reason
            if state.battery_frac is not None:
                answer["battery"] = {
                    "volts": round(6.0 + 2.4 * state.battery_frac, 3),
                    "percent": round(100.0 * state.battery_frac, 1),
                }
            if state.hottest_servo_c is not None:
                answer["motors"] = {"hottest_c": state.hottest_servo_c}
            return answer

    def _h_subscribe(self, conn: _Connection, params: dict[str, Any]) -> dict[str, Any]:
        hz = params.get("hz") or self.default_state_hz
        conn.subscribe("state")
        self._start_state_stream(conn, float(hz))
        with self._lock:
            state = self.state
            answer: dict[str, Any] = {"accepted": True}
            if state.walk_policy is not None:
                answer["walk"] = state.walk_policy
            if state.stand_policy is not None:
                answer["stand"] = state.stand_policy
            if state.unavailable is not None:
                answer["unavailable"] = state.unavailable
            for skill in state.skills:
                answer[skill] = f"{skill}.onnx"
            return answer

    def _h_accepted(self, _conn: _Connection, _params: dict[str, Any]) -> dict[str, Any]:
        return {"accepted": True}

    def _h_enable(self, _conn: _Connection, params: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            if self.state.fallen:
                return {"accepted": False, "reason": "the robot has fallen"}
            on = not self.state.enabled if params.get("toggle") else bool(params.get("on", True))
            self.state.enabled = on
            self.state.policy = "walk" if on else "held"
        if on:
            return {"accepted": True, "reason": "enabled — driving"}
        return {"accepted": True, "reason": "disabled — holding the home pose"}

    def _h_relax(self, _conn: _Connection, _params: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            self.state.enabled = False
            self.state.policy = "held"
        return {"accepted": True}

    def _h_do(self, _conn: _Connection, params: dict[str, Any]) -> Any:
        skill = params.get("skill")
        if skill not in SKILLS:
            return _Invalid(f"unknown variant `{skill}`, {_expected(SKILLS)}")
        with self._lock:
            state = self.state
            if state.fallen:
                return {"accepted": False, "reason": "the robot has fallen"}
            if skill not in state.skills:
                return {"accepted": False, "reason": "no policy configured for that skill"}
            if not state.enabled:
                return {"accepted": False, "reason": "the robot is not driving"}
        return {"accepted": True}

    def _h_look(self, _conn: _Connection, params: dict[str, Any]) -> dict[str, Any]:
        return {
            "head": {
                "neck_pitch": float(params.get("neck_pitch", 0.0)),
                "head_pitch": 0.0,
                "head_yaw": 0.0,
                "head_roll": 0.0,
            }
        }

    def _h_set_mode(self, _conn: _Connection, params: dict[str, Any]) -> dict[str, Any]:
        mode = params.get("mode", "")
        with self._lock:
            state = self.state
            if state.walk_policy is None and state.stand_policy is None:
                return {
                    "accepted": False,
                    "reason": "no policy on this robot, so there is nothing to switch between",
                }
            if mode not in MODES:
                return {"accepted": False, "reason": f"known modes: {', '.join(MODES)}"}
            state.mode = mode
        return {"accepted": True}

    def _h_mode(self, _conn: _Connection, _params: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            return {"mode": self.state.mode}

    def _h_model_api(self, _conn: _Connection, _params: dict[str, Any]) -> dict[str, Any]:
        return {"model_api": 1}

    def _h_session_active(self, _conn: _Connection, _params: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            return {"active": self.state.remote_session}

    def _h_safe_to_restart(self, _conn: _Connection, _params: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            return {"safe": not self.state.enabled}

    def _h_pad_input(self, conn: _Connection, _params: dict[str, Any]) -> dict[str, Any]:
        conn.subscribe("pad")
        with self._lock:
            attached = self.state.pad_attached
        return {"accepted": True, "attached": attached}

    def _h_tof_stream(self, conn: _Connection, _params: dict[str, Any]) -> dict[str, Any]:
        conn.subscribe("tof")
        with self._lock:
            sensor = self.state.tof_sensor
        answer: dict[str, Any] = {"accepted": True, "rows": 8, "cols": 8, "hz": 15}
        if sensor is None:
            answer["unavailable"] = "no ToF sensor fitted"
        else:
            answer["sensor"] = sensor
        return answer

    # -- policy methods: only from POLICY_API_VERSION on ---------------------

    def _h_policies(self, _conn: _Connection, _params: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            state = self.state
            return {
                "slots": {
                    "walk": state.walk_policy,
                    "stand": state.stand_policy,
                    "unavailable": state.unavailable,
                },
                "skills": [{"name": s, "file": f"{s}.onnx"} for s in state.skills],
            }

    def _h_skills(self, _conn: _Connection, _params: dict[str, Any]) -> dict[str, Any]:
        # main's SkillsResult{skills, built_in} — skills is the live
        # [[policy.skill]] table robot.setSkill/robot.removeSkill write to, not
        # the fixed Skill enum. `built_in` is what robot.do drives itself.
        with self._lock:
            entries = [dict(entry) for entry in self.state.skill_entries.values()]
        return {"skills": entries, "built_in": list(SKILLS)}

    def _h_load_policy(self, _conn: _Connection, params: dict[str, Any]) -> dict[str, Any]:
        # LoadPolicyParams {slot: Option<String>, path: Option<String>} — see the
        # module docstring's table: (Some, Some) writes a slot, (Some, None) drops
        # one slot's override, (None, None) drops every override, (None, Some) is
        # refused.
        slot = params.get("slot")
        path = params.get("path")
        if slot is None:
            if path is not None:
                return {
                    "accepted": False,
                    "reason": "no such thing as loading one file into every slot",
                }
            with self._lock:
                self.state.walk_policy = None
                self.state.stand_policy = None
            return {"accepted": True}
        if slot not in ("walk", "stand"):
            return {"accepted": False, "reason": "known slots: walk, stand"}
        with self._lock:
            setattr(self.state, f"{slot}_policy", path)
            if path is not None:
                self.state.unavailable = None
        return {"accepted": True}

    def _h_reload_policies(self, _conn: _Connection, _params: dict[str, Any]) -> dict[str, Any]:
        # Call::RobotReloadPolicies takes no params — re-reads every slot from
        # disk. Nothing in the fake's state models a config file to re-read, so
        # this is a no-op that just accepts.
        return {"accepted": True}

    def _h_set_skill(self, _conn: _Connection, params: dict[str, Any]) -> dict[str, Any]:
        # SkillParams — container-level `#[serde(default)]`, so every field but
        # `name` is optional; an existing entry supplies whatever is left out
        # (the doc comment: "the same shape read and written").
        name = params.get("name") or ""
        if not name:
            return {"accepted": False, "reason": "no skill name given"}
        with self._lock:
            existing = self.state.skill_entries.get(name, {})
            entry = dict(existing)
            entry["name"] = name
            for field_name in (
                "path",
                "duration",
                "command",
                "unwind",
                "unwind_s",
                "chain",
                "action_scale",
                "gain_ratio",
            ):
                if field_name in params:
                    entry[field_name] = params[field_name]
            entry["overridden"] = True
            self.state.skill_entries[name] = entry
        return {"accepted": True}

    def _h_remove_skill(self, _conn: _Connection, params: dict[str, Any]) -> dict[str, Any]:
        name = params.get("name") or ""
        with self._lock:
            removed = self.state.skill_entries.pop(name, None) is not None
        return {"accepted": True, "removed": removed}

    # -- state stream -------------------------------------------------------

    def _start_state_stream(self, conn: _Connection, hz: float) -> None:
        period = 1.0 / hz if hz > 0 else 0.02

        def pump() -> None:
            while not self._closing.is_set() and conn.subscribed("state"):
                time.sleep(period)
                if self._wedged.is_set():
                    continue
                if not conn.send(
                    {
                        "jsonrpc": "2.0",
                        "method": STATE_NOTIFICATION,
                        "params": self._state_frame(),
                    }
                ):
                    return

        self._spawn(pump, name="fake-robotd-state")

    def _state_frame(self) -> dict[str, Any]:
        """One ``robot.state`` frame, keyed exactly as the probed daemon's."""
        with self._lock:
            state = self.state
            joints = [0.0] * len(state.joint_names)
            return {
                "head": [0.0, 0.0, 0.0, 0.0],
                "joints": joints,
                "loop": {"hz": state.loop_hz, "missed": 0},
                "move": {"applied": [0.0, 0.0, 0.0], "requested": [0.0, 0.0, 0.0]},
                "odom": {"position": [0.0, 0.0, 0.0], "yaw": 0.0},
                "policy": state.policy,
                "safety": {
                    "fallen": state.fallen,
                    "gain": state.gain,
                    "gravity": [0.0, 0.0, 1.0 if state.fallen else -1.0],
                    "limp": not state.enabled,
                },
                "t": round(time.monotonic() % 1e6, 4),
                "targets": list(joints),
            }


class _Connection:
    """One accepted client connection, with its own write lock and subscriptions."""

    def __init__(self, raw: socket.socket) -> None:
        raw.settimeout(0.05)
        self._raw = raw
        self._write_lock = threading.Lock()
        self._subs: set[str] = set()
        self._closed = threading.Event()

    def recv(self, size: int = 65536) -> bytes:
        return self._raw.recv(size)

    def send(self, message: dict[str, Any]) -> bool:
        if self._closed.is_set():
            return False
        payload = (json.dumps(message, separators=(",", ":")) + "\n").encode()
        with self._write_lock:
            try:
                self._raw.sendall(payload)
                return True
            except OSError:
                self._closed.set()
                return False

    def subscribe(self, channel: str) -> None:
        self._subs.add(channel)

    def subscribed(self, channel: str) -> bool:
        return channel in self._subs and not self._closed.is_set()

    def shutdown(self) -> None:
        self._closed.set()
        self._subs.clear()
        try:
            self._raw.close()
        except OSError:  # pragma: no cover - defensive
            pass


def _expected(fields: Iterable[str]) -> str:
    """Render serde's "expected ..." clause for a field or variant list."""
    quoted = [f"`{name}`" for name in fields]
    if not quoted:
        return "there are no fields"
    if len(quoted) == 1:
        return f"expected {quoted[0]}"
    if len(quoted) == 2:
        return f"expected {quoted[0]} or {quoted[1]}"
    return "expected one of " + ", ".join(quoted)


def _validate(method: str, params: dict[str, Any] | None) -> str | None:
    """Return a serde-style INVALID_PARAMS message, or None when the params fit.

    The pinned proto's param structs are ``deny_unknown_fields``, and the fields
    without a serde ``default`` are genuinely required — which is why an unknown
    key and a missing key are two different messages on the wire.
    """
    known = PARAM_FIELDS.get(method)
    supplied = params or {}
    if known is not None:
        for key in supplied:
            if key not in known:
                return f"unknown field `{key}`, {_expected(known)}"
    for key in REQUIRED_FIELDS.get(method, ()):
        if key not in supplied:
            return f"missing field `{key}`"
    for key in INTEGER_FIELDS.get(method, ()):
        value = supplied.get(key)
        if isinstance(value, float):
            return f"invalid type: floating point `{value}`, expected u32"
    return None


def _err(code: int, message: str) -> dict[str, Any]:
    return {"code": code, "message": message}


__all__ = [
    "API_VERSION",
    "CONTINUOUS_METHODS",
    "DAEMON_VERSION",
    "DISCRETE_METHODS",
    "INTEGER_FIELDS",
    "INTERNAL_ERROR",
    "INVALID_PARAMS",
    "INVALID_REQUEST",
    "JOINT_NAMES",
    "METHOD_NOT_FOUND",
    "MODES",
    "PAD_NOTIFICATION",
    "PARAM_FIELDS",
    "PARSE_ERROR",
    "POLICY_API_VERSION",
    "POLICY_METHODS",
    "REQUIRED_FIELDS",
    "SKILLS",
    "SOUND_TAGS",
    "STATE_NOTIFICATION",
    "TOF_NOTIFICATION",
    "CallRecord",
    "FakeRobotd",
]
