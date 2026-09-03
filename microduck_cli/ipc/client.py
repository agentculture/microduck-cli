"""A threaded JSON-RPC 2.0 client for ``robotd`` over a unix socket.

The transport half of :mod:`microduck_cli.ipc`: :mod:`~microduck_cli.ipc.proto` says
*what* the wire looks like, this module *speaks* it. Stdlib only (``socket``,
``threading``, ``queue``, ``json``, ``logging``, ``dataclasses``) plus two first-party
leaves — the protocol table and :class:`~microduck_cli.behavior.sense.SenseProviders`.

Four contracts are load-bearing here; everything else is plumbing.

**The tick thread never blocks.** :meth:`RobotClient.notify` is a single
``put_nowait`` onto a bounded queue that a writer thread drains. A wedged daemon
therefore stalls the *writer*, never the caller: the queue fills, further notifies
return ``False``, and each one counts an ``ipc-queue-full`` drop. A control loop that
cannot afford to wait for a socket can call this at 50 Hz and know the worst case is a
lost frame, not a missed tick.

**A named drop, never silence.** Every message this client fails to send, fails to
correlate, or answers with an error increments a counter under a *named* reason
(:attr:`RobotClient.drops`) and emits exactly one line on the ``microduck.sense``
logger::

    [SENSE stage=ipc source=<socket> event=<reason>] <detail>

No handler is installed here — that belongs to whoever composes the process — so these
lines are inert until someone attaches a stderr handler. A layer whose drops are
invisible is indistinguishable from one that silently no-ops, which is the whole reason
the reasons are named.

**Errors are exceptions to a caller, drops to a tick.** A JSON-RPC error reply raises
:class:`RpcError` — a plain exception, never a :class:`~microduck_cli.cli._errors.CliError`
and never a traceback escaping into a tick. ``METHOD_NOT_FOUND`` is *additionally*
recorded, so :meth:`RobotClient.supports` answers the "does this daemon have that
method?" question without anyone having to catch anything. That is how a client on API
16 copes with methods (``robot.skills``, ``robot.policies``, ``robot.loadPolicy``) that
only exist on a newer daemon.

**Skew is reported, not refused.** ``hello`` sends our own
:data:`~microduck_cli.ipc.proto.API_VERSION` and accepts whatever version the daemon
answers with — the daemon does the same, per the probed 0.10.0 build — and the
difference is readable on :attr:`RobotClient.api_skew`. The *one* thing that is refused
is a joint-table disagreement: a daemon whose ``robot.state`` frames carry a different
number of joints than :data:`~microduck_cli.ipc.proto.JOINT_NAMES` means every index
into that vector is wrong, so :meth:`RobotClient.connect` raises ``CliError`` with exit
code 2 naming *both* counts.
"""

from __future__ import annotations

import json
import logging
import queue
import socket
import threading
import time
from collections import Counter
from dataclasses import dataclass, field
from typing import Any, Callable

from microduck_cli.behavior.sense import SenseProviders
from microduck_cli.cli._errors import EXIT_ENV_ERROR, CliError
from microduck_cli.ipc import proto

#: Every drop this module counts goes out on this logger, one line each. Handlers are
#: deliberately NOT installed here: the composition root owns where stderr goes.
LOGGER = logging.getLogger("microduck.sense")

# ── named drop reasons ────────────────────────────────────────────────────────────────

#: The write queue was full: the daemon (or the link) is slower than the caller.
DROP_QUEUE_FULL = "ipc-queue-full"
#: The link is down — never connected, dropped, or the daemon went away.
DROP_DOWN = "ipc-down"
#: The daemon answered -32601: it does not have that method. See :meth:`RobotClient.supports`.
DROP_METHOD_NOT_FOUND = "method-not-found"
#: A request went unanswered within its timeout.
DROP_TIMEOUT = "ipc-timeout"
#: A reply arrived for an id nobody is waiting on any more (usually a late answer to a
#: request that already timed out).
DROP_LATE_REPLY = "ipc-late-reply"
#: A line off the socket that is not a JSON-RPC message we can route.
DROP_MALFORMED = "ipc-malformed"
#: A ``robot.state`` frame whose joint vector disagrees with our table. Fatal at
#: connect time; counted (once per link) if it appears later.
DROP_JOINT_MISMATCH = "joint-table-mismatch"
#: A background poll (``robot.health``, ``robot.remoteSessionActive``) that failed.
#: Swallowed rather than raised — these are called from the tick path.
DROP_POLL_FAILED = "ipc-poll-failed"
#: ``robot.subscribe`` was refused, so the joint table could not be verified.
DROP_SUBSCRIBE_FAILED = "ipc-subscribe-failed"

# ── client-side error codes ───────────────────────────────────────────────────────────
#
# These never go on the wire. They live in the JSON-RPC "implementation-defined server
# error" band (-32000..-32099) so they cannot collide with a code the daemon sends, and
# they are distinct from proto's transcribed codes on purpose: a caller that sees one of
# these knows the failure was ours, not the daemon's.

CLIENT_TIMEOUT = -32001
CLIENT_LINK_DOWN = -32002
CLIENT_QUEUE_FULL = -32003

#: How long the writer waits for a reconnect nudge before looping. Purely a floor on
#: how often a down link is retried when nobody is calling: the control loop calling
#: :meth:`RobotClient.notify` is the real retry timer.
_RECONNECT_POLL_S = 0.2
#: How long the writer waits for work before re-checking the closing flag.
_DRAIN_POLL_S = 0.05
_RECV_SIZE = 65536


class RpcError(Exception):
    """A JSON-RPC failure, as an exception a caller can catch.

    Deliberately NOT a :class:`~microduck_cli.cli._errors.CliError`: an RPC that fails
    is a fact about the daemon, and only the CLI edge gets to decide whether that fact
    is worth an exit code. ``code`` is the daemon's own error code (see
    :mod:`microduck_cli.ipc.proto`) or one of the client-side codes above.
    """

    def __init__(self, code: int, message: str, *, method: str | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.method = method


class RpcTimeout(RpcError):
    """No reply arrived within the request's timeout. The message names the method."""

    def __init__(self, message: str, *, method: str | None = None) -> None:
        super().__init__(CLIENT_TIMEOUT, message, method=method)


class RpcLinkDown(RpcError):
    """The request was never sent: there is no live socket to the daemon."""

    def __init__(self, message: str, *, method: str | None = None) -> None:
        super().__init__(CLIENT_LINK_DOWN, message, method=method)


class RpcQueueFull(RpcError):
    """The request was never sent: the bounded write queue is full."""

    def __init__(self, message: str, *, method: str | None = None) -> None:
        super().__init__(CLIENT_QUEUE_FULL, message, method=method)


@dataclass(frozen=True)
class DaemonInfo:
    """What ``hello`` told us about the daemon. Fields are ``None`` before the handshake."""

    api_version: int | None = None
    daemon_version: str | None = None
    revision: str | None = None


#: Keys of a ``robot.subscribe`` result that are not skill entries. Everything else in
#: that object is ``<skill name>: <policy file>`` — which, on API 16, is the ONLY place
#: the daemon reports which skills have a policy behind them (``robot.skills`` answers
#: METHOD_NOT_FOUND on the pinned build).
_SUBSCRIBE_RESERVED = frozenset({"accepted", "walk", "stand", "unavailable"})


@dataclass(frozen=True)
class SubscribeResult:
    """The answer to ``robot.subscribe``, parsed.

    ``walk`` / ``stand`` name the loaded policy slots, ``unavailable`` is the daemon's
    own prose for why it cannot drive (``"no policy configured; holding the startup
    pose"`` on a bare ``robotd --fake``), and ``skills`` lists the skill names it
    advertised, with ``files`` mapping each to its policy file.
    """

    accepted: bool = False
    walk: str | None = None
    stand: str | None = None
    unavailable: str | None = None
    skills: tuple[str, ...] = ()
    files: dict[str, str] = field(default_factory=dict)
    raw: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_result(cls, result: Any) -> "SubscribeResult":
        """Parse a ``robot.subscribe`` result, tolerating anything unexpected."""
        if not isinstance(result, dict):
            return cls()
        files = {
            key: value
            for key, value in result.items()
            if key not in _SUBSCRIBE_RESERVED and isinstance(value, str)
        }
        return cls(
            accepted=bool(result.get("accepted", False)),
            walk=_as_str(result.get("walk")),
            stand=_as_str(result.get("stand")),
            unavailable=_as_str(result.get("unavailable")),
            skills=tuple(files),
            files=files,
            raw=dict(result),
        )


@dataclass
class _Slot:
    """The last value seen for one method, with the clock reading when it landed."""

    params: Any = None
    stamp: float | None = None


@dataclass
class _Pending:
    """One in-flight request, correlated by id."""

    method: str
    event: threading.Event = field(default_factory=threading.Event)
    result: Any = None
    error: tuple[int, str] | None = None
    callback: Callable[[Any, tuple[int, str] | None], None] | None = None


def _as_str(value: Any) -> str | None:
    return value if isinstance(value, str) else None


def _walk(payload: Any, path: tuple[str, ...]) -> Any:
    """Follow ``path`` through nested dicts, answering ``None`` at the first miss."""
    node = payload
    for key in path:
        if not isinstance(node, dict) or key not in node:
            return None
        node = node[key]
    return node


class RobotClient:
    """A JSON-RPC client for one ``robotd``-family unix socket.

    ``clock`` is a zero-arg callable returning monotonic seconds; every peek slot is
    stamped with it, so a caller reading :meth:`providers` and a caller building a
    :class:`~microduck_cli.behavior.sense.Sense` agree on one clock. It is required
    rather than defaulted so that no code path can accidentally mix ``time.monotonic``
    with an injected test clock.

    ``queue_depth`` bounds the writer queue: the number of unsent frames tolerated
    before :meth:`notify` starts dropping. ``request_timeout_s`` is the default wait for
    a correlated reply.
    """

    def __init__(
        self,
        socket_path: str,
        *,
        clock: Callable[[], float],
        queue_depth: int = 64,
        request_timeout_s: float = 2.0,
        state_hz: int = 50,
        connect_timeout_s: float = 2.0,
    ) -> None:
        self._socket_path = socket_path
        self._clock = clock
        self._queue_depth = queue_depth
        self._request_timeout_s = request_timeout_s
        # An integer on purpose: robot.subscribe's `hz` is a serde u32 upstream, and the
        # real daemon answers -32602 "invalid type: floating point `50.0`, expected u32"
        # for a JSON float. Found by probing robotd 0.10.0; the fake now refuses it too.
        self._state_hz = int(state_hz)
        self._connect_timeout_s = connect_timeout_s

        self._lock = threading.RLock()
        self._queue: queue.Queue[bytes] = queue.Queue(maxsize=queue_depth)
        self._pending: dict[int, _Pending] = {}
        self._slots: dict[str, _Slot] = {}
        self._drops: Counter[str] = Counter()
        self._unsupported: set[str] = set()

        self._sock: socket.socket | None = None
        self._gen = 0
        self._threads: list[threading.Thread] = []
        self._next_id = 0

        self._closing = threading.Event()
        self._link_up = threading.Event()
        self._reconnect = threading.Event()
        self._state_seen = threading.Event()

        self.daemon = DaemonInfo()
        self._subscribe_result: SubscribeResult | None = None
        self._subscribed_hz: int | None = None
        self._joint_count: int | None = None
        self._joints_verified = False
        self._mismatch_reported = False

    # -- properties ---------------------------------------------------------

    @property
    def socket_path(self) -> str:
        return self._socket_path

    @property
    def connected(self) -> bool:
        """True while a live socket exists. False from the moment the link drops."""
        return self._link_up.is_set()

    @property
    def drops(self) -> Counter[str]:
        """A snapshot of the named drop counters. Safe to read from any thread."""
        with self._lock:
            return Counter(self._drops)

    @property
    def api_skew(self) -> tuple[int, int] | None:
        """``(daemon, client)`` API versions when they differ, else ``None``.

        Skew is *reported*, never refused — the daemon accepts our version and we accept
        its. A caller that cares (a verb needing a method only newer daemons have) reads
        this and decides for itself.
        """
        daemon = self.daemon.api_version
        if daemon is None or daemon == proto.API_VERSION:
            return None
        return (daemon, proto.API_VERSION)

    @property
    def joints_verified(self) -> bool:
        """True once a ``robot.state`` frame confirmed the joint table matches ours."""
        return self._joints_verified

    # -- lifecycle ----------------------------------------------------------

    def connect(self, *, verify_joints: bool = True) -> "RobotClient":
        """Open the socket, start both threads, say ``hello``, check the joint table.

        Raises :class:`~microduck_cli.cli._errors.CliError` (exit 2) when the socket
        cannot be reached, when the handshake fails, or when the daemon's joint count
        disagrees with :data:`~microduck_cli.ipc.proto.JOINT_NAMES`. Every one of those
        leaves the client closed rather than half-open.

        ``verify_joints=False`` skips the brief verification subscribe — for a one-shot
        request (``robot.mode``, ``robot.health``) that has no use for a 50 Hz state
        stream and never indexes the joint vector.
        """
        if self._closing.is_set():
            raise CliError(
                EXIT_ENV_ERROR,
                "this RobotClient is closed; build a new one",
                remediation="Construct a fresh RobotClient rather than reusing a closed one.",
            )
        with self._lock:
            if self._sock is not None:
                return self
        self._open_socket()
        self._start_threads()
        self._handshake()
        if verify_joints:
            self._verify_joint_table()
        return self

    def flush(self, timeout_s: float = 1.0) -> bool:
        """Wait (real time, bounded) until every queued frame has left the queue.

        For the shutdown path only — the tick loop never waits on I/O. Returns
        True when the queue drained within ``timeout_s``; False when frames are
        still pending (a wedged or dead daemon), so the caller can name what it
        could not deliver instead of assuming it went out.
        """
        deadline = time.monotonic() + max(0.0, timeout_s)
        while not self._queue.empty():
            if time.monotonic() >= deadline:
                return False
            time.sleep(0.005)
        return True

    def close(self) -> None:
        """Shut the link down and join both threads. Idempotent; never hangs.

        The threads are daemon threads and the socket is shut down before the join, so a
        writer blocked in ``sendall`` against a wedged daemon is released immediately
        rather than waiting out a timeout.
        """
        if self._closing.is_set():
            return
        self._closing.set()
        self._link_up.clear()
        self._reconnect.set()
        self._shutdown_socket()
        self._fail_pending("the client is closing")
        for thread in list(self._threads):
            thread.join(timeout=2.0)
        self._threads.clear()

    def __enter__(self) -> "RobotClient":
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    def _open_socket(self) -> None:
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.settimeout(self._connect_timeout_s)
        try:
            sock.connect(self._socket_path)
        except OSError as exc:
            sock.close()
            raise CliError(
                EXIT_ENV_ERROR,
                f"cannot reach the robot daemon on {self._socket_path}: {exc}",
                remediation=(
                    "Check the daemon is running and the socket path is right "
                    f"(default {proto.SOCKET_ROBOT})."
                ),
            ) from exc
        # Blocking from here on. close() shuts the socket down, which releases a reader
        # parked in recv() and a writer parked in sendall() at once -- so no timeout is
        # needed, and a wedged daemon stalls the writer (the designed behaviour) instead
        # of being mistaken for a dead one.
        sock.settimeout(None)
        with self._lock:
            self._sock = sock
            self._gen += 1
        self._link_up.set()

    def _shutdown_socket(self) -> socket.socket | None:
        with self._lock:
            sock, self._sock = self._sock, None
            self._gen += 1
        if sock is not None:
            try:
                sock.shutdown(socket.SHUT_RDWR)
            except OSError:  # pragma: no cover - already gone
                pass
            try:
                sock.close()
            except OSError:  # pragma: no cover - defensive
                pass
        return sock

    def _start_threads(self) -> None:
        if self._threads:
            return
        for target, name in ((self._writer_loop, "ipc-writer"), (self._reader_loop, "ipc-reader")):
            thread = threading.Thread(target=target, name=name, daemon=True)
            self._threads.append(thread)
            thread.start()

    # -- handshake ----------------------------------------------------------

    def _handshake(self) -> None:
        try:
            result = self.request(proto.HELLO, {"api_version": proto.API_VERSION})
        except RpcError as exc:
            self.close()
            raise CliError(
                EXIT_ENV_ERROR,
                f"the robot daemon on {self._socket_path} did not answer hello: {exc.message}",
                remediation="Check the daemon is healthy; `journalctl -u robotd` on the box.",
            ) from exc
        self._record_hello(result, None)

    def _record_hello(self, result: Any, error: tuple[int, str] | None) -> None:
        """Record what ``hello`` answered. Any api_version is accepted -- see api_skew."""
        if error is not None or not isinstance(result, dict):
            return
        api = result.get("api_version")
        self.daemon = DaemonInfo(
            api_version=api if isinstance(api, int) else None,
            daemon_version=_as_str(result.get("daemon_version")),
            revision=_as_str(result.get("revision")),
        )

    def _verify_joint_table(self) -> None:
        """Subscribe briefly and check the first ``robot.state`` frame's joint count.

        A mismatch is fatal: every index into that vector would name the wrong joint.
        Silence is NOT — a daemon that never streams tells us nothing about its table,
        so the client stays up with ``joints_verified`` False.
        """
        self._state_seen.clear()
        try:
            self.subscribe()
        except RpcError as exc:
            self._drop(DROP_SUBSCRIBE_FAILED, f"{proto.ROBOT_SUBSCRIBE}: {exc.message}")
            return
        if not self._state_seen.wait(self._request_timeout_s):
            return
        count = self._joint_count
        expected = len(proto.JOINT_NAMES)
        if count is not None and count != expected:
            self.close()
            raise CliError(
                EXIT_ENV_ERROR,
                f"daemon reports {count} joints, client table has {expected}",
                remediation=(
                    "The daemon and this CLI disagree on the joint table: re-pin "
                    "duck-ipc-proto (docs/upstream-pins.md) or update the daemon."
                ),
            )
        self._joints_verified = True

    # -- sending ------------------------------------------------------------

    def notify(self, method: str, params: Any = None) -> bool:
        """Queue a fire-and-forget notification. O(1); NEVER blocks the caller.

        Returns True when the frame was queued, False when it was dropped — with the
        reason counted and logged either way. This is the tick-path entry point: with a
        wedged daemon it returns in microseconds and counts ``ipc-queue-full`` rather
        than waiting for a socket that will never drain.
        """
        if not self._link_up.is_set():
            self._drop(DROP_DOWN, f"{method} not sent: no link to {self._socket_path}")
            self._reconnect.set()
            return False
        try:
            self._queue.put_nowait(self._encode(method, params, None))
        except queue.Full:
            self._drop(DROP_QUEUE_FULL, f"{method} dropped: write queue full ({self._queue_depth})")
            return False
        return True

    def request(self, method: str, params: Any = None, timeout: float | None = None) -> Any:
        """Send a request and wait for its correlated reply.

        Returns the ``result`` object (a dict for every method the daemon defines).
        Raises :class:`RpcError` for a JSON-RPC error reply, :class:`RpcTimeout` when no
        reply arrives in time (the message names the method), :class:`RpcLinkDown` when
        there is no socket, and :class:`RpcQueueFull` when the write queue is full.
        Never raises ``CliError`` and never lets a traceback out.
        """
        wait_s = self._request_timeout_s if timeout is None else timeout
        request_id, pending = self._register(method)
        frame = self._encode(method, params, request_id)

        if not self._link_up.is_set():
            self._discard(request_id)
            self._reconnect.set()
            self._drop(DROP_DOWN, f"{method} not sent: no link to {self._socket_path}")
            raise RpcLinkDown(f"{method} not sent: no link to {self._socket_path}", method=method)
        try:
            self._queue.put_nowait(frame)
        except queue.Full:
            self._discard(request_id)
            self._drop(DROP_QUEUE_FULL, f"{method} dropped: write queue full ({self._queue_depth})")
            raise RpcQueueFull(f"{method} dropped: the write queue is full", method=method)

        if not pending.event.wait(wait_s):
            self._discard(request_id)
            detail = f"{method} timed out after {wait_s:g}s"
            self._drop(DROP_TIMEOUT, detail)
            raise RpcTimeout(detail, method=method)
        self._discard(request_id)

        if pending.error is not None:
            code, message = pending.error
            if code == proto.METHOD_NOT_FOUND:
                with self._lock:
                    self._unsupported.add(method)
                self._drop(DROP_METHOD_NOT_FOUND, f"{method}: {message}")
            raise RpcError(code, message, method=method)
        return pending.result

    def send(self, method: str, params: Any = None) -> Any:
        """Route by the protocol's own classification: notify a continuous intent,
        request a discrete one. See :func:`microduck_cli.ipc.proto.is_notification`."""
        if proto.is_notification(method):
            return self.notify(method, params)
        return self.request(method, params)

    def supports(self, method: str) -> bool:
        """Has this daemon got ``method``?

        Optimistic: True until the daemon answers -32601 once, then False forever on
        this client. A caller on the tick path checks this instead of catching an
        exception per tick for a method a daemon on an older API simply lacks.
        """
        with self._lock:
            return method not in self._unsupported

    def _encode(self, method: str, params: Any, request_id: int | None) -> bytes:
        message: dict[str, Any] = {"jsonrpc": proto.JSONRPC_VERSION, "method": method}
        if params is not None:
            message["params"] = params
        if request_id is not None:
            message["id"] = request_id
        return (json.dumps(message, separators=(",", ":")) + "\n").encode()

    def _register(
        self,
        method: str,
        callback: Callable[[Any, tuple[int, str] | None], None] | None = None,
    ) -> tuple[int, _Pending]:
        pending = _Pending(method=method, callback=callback)
        with self._lock:
            self._next_id += 1
            request_id = self._next_id
            self._pending[request_id] = pending
        return request_id, pending

    def _discard(self, request_id: int) -> None:
        with self._lock:
            self._pending.pop(request_id, None)

    def _fail_pending(self, detail: str) -> None:
        with self._lock:
            pending = list(self._pending.values())
            self._pending.clear()
        for entry in pending:
            entry.error = (CLIENT_LINK_DOWN, detail)
            if entry.callback is not None:
                entry.callback(None, entry.error)
            entry.event.set()

    # -- writer thread ------------------------------------------------------

    def _writer_loop(self) -> None:
        while not self._closing.is_set():
            if not self._link_up.is_set():
                if self._reconnect.wait(_RECONNECT_POLL_S) and not self._closing.is_set():
                    self._reconnect.clear()
                    self._try_reconnect()
                continue
            try:
                frame = self._queue.get(timeout=_DRAIN_POLL_S)
            except queue.Empty:
                continue
            self._write_frame(frame)

    def _write_frame(self, frame: bytes) -> None:
        with self._lock:
            sock, gen = self._sock, self._gen
        if sock is None:
            self._mark_link_down("write attempted with no socket", gen)
            return
        try:
            sock.sendall(frame)
        except OSError as exc:
            self._mark_link_down(f"write failed: {exc}", gen)

    def _try_reconnect(self) -> None:
        """Reopen the link and redo the handshake. A failure simply leaves it down.

        The control loop is the retry timer: the next :meth:`notify` asks again. Nothing
        here blocks a caller — this runs on the writer thread.
        """
        try:
            self._open_socket()
        except CliError:
            return
        self._fire(proto.HELLO, {"api_version": proto.API_VERSION}, self._record_hello)
        if self._subscribed_hz is not None:
            self._fire(
                proto.ROBOT_SUBSCRIBE,
                {"hz": self._subscribed_hz},
                self._record_subscribe,
            )

    def _fire(
        self,
        method: str,
        params: Any,
        callback: Callable[[Any, tuple[int, str] | None], None],
    ) -> None:
        """Send a request whose reply is handled by ``callback``, with nobody waiting."""
        request_id, _pending = self._register(method, callback=callback)
        try:
            self._queue.put_nowait(self._encode(method, params, request_id))
        except queue.Full:  # pragma: no cover - the queue is drained on link loss
            self._discard(request_id)
            self._drop(DROP_QUEUE_FULL, f"{method} dropped: write queue full")

    # -- reader thread ------------------------------------------------------

    def _reader_loop(self) -> None:
        buffer = b""
        while not self._closing.is_set():
            with self._lock:
                sock, gen = self._sock, self._gen
            if sock is None:
                self._link_up.wait(_RECONNECT_POLL_S)
                buffer = b""
                continue
            try:
                chunk = sock.recv(_RECV_SIZE)
            except OSError as exc:
                self._mark_link_down(f"read failed: {exc}", gen)
                buffer = b""
                continue
            if not chunk:
                self._mark_link_down("the daemon closed the connection", gen)
                buffer = b""
                continue
            buffer += chunk
            while b"\n" in buffer:
                line, buffer = buffer.split(b"\n", 1)
                if line.strip():
                    self._dispatch(line)

    def _dispatch(self, line: bytes) -> None:
        try:
            message = json.loads(line)
        except json.JSONDecodeError:
            self._drop(DROP_MALFORMED, f"undecodable line ({len(line)} bytes)")
            return
        if not isinstance(message, dict):
            self._drop(DROP_MALFORMED, "a JSON-RPC message must be an object")
            return
        request_id = message.get("id")
        if request_id is None and "method" in message:
            self._on_notification(str(message["method"]), message.get("params"))
            return
        if request_id is None:
            self._drop(DROP_MALFORMED, "message carries neither an id nor a method")
            return
        self._on_reply(request_id, message)

    def _on_reply(self, request_id: Any, message: dict[str, Any]) -> None:
        with self._lock:
            pending = self._pending.pop(request_id, None)
        if pending is None:
            self._drop(DROP_LATE_REPLY, f"reply for unknown id {request_id!r}")
            return
        error = message.get("error")
        if isinstance(error, dict):
            code = error.get("code")
            pending.error = (
                code if isinstance(code, int) else proto.INTERNAL_ERROR,
                str(error.get("message", "")),
            )
        else:
            pending.result = message.get("result")
        if pending.callback is not None:
            pending.callback(pending.result, pending.error)
        pending.event.set()

    def _on_notification(self, method: str, params: Any) -> None:
        with self._lock:
            self._slots[method] = _Slot(params=params, stamp=self._clock())
        if method == proto.ROBOT_STATE:
            self._note_state_frame(params)

    def _note_state_frame(self, params: Any) -> None:
        joints = params.get("joints") if isinstance(params, dict) else None
        if isinstance(joints, list):
            self._joint_count = len(joints)
            if len(joints) != len(proto.JOINT_NAMES) and not self._mismatch_reported:
                self._mismatch_reported = True
                self._drop(
                    DROP_JOINT_MISMATCH,
                    f"daemon reports {len(joints)} joints, "
                    f"client table has {len(proto.JOINT_NAMES)}",
                )
        self._state_seen.set()

    def _mark_link_down(self, detail: str, gen: int | None = None) -> None:
        """Tear the link down once, guarded by generation so a stale thread cannot
        clobber a link that has already been re-established."""
        with self._lock:
            if gen is not None and gen != self._gen:
                return
            if self._sock is None and not self._link_up.is_set():
                return
        self._link_up.clear()
        self._shutdown_socket()
        if self._closing.is_set():
            return
        self._drop(DROP_DOWN, detail)
        self._drain_queue(detail)
        self._fail_pending(detail)
        self._mismatch_reported = False

    def _drain_queue(self, detail: str) -> None:
        """Discard everything queued for a link that is gone. One line, not one per frame."""
        discarded = 0
        while True:
            try:
                self._queue.get_nowait()
            except queue.Empty:
                break
            discarded += 1
        if discarded:
            self._drop(DROP_DOWN, f"discarded {discarded} queued frames: {detail}")

    # -- drops --------------------------------------------------------------

    def _drop(self, reason: str, detail: str) -> None:
        with self._lock:
            self._drops[reason] += 1
        LOGGER.warning("[SENSE stage=ipc source=%s event=%s] %s", self._socket_path, reason, detail)

    # -- subscriptions and peek slots ---------------------------------------

    def subscribe(self, hz: int | None = None) -> SubscribeResult:
        """Start (or reuse) the ``robot.state`` stream and keep its answer.

        Re-issued only when the requested rate differs from the live subscription, so a
        caller may call this freely. The result is where the skill list comes from on
        API 16 — see :meth:`skills_from_subscribe`.

        ``hz`` goes on the wire as an integer: upstream types it ``u32`` and the daemon
        rejects a JSON float outright.
        """
        rate = self._state_hz if hz is None else int(hz)
        cached = self._subscribe_result
        if cached is not None and self._subscribed_hz == rate:
            return cached
        result = self.request(proto.ROBOT_SUBSCRIBE, {"hz": rate})
        self._subscribed_hz = rate
        return self._record_subscribe(result, None)

    def _record_subscribe(self, result: Any, error: tuple[int, str] | None) -> SubscribeResult:
        if error is not None:
            return self._subscribe_result or SubscribeResult()
        parsed = SubscribeResult.from_result(result)
        self._subscribe_result = parsed
        return parsed

    @property
    def subscribe_result(self) -> SubscribeResult | None:
        """The last ``robot.subscribe`` answer, or ``None`` before subscribing."""
        return self._subscribe_result

    def skills_from_subscribe(self) -> tuple[str, ...] | None:
        """Skill names the daemon advertised in ``robot.subscribe``.

        ``None`` means "never asked" — distinct from ``()``, "asked, and the daemon has
        no skill policies loaded", which is what a bare ``robotd --fake`` reports.
        """
        if self._subscribe_result is None:
            return None
        return self._subscribe_result.skills

    def subscribe_pad(self) -> Any:
        """Ask for ``pad.report`` frames (``pad.input``). Served by padd on the real box."""
        return self.request(proto.PAD_INPUT)

    def subscribe_tof(self) -> Any:
        """Ask for ``tof.frame`` frames (``tof.stream``). Served by tofd on the real box."""
        return self.request(proto.TOF_STREAM)

    def peek(self, method: str) -> tuple[Any, float | None] | None:
        """The last params and stamp for ``method``, or ``None``. Peeks; never consumes."""
        with self._lock:
            slot = self._slots.get(method)
            if slot is None:
                return None
            return (slot.params, slot.stamp)

    def _peek_params(self, method: str) -> Any:
        with self._lock:
            slot = self._slots.get(method)
            return None if slot is None else slot.params

    def _peek_stamp(self, method: str) -> float | None:
        with self._lock:
            slot = self._slots.get(method)
            return None if slot is None else slot.stamp

    def _peek_path(self, method: str, *path: str) -> Any:
        return _walk(self._peek_params(method), path)

    # -- polls (caller-driven; never from the reader thread) -----------------

    def poll_health(self, timeout: float | None = None) -> Any:
        """Request ``robot.health`` and refresh its peek slot. Returns the result or None.

        Called by the control loop at ~2 Hz — health is a request, not a stream, and
        polling it from the reader thread would deadlock a reply against the very thread
        that must read it. Failures are swallowed into a named drop: this is on the tick
        path and must not raise.
        """
        return self._poll(proto.ROBOT_HEALTH, timeout)

    def poll_remote_session(self, timeout: float | None = None) -> Any:
        """Request ``robot.remoteSessionActive`` and refresh its peek slot."""
        return self._poll(proto.ROBOT_SESSION_ACTIVE, timeout)

    def _poll(self, method: str, timeout: float | None) -> Any:
        try:
            result = self.request(method, timeout=timeout)
        except RpcError as exc:
            self._drop(DROP_POLL_FAILED, f"{method}: {exc.message}")
            return None
        with self._lock:
            self._slots[method] = _Slot(params=result, stamp=self._clock())
        return result

    # -- the sense seam -----------------------------------------------------

    def providers(self) -> SenseProviders:
        """Peek callables over this client's slots, for
        :func:`microduck_cli.behavior.sense.read_sense`.

        Every callable reads a slot the reader thread already filled — no request, no
        block, no consume — so any number of consumers within one tick see the same
        sample. Fields with no source on the pinned build stay unwired: ``pad_active``
        and ``tof_nearest_m`` need payload shapes the probe never captured, and
        ``enabled`` / ``self_moving`` / ``mode`` belong to the engine's own latches
        rather than to a notification.
        """
        state = proto.ROBOT_STATE
        health = proto.ROBOT_HEALTH
        return SenseProviders(
            fallen=lambda: self._peek_path(state, "safety", "fallen"),
            limp=lambda: self._peek_path(state, "safety", "limp"),
            gravity=lambda: self._peek_path(state, "safety", "gravity"),
            loop_hz=lambda: self._peek_path(state, "loop", "hz"),
            policy=lambda: self._peek_path(state, "policy"),
            move_applied=lambda: self._peek_path(state, "move", "applied"),
            move_requested=lambda: self._peek_path(state, "move", "requested"),
            battery_frac=self._battery_frac,
            hottest_servo_c=lambda: self._peek_path(health, "motors", "hottest_c"),
            remote_session=lambda: self._peek_path(proto.ROBOT_SESSION_ACTIVE, "active"),
            skills=self.skills_from_subscribe,
            state_stamp=lambda: self._peek_stamp(state),
            health_stamp=lambda: self._peek_stamp(health),
            pad_stamp=lambda: self._peek_stamp(proto.PAD_REPORT),
            tof_stamp=lambda: self._peek_stamp(proto.TOF_FRAME),
        )

    def _battery_frac(self) -> float | None:
        """``robot.health``'s ``battery.percent`` as a 0..1 fraction.

        Absent on ``robotd --fake`` (no battery is measured there), which reads as "no
        reading" rather than as a flat pack -- exactly the Sense contract.
        """
        percent = self._peek_path(proto.ROBOT_HEALTH, "battery", "percent")
        if isinstance(percent, bool) or not isinstance(percent, (int, float)):
            return None
        return float(percent) / 100.0


__all__ = [
    "CLIENT_LINK_DOWN",
    "CLIENT_QUEUE_FULL",
    "CLIENT_TIMEOUT",
    "DROP_DOWN",
    "DROP_JOINT_MISMATCH",
    "DROP_LATE_REPLY",
    "DROP_MALFORMED",
    "DROP_METHOD_NOT_FOUND",
    "DROP_POLL_FAILED",
    "DROP_QUEUE_FULL",
    "DROP_SUBSCRIBE_FAILED",
    "DROP_TIMEOUT",
    "LOGGER",
    "DaemonInfo",
    "RobotClient",
    "RpcError",
    "RpcLinkDown",
    "RpcQueueFull",
    "RpcTimeout",
    "SubscribeResult",
]
