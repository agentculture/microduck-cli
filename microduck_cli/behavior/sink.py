"""The target sink — a composed pose becomes robotd wire traffic, and nothing else.

:class:`~microduck_cli.behavior.engine.Engine` writes ONE ``{channel: value}``
dict per tick through a :class:`~microduck_cli.behavior.engine.TargetSink`. This
module is the sink that speaks to a real daemon: it owns the *wire encoding* that
:mod:`microduck_cli.behavior.intents` deliberately refuses to invent (each kind's
contribution carries its validated parameters under its channel name; turning
those into ``robot.move`` / ``robot.head`` / ``robot.do`` params is this leaf's
job), and it owns nothing else.

Three contracts are load-bearing.

**Continuous channels are NOTIFICATIONS; discrete ones are REQUESTS.** Which is
which is not a local opinion — :func:`microduck_cli.ipc.proto.is_notification`
transcribes the pinned protocol, and every method this module sends is routed by
it. ``twist``/``head``/``pose``/``mouth``/``sound`` stream out fire-and-forget at
the tick rate; ``skill`` (``robot.do``), ``stop`` (``robot.stop``) and ``mode``
(``robot.setMode``) are calls whose *answer* matters, so their ``accepted`` /
``reason`` is read and a refusal becomes a named drop.

**The tick never blocks.** ``client.notify`` is already a ``put_nowait``. A
request is not: it waits for a correlated reply, which at 50 Hz would be a missed
tick every time the daemon hesitates. So requests are handed to ONE small worker
thread with a bounded queue, and :meth:`RobotSink.write` returns without ever
waiting for a reply. A full worker queue is a named drop, exactly like a full
write queue — never a wait.

**No filtering, no smoothing, no rate limiting, no clamping** (honesty h2). The
value a behaviour composed reaches the daemon byte-for-byte. robotd's own
``cmd_alpha`` EMA is the plant, and a second filter on this side would mean two
undocumented low-pass stages between an operator's number and the duck's legs —
the exact situation where "the robot moved less than I asked" has no single
owner. Bounds are enforced once, on admission, by
:mod:`~microduck_cli.behavior.intents`; this module re-checks nothing and
silently repairs nothing. A value it cannot *encode* (a twist that is not three
numbers) is refused as a named drop rather than coerced.

One thing is deliberately edge-triggered, and it is not a value filter: a
**discrete** channel is sent when its value CHANGES, not once per tick. ``skill``
is the whole reason — a ``do`` behaviour claims its channel for its entire
lifetime, so a per-tick ``robot.do`` would fire the same canned skill fifty times
a second. The value that goes out is still exactly the one composed; only the
repetition is dropped. Continuous channels are never de-duplicated: a stream that
stops streaming is what robotd's deadman is watching for.
"""

from __future__ import annotations

import queue
import threading
from collections import Counter
from typing import Any, Callable, Iterable, Mapping

from microduck_cli.behavior import senselog
from microduck_cli.ipc import proto

#: ``senselog`` stage token for everything this module reports.
STAGE = "sink"

#: A continuous channel's notification never reached the write queue.
DROP_NOTIFY_FAILED = "sink-notify-failed"
#: A discrete request came back ``accepted: false``, or raised.
DROP_REQUEST_REFUSED = "sink-request-refused"
#: The request worker's own queue is full — the daemon is not keeping up.
DROP_REQUEST_QUEUE_FULL = "sink-request-queue-full"
#: A channel carried a value this sink cannot encode onto the wire.
DROP_UNENCODABLE = "sink-unencodable"

#: The pose keys this sink sends as notifications, and the method each maps to.
CONTINUOUS_CHANNELS: dict[str, str] = {
    "twist": proto.ROBOT_MOVE,
    "head": proto.ROBOT_HEAD,  # or ROBOT_LOOK, decided per contribution
    "pose": proto.ROBOT_POSE,
    "mouth": proto.ROBOT_MOUTH,
    "sound": proto.ROBOT_SOUND,
}

#: The pose keys this sink sends as requests. ``stop`` and ``mode`` are not
#: :data:`~microduck_cli.behavior.model.CHANNELS` — they are extra keys a caller
#: (or a later noun verb) may place in the pose dict to make a discrete call ride
#: the same seam instead of opening a second path to the socket.
DISCRETE_CHANNELS: tuple[str, ...] = ("skill", "stop", "mode")

#: ``robot.move``'s param fields. The third axis is ``vyaw`` on the wire while the
#: intent layer calls it ``wz``; this rename happens HERE and only here.
TWIST_FIELDS: tuple[str, str, str] = ("vx", "vy", "vyaw")

#: ``robot.head``'s param fields (``HeadParams``).
HEAD_FIELDS: tuple[str, ...] = ("neck_pitch", "head_pitch", "head_yaw", "head_roll")

#: ``robot.look``'s param fields (``LookParams``). A ``head`` contribution naming
#: any of ``x``/``y``/``z`` is a LOOK TARGET, not a joint pose.
LOOK_FIELDS: tuple[str, ...] = ("x", "y", "z", "neck_pitch")

#: The three keys that mark a head contribution as a look target.
LOOK_MARKERS: frozenset[str] = frozenset({"x", "y", "z"})

#: ``robot.pose``'s param fields (``PoseParams``).
POSE_FIELDS: tuple[str, ...] = ("z", "roll", "pitch", "active")

#: How long a queued request may wait for its reply on the worker thread. Never
#: felt by the tick — the worker waits, the loop does not.
DEFAULT_REQUEST_TIMEOUT_S = 2.0

#: How many un-dispatched requests the worker holds. Small on purpose: a backlog
#: of discrete calls is a daemon that is not answering, and the honest response is
#: a named drop, not a queue that grows until the process dies.
DEFAULT_REQUEST_QUEUE_DEPTH = 32


class _Encoding:
    """One channel's wire form: ``(method, params)`` or a refusal reason."""

    __slots__ = ("method", "params", "error")

    def __init__(self, method: str = "", params: Any = None, error: str = "") -> None:
        self.method = method
        self.params = params
        self.error = error


def _numbers(value: Any, count: int) -> tuple[float, ...] | None:
    """*value* as exactly *count* real numbers, or ``None`` when it is not."""
    if isinstance(value, (str, bytes, Mapping)):
        return None
    try:
        items = list(value)
    except TypeError:
        return None
    if len(items) != count:
        return None
    out: list[float] = []
    for item in items:
        if isinstance(item, bool) or not isinstance(item, (int, float)):
            return None
        out.append(float(item))
    return tuple(out)


def _pick(value: Mapping, fields: Iterable[str]) -> dict[str, Any]:
    """The subset of *fields* present in *value*, in *fields* order.

    Absent fields are OMITTED rather than defaulted: this CLI has no authority to
    invent a neck angle a behaviour did not ask for, and every one of these param
    structs derives ``Default`` on the daemon side anyway.
    """
    return {name: value[name] for name in fields if name in value}


def encode_twist(value: Any) -> _Encoding:
    """``(vx, vy, wz)`` -> ``robot.move {vx, vy, vyaw}``.

    Also accepts a mapping naming the axes (``wz`` or ``vyaw``), which is what a
    hand-written pose dict tends to look like.
    """
    if isinstance(value, Mapping):
        params: dict[str, Any] = {}
        for wire, names in (("vx", ("vx",)), ("vy", ("vy",)), ("vyaw", ("vyaw", "wz"))):
            for name in names:
                if name in value:
                    params[wire] = value[name]
                    break
        if not params:
            return _Encoding(error=f"twist mapping names no axis: {value!r}")
        return _Encoding(proto.ROBOT_MOVE, params)
    triple = _numbers(value, 3)
    if triple is None:
        return _Encoding(error=f"twist must be three numbers (vx, vy, wz), got {value!r}")
    return _Encoding(proto.ROBOT_MOVE, dict(zip(TWIST_FIELDS, triple)))


def encode_head(value: Any) -> _Encoding:
    """A head contribution -> ``robot.look`` (a target) or ``robot.head`` (joints).

    The discriminator is the contribution's own vocabulary: ``LookParams`` names a
    trunk-frame point (``x``/``y``/``z``), ``HeadParams`` names joints. A
    contribution carrying any of the three point axes is a look target and goes out
    as the ``robot.look`` REQUEST the protocol classifies it as; anything else is
    the ``robot.head`` notification.
    """
    if not isinstance(value, Mapping):
        return _Encoding(error=f"head must be a mapping of angles or a look target, got {value!r}")
    if LOOK_MARKERS & set(value):
        return _Encoding(proto.ROBOT_LOOK, _pick(value, LOOK_FIELDS))
    params = _pick(value, HEAD_FIELDS)
    if not params:
        return _Encoding(error=f"head names no known field: {sorted(value)}")
    return _Encoding(proto.ROBOT_HEAD, params)


def encode_pose(value: Any) -> _Encoding:
    """``{z, roll, pitch, active}`` -> ``robot.pose``."""
    if not isinstance(value, Mapping):
        return _Encoding(error=f"pose must be a mapping of {POSE_FIELDS}, got {value!r}")
    return _Encoding(proto.ROBOT_POSE, _pick(value, POSE_FIELDS))


def encode_mouth(value: Any) -> _Encoding:
    """A beak opening -> ``robot.mouth {open}``. Accepts a bare number or a mapping."""
    if isinstance(value, Mapping):
        if "open" not in value:
            return _Encoding(error=f"mouth mapping has no 'open' field: {sorted(value)}")
        return _Encoding(proto.ROBOT_MOUTH, {"open": value["open"]})
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return _Encoding(error=f"mouth must be a number in 0..1, got {value!r}")
    return _Encoding(proto.ROBOT_MOUTH, {"open": float(value)})


def encode_sound(value: Any) -> _Encoding:
    """``{name, hold}`` (the intent's vocabulary) -> ``robot.sound {tag, hold}``.

    ``name`` -> ``tag`` is the second and last rename this module owns. ``hold`` is
    omitted when the contribution left it unset, so the daemon's own default
    stands rather than a ``False`` we made up.
    """
    if isinstance(value, str):
        return _Encoding(proto.ROBOT_SOUND, {"tag": value})
    if not isinstance(value, Mapping):
        return _Encoding(error=f"sound must be a tag or a mapping, got {value!r}")
    tag = value.get("tag", value.get("name"))
    if not isinstance(tag, str) or not tag:
        return _Encoding(error=f"sound names no tag: {value!r}")
    params: dict[str, Any] = {"tag": tag}
    hold = value.get("hold")
    if hold is not None:
        params["hold"] = bool(hold)
    return _Encoding(proto.ROBOT_SOUND, params)


def encode_skill(value: Any) -> _Encoding:
    """A skill name -> ``robot.do {skill}``."""
    if isinstance(value, Mapping):
        value = value.get("skill")
    if not isinstance(value, str) or not value:
        return _Encoding(error=f"skill must be a non-empty name, got {value!r}")
    return _Encoding(proto.ROBOT_DO, {"skill": value})


def encode_stop(value: Any) -> _Encoding:
    """Anything truthy -> ``robot.stop`` (which takes no params)."""
    if not value:
        return _Encoding(error="stop must be truthy to be sent")
    return _Encoding(proto.ROBOT_STOP, None)


def encode_mode(value: Any) -> _Encoding:
    """``"walk"`` / ``"roller"`` -> ``robot.setMode {mode}``."""
    if isinstance(value, Mapping):
        value = value.get("mode")
    if not isinstance(value, str) or not value:
        return _Encoding(error=f"mode must be a non-empty name, got {value!r}")
    return _Encoding(proto.ROBOT_SET_MODE, {"mode": value})


#: ``channel -> encoder``. The ONE table; nothing else in this module knows a
#: channel name, so adding a channel is one entry plus its encoder.
ENCODERS: dict[str, Callable[[Any], _Encoding]] = {
    "twist": encode_twist,
    "head": encode_head,
    "pose": encode_pose,
    "mouth": encode_mouth,
    "sound": encode_sound,
    "skill": encode_skill,
    "stop": encode_stop,
    "mode": encode_mode,
}

#: The order channels are sent in within one tick. Fixed so a call log is
#: comparable across runs, and motion-before-expression so a stop-shaped tick
#: reaches the legs first.
WRITE_ORDER: tuple[str, ...] = ("stop", "mode", "twist", "head", "pose", "mouth", "sound", "skill")


class RobotSink:
    """A :class:`~microduck_cli.behavior.engine.TargetSink` backed by a
    :class:`~microduck_cli.ipc.client.RobotClient`.

    Construct one per engine and hand it to ``Engine(sink=…)``. It owns a single
    daemon worker thread, started lazily on the first request and stopped by
    :meth:`close` (or by using the sink as a context manager). It does NOT own the
    client: connecting and closing the socket stay with whoever built it.

    :param client: a connected (or reconnecting) ``RobotClient``.
    :param request_timeout_s: how long the WORKER waits for a discrete reply.
    :param queue_depth: how many pending requests the worker will hold.
    :param repeat_discrete: send a discrete channel every tick instead of on
        change. Off by default — see the module docstring.
    """

    def __init__(
        self,
        client,
        *,
        request_timeout_s: float = DEFAULT_REQUEST_TIMEOUT_S,
        queue_depth: int = DEFAULT_REQUEST_QUEUE_DEPTH,
        repeat_discrete: bool = False,
    ) -> None:
        self._client = client
        self._request_timeout_s = request_timeout_s
        self._repeat_discrete = repeat_discrete
        self._queue: queue.Queue[tuple[str, str, Any] | None] = queue.Queue(maxsize=queue_depth)
        self._worker: threading.Thread | None = None
        self._closing = threading.Event()
        self._lock = threading.Lock()
        self._last_discrete: dict[str, Any] = {}
        #: Named drop counters, same shape and spirit as ``RobotClient.drops``.
        self.drops: Counter[str] = Counter()
        #: ``method -> count`` of everything this sink handed the client.
        self.sent: Counter[str] = Counter()
        #: The last refusal reason per method, for a verb that wants to report it.
        self.refusals: dict[str, str] = {}

    # -- lifecycle ---------------------------------------------------------

    @property
    def client(self):
        """The client this sink writes through (never owned by the sink)."""
        return self._client

    def __enter__(self) -> "RobotSink":
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    def close(self, timeout: float = 2.0) -> None:
        """Stop the request worker, draining what it has already accepted."""
        self._closing.set()
        worker = self._worker
        if worker is not None:
            try:
                self._queue.put_nowait(None)
            except queue.Full:  # pragma: no cover - the sentinel is best-effort
                pass
            worker.join(timeout)
        self._worker = None

    # -- the sink contract -------------------------------------------------

    def write(self, pose: dict[str, object]) -> None:
        """Send one composed tick. NEVER blocks, NEVER raises, never filters.

        A channel absent from *pose* sends nothing at all: composition omits a
        channel nobody owns, and the daemon's own last value (plus its deadman)
        stays in charge rather than being overwritten with a neutral this CLI
        invented.
        """
        if not pose:
            return
        for channel in WRITE_ORDER:
            if channel not in pose:
                continue
            value = pose[channel]
            if value is None:
                continue
            self._send_channel(channel, value)

    def _send_channel(self, channel: str, value: Any) -> None:
        encoder = ENCODERS.get(channel)
        if encoder is None:
            self._drop(DROP_UNENCODABLE, channel, f"no encoder for channel {channel!r}")
            return
        encoded = encoder(value)
        if encoded.error:
            self._drop(DROP_UNENCODABLE, channel, encoded.error)
            return
        if proto.is_notification(encoded.method):
            self._notify(channel, encoded)
            return
        if not self._repeat_discrete and self._unchanged(channel, encoded.params):
            return
        self._enqueue_request(channel, encoded)

    def _unchanged(self, channel: str, params: Any) -> bool:
        """Edge-trigger a discrete channel; record the new value when it changes."""
        with self._lock:
            previous = self._last_discrete.get(channel, _UNSET)
            if previous is not _UNSET and previous == params:
                return True
            self._last_discrete[channel] = params
        return False

    def forget_discrete(self, channel: str | None = None) -> None:
        """Clear the edge-trigger memory so the next write re-sends a discrete call.

        The engine's counterpart to a behaviour ending: a second ``do`` of the same
        skill is a genuinely new request, and whoever admits it says so here.
        """
        with self._lock:
            if channel is None:
                self._last_discrete.clear()
            else:
                self._last_discrete.pop(channel, None)

    # -- the two send paths ------------------------------------------------

    def _notify(self, channel: str, encoded: _Encoding) -> None:
        if self._client.notify(encoded.method, encoded.params):
            self.sent[encoded.method] += 1
            return
        self._drop(
            DROP_NOTIFY_FAILED,
            channel,
            f"{encoded.method} was not queued (see the client's own drop counters)",
        )

    def _enqueue_request(self, channel: str, encoded: _Encoding) -> None:
        self._ensure_worker()
        try:
            self._queue.put_nowait((channel, encoded.method, encoded.params))
        except queue.Full:
            self._drop(
                DROP_REQUEST_QUEUE_FULL,
                channel,
                f"{encoded.method} dropped: the request worker is {self._queue.maxsize} behind",
            )

    def _ensure_worker(self) -> None:
        if self._worker is not None and self._worker.is_alive():
            return
        if self._closing.is_set():  # pragma: no cover - closed sinks send nothing
            return
        self._worker = threading.Thread(
            target=self._worker_loop, name="microduck-sink-requests", daemon=True
        )
        self._worker.start()

    def _worker_loop(self) -> None:
        while True:
            try:
                item = self._queue.get(timeout=0.05)
            except queue.Empty:
                if self._closing.is_set():
                    return
                continue
            if item is None:
                return
            channel, method, params = item
            self._call(channel, method, params)

    def _call(self, channel: str, method: str, params: Any) -> None:
        """Make one discrete call and turn its answer into a fact, never a raise."""
        try:
            result = self._client.request(method, params, timeout=self._request_timeout_s)
        except Exception as exc:  # a refused call is a drop, never a dead worker
            self._drop(DROP_REQUEST_REFUSED, channel, f"{method}: {exc}")
            self.refusals[method] = str(exc)
            return
        self.sent[method] += 1
        accepted, reason = _answer(result)
        if accepted is False:
            detail = f"{method}: {reason}" if reason else f"{method}: refused"
            self._drop(DROP_REQUEST_REFUSED, channel, detail)
            self.refusals[method] = reason or "refused"

    # -- drops -------------------------------------------------------------

    def _drop(self, reason: str, source: str, detail: str) -> None:
        self.drops[reason] += 1
        senselog.drop(STAGE, source, reason, detail)


class _Unset:
    """A sentinel distinct from every value a discrete channel may carry."""

    __slots__ = ()

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return "<unset>"


_UNSET = _Unset()


def _answer(result: Any) -> tuple[bool | None, str]:
    """``(accepted, reason)`` from a discrete reply; ``(None, "")`` when it says neither.

    A daemon that answers with no ``accepted`` field (``robot.look`` returns the
    head pose it adopted) is NOT treated as a refusal: absence of the field is
    absence of a verdict, and inventing one would name a drop that never happened.
    """
    if not isinstance(result, Mapping):
        return (None, "")
    accepted = result.get("accepted")
    reason = result.get("reason")
    return (
        None if accepted is None else bool(accepted),
        reason if isinstance(reason, str) else "",
    )


__all__ = [
    "CONTINUOUS_CHANNELS",
    "DISCRETE_CHANNELS",
    "DROP_NOTIFY_FAILED",
    "DROP_REQUEST_QUEUE_FULL",
    "DROP_REQUEST_REFUSED",
    "DROP_UNENCODABLE",
    "ENCODERS",
    "HEAD_FIELDS",
    "LOOK_FIELDS",
    "POSE_FIELDS",
    "STAGE",
    "TWIST_FIELDS",
    "WRITE_ORDER",
    "RobotSink",
    "encode_head",
    "encode_mode",
    "encode_mouth",
    "encode_pose",
    "encode_skill",
    "encode_sound",
    "encode_stop",
    "encode_twist",
]
