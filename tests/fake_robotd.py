"""An in-process fake ``robotd`` for tests.

A unix-socket JSON-RPC 2.0 server that speaks NDJSON (one JSON object per line),
the way the real MicroDuck daemons do: a message carrying an ``id`` is a request
and gets exactly one reply; a message without one is a notification and gets no
reply at all. Server-to-client pushes (``robot.state``, ``pad.report``,
``tof.frame``) are notifications too, so they never carry an ``id``.

The behaviour here is implemented from the wire contract of
``duck-ipc-proto/src/lib.rs`` at the commit pinned in ``docs/upstream-pins.md``
(``0cd676d`` of ``pollen-robotics/microduck``). Nothing upstream is copied — the
shapes below are a Python re-implementation of the documented protocol.

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

# --- private method table ---------------------------------------------------
#
# Deliberately local to the fake. A sibling task owns ``microduck_cli/ipc/proto.py``
# as the real transcription of the pinned duck-ipc-proto commit; this table is only
# what the fake needs to answer, transcribed from the same source.
#
# TODO(t10): reconcile these names, API_VERSION and JOINT_NAMES against
# ``microduck_cli.ipc.proto`` and import from there instead of duplicating them.

API_VERSION = 16

JOINT_NAMES: tuple[str, ...] = (
    "left_hip_yaw",
    "left_hip_roll",
    "left_hip_pitch",
    "left_knee",
    "left_ankle",
    "neck_pitch",
    "head_pitch",
    "head_yaw",
    "head_roll",
    "mouth",
    "right_hip_yaw",
    "right_hip_roll",
    "right_hip_pitch",
    "right_knee",
    "right_ankle",
)
assert len(JOINT_NAMES) == 15, "the pinned proto declares exactly 15 joints"

ROBOT_MODEL = "microduck"

#: JSON-RPC 2.0 error codes, as the pinned proto's ``code`` module names them.
PARSE_ERROR = -32700
INVALID_REQUEST = -32600
METHOD_NOT_FOUND = -32601
INVALID_PARAMS = -32602
INTERNAL_ERROR = -32603

#: Methods sent as notifications by a client (continuous intents: no id, no reply).
#: The fake never relies on this to decide whether to answer — the presence of an
#: ``id`` on the wire does — but it records the classification the proto documents.
CONTINUOUS_METHODS: frozenset[str] = frozenset(
    {"robot.move", "robot.head", "robot.pose", "robot.mouth", "robot.sound"}
)

#: Methods a client sends as answered requests (discrete intents and questions).
DISCRETE_METHODS: frozenset[str] = frozenset(
    {
        "hello",
        "robot.health",
        "robot.subscribe",
        "robot.policies",
        "robot.init",
        "robot.enable",
        "robot.relax",
        "robot.do",
        "robot.look",
        "robot.stop",
        "robot.setMode",
        "robot.mode",
        "robot.loadPolicy",
        "robot.modelApi",
        "robot.remoteSessionActive",
        "robot.safeToRestart",
        "pad.input",
        "tof.stream",
    }
)

#: Server -> client push method names. Never carry an id.
STATE_NOTIFICATION = "robot.state"
PAD_NOTIFICATION = "pad.report"
TOF_NOTIFICATION = "tof.frame"

#: The skill names ``robot.do`` accepts.
SKILLS: tuple[str, ...] = (
    "ground_pick",
    "kick_left",
    "kick_right",
    "sit_toggle",
    "roulade",
)

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


@dataclass
class _RobotState:
    """What the fake reports about itself, shaped by :meth:`FakeRobotd.set_state`."""

    api_version: int = API_VERSION
    healthy: bool = True
    fallen: bool = False
    enabled: bool = False
    battery_frac: float = 0.87
    loop_hz: float = 50.0
    mode: str = "walk"
    policy: str = "held"
    walk_policy: str = "walk.onnx"
    stand_policy: str = "stand.onnx"
    remote_session: bool = False
    skills: tuple[str, ...] = SKILLS
    joint_names: tuple[str, ...] = JOINT_NAMES
    daemon_version: str = "0.0.0-fake"
    tof_sensor: str | None = "VL53L8CX"
    pad_attached: bool = False
    unavailable: str | None = None
    reason: str | None = None
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
        ``enabled``, ``healthy``, ``battery_frac``, ``api_version``,
        ``joint_names`` (so a joint-table mismatch can be simulated), ``loop_hz``,
        ``mode``, ``policy``, ``remote_session``, ``reason``, ``unavailable``.
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
            self._reply(conn, {"jsonrpc": "2.0", "id": None, "error": _err(PARSE_ERROR, "parse")})
            return
        if not isinstance(message, dict) or "method" not in message:
            self._reply(
                conn,
                {"jsonrpc": "2.0", "id": None, "error": _err(INVALID_REQUEST, "invalid request")},
            )
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
            # A notification is never answered — not even an unknown one, which
            # is the JSON-RPC 2.0 rule and what the real daemons do.
            self._apply_notification(method, params)
            return

        with self._lock:
            refusal = self._refusals.get(method)
        if refusal is not None:
            self._reply(
                conn,
                {
                    "jsonrpc": "2.0",
                    "id": record.id,
                    "error": _err(refusal.code, refusal.message),
                },
            )
            return

        handler = self._handlers().get(method)
        if handler is None:
            self._reply(
                conn,
                {
                    "jsonrpc": "2.0",
                    "id": record.id,
                    "error": _err(METHOD_NOT_FOUND, f"method not found: {method}"),
                },
            )
            return
        result = handler(conn, params if isinstance(params, dict) else {})
        self._reply(conn, {"jsonrpc": "2.0", "id": record.id, "result": result})

    def _reply(self, conn: _Connection, message: dict[str, Any]) -> None:
        with self._lock:
            delay = self._delay_s
        if delay:
            time.sleep(delay)
        conn.send(message)

    def _apply_notification(self, method: str, params: Any) -> None:
        """Fold a continuous intent into the reported state, minimally."""
        if method == "robot.pose" and isinstance(params, dict):
            with self._lock:
                self.state.extra["pose"] = params
        elif method == "robot.mouth" and isinstance(params, dict):
            with self._lock:
                self.state.extra["mouth"] = params.get("open", 0.0)

    # -- handlers -----------------------------------------------------------

    def _handlers(self) -> dict[str, Callable[[_Connection, dict[str, Any]], Any]]:
        return {
            "hello": self._h_hello,
            "robot.health": self._h_health,
            "robot.subscribe": self._h_subscribe,
            "robot.policies": self._h_policies,
            "robot.init": self._h_intent,
            "robot.enable": self._h_enable,
            "robot.relax": self._h_relax,
            "robot.do": self._h_do,
            "robot.look": self._h_look,
            "robot.stop": self._h_intent,
            "robot.setMode": self._h_set_mode,
            "robot.mode": self._h_mode,
            "robot.loadPolicy": self._h_load_policy,
            "robot.modelApi": self._h_model_api,
            "robot.remoteSessionActive": self._h_session_active,
            "robot.safeToRestart": self._h_safe_to_restart,
            "pad.input": self._h_pad_input,
            "tof.stream": self._h_tof_stream,
        }

    def _h_hello(self, _conn: _Connection, _params: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            state = self.state
            return {
                "api_version": state.api_version,
                "daemon_version": state.daemon_version,
                "revision": None,
                "model": ROBOT_MODEL,
                "joint_names": list(state.joint_names),
            }

    def _h_health(self, _conn: _Connection, _params: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            state = self.state
            answer: dict[str, Any] = {
                "healthy": state.healthy,
                "degraded": False,
                "battery": {
                    "volts": round(6.0 + 2.4 * state.battery_frac, 3),
                    "percent": round(100.0 * state.battery_frac, 1),
                },
                "control_loop": {
                    "target_hz": 50.0,
                    "achieved_hz": state.loop_hz,
                    "ticks": 1000,
                    "missed": 0,
                    "last_tick_age_ms": 4,
                },
                "bus": {"consecutive_errors": 0, "startup_failures": 0},
                "imu": {"ready": True},
            }
            if state.reason is not None:
                answer["reason"] = state.reason
            return answer

    def _h_subscribe(self, conn: _Connection, params: dict[str, Any]) -> dict[str, Any]:
        hz = params.get("hz") or self.default_state_hz
        conn.subscribe("state")
        self._start_state_stream(conn, float(hz))
        with self._lock:
            state = self.state
            answer: dict[str, Any] = {
                "accepted": True,
                "walk": state.walk_policy,
                "stand": state.stand_policy,
            }
            if state.unavailable is not None:
                answer["unavailable"] = state.unavailable
            for skill in state.skills:
                answer[skill] = f"{skill}.onnx"
            return answer

    def _h_policies(self, _conn: _Connection, _params: dict[str, Any]) -> dict[str, Any]:
        # NOTE: the pinned proto has no ``robot.policies`` method — the policy
        # slots and the loaded skill networks are only visible through
        # ``robot.subscribe``'s answer. The fake answers it because the CLI's
        # planned ``policies`` surface asks for it; TODO(t10) drop or keep it
        # deliberately once ipc/proto.py settles the real method name.
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

    def _h_intent(self, _conn: _Connection, _params: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            if self.state.fallen:
                return {"accepted": False, "reason": "the robot has fallen"}
        return {"accepted": True}

    def _h_enable(self, _conn: _Connection, params: dict[str, Any]) -> dict[str, Any]:
        on = bool(params.get("on", True))
        with self._lock:
            if on and self.state.fallen:
                return {"accepted": False, "reason": "the robot has fallen"}
            self.state.enabled = on
            self.state.policy = "walk" if on else "held"
        return {"accepted": True}

    def _h_relax(self, _conn: _Connection, _params: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            self.state.enabled = False
            self.state.policy = "held"
        return {"accepted": True}

    def _h_do(self, _conn: _Connection, params: dict[str, Any]) -> dict[str, Any]:
        skill = params.get("skill")
        with self._lock:
            known = self.state.skills
        if skill not in known:
            return {"accepted": False, "reason": f"unknown skill: {skill}"}
        return {"accepted": True}

    def _h_look(self, _conn: _Connection, params: dict[str, Any]) -> dict[str, Any]:
        neck = float(params.get("neck_pitch", 0.0))
        return {"head": {"neck_pitch": neck, "pitch": 0.0, "yaw": 0.0, "roll": 0.0}}

    def _h_set_mode(self, _conn: _Connection, params: dict[str, Any]) -> dict[str, Any]:
        mode = params.get("mode")
        if mode not in ("walk", "roller"):
            return {"accepted": False, "reason": "known modes: walk, roller"}
        with self._lock:
            self.state.mode = mode
        return {"accepted": True}

    def _h_mode(self, _conn: _Connection, _params: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            return {"mode": self.state.mode}

    def _h_load_policy(self, _conn: _Connection, params: dict[str, Any]) -> dict[str, Any]:
        # NOTE: also absent from the pinned proto — see ``_h_policies``. TODO(t10).
        slot = params.get("slot", "walk")
        name = params.get("policy") or params.get("file")
        if slot not in ("walk", "stand"):
            return {"accepted": False, "reason": "known slots: walk, stand"}
        if not name:
            return {"accepted": False, "reason": "no policy named"}
        with self._lock:
            setattr(self.state, f"{slot}_policy", name)
        return {"accepted": True}

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
        with self._lock:
            state = self.state
            joints = [0.0] * len(state.joint_names)
            return {
                "t": round(time.monotonic() % 1e6, 4),
                "move": {"requested": [0.0, 0.0, 0.0], "applied": [0.0, 0.0, 0.0]},
                "head": [0.0, 0.0, 0.0, 0.0],
                "policy": state.policy,
                "safety": {
                    "fallen": state.fallen,
                    "limp": not state.enabled,
                    "gravity": [0.0, 0.0, 9.81 if state.fallen else -9.81],
                },
                "loop": {"hz": state.loop_hz, "missed": 0},
                "joints": joints,
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


def _err(code: int, message: str) -> dict[str, Any]:
    return {"code": code, "message": message}


__all__ = [
    "API_VERSION",
    "CallRecord",
    "CONTINUOUS_METHODS",
    "DISCRETE_METHODS",
    "FakeRobotd",
    "INTERNAL_ERROR",
    "INVALID_PARAMS",
    "INVALID_REQUEST",
    "JOINT_NAMES",
    "METHOD_NOT_FOUND",
    "PAD_NOTIFICATION",
    "PARSE_ERROR",
    "ROBOT_MODEL",
    "SKILLS",
    "STATE_NOTIFICATION",
    "TOF_NOTIFICATION",
]
