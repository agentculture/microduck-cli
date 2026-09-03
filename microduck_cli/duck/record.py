"""JSONL recorder: everything one duck reports, one JSON object per line.

``microduck-cli duck record`` writes a *recording* — the raw sense traffic of a
running duck, in arrival order, on stdout — so a session can be replayed,
diffed or shipped with a bug report. The engine's replay reader
(``behavior/replay.py``) consumes exactly this shape.

The schema, normative
---------------------
One JSON object per line, no blank lines, no wrapper array::

    {"ts": <float>, "source": "<source>", "params": {...}}

``ts``
    Monotonic seconds (``time.monotonic``). For a streamed frame it is the
    reading the *client's reader thread* took when the notification arrived, not
    the moment this loop got round to writing it — a recording is evidence about
    when the duck said something, and a drain-time stamp would smear a 50 Hz
    stream across the recorder's own poll interval. For a polled record
    (``health``, ``remote``, ``hello``) there is no earlier reading, so it is the
    write time. Monotonic, not wall clock: a clock step mid-run must not make a
    frame appear to arrive before its predecessor. Only differences between
    ``ts`` values are meaningful.
``source``
    One of :data:`RECORD_SOURCES` — ``"state"`` (a ``robot.state`` frame from
    the subscription), ``"health"`` (``robot.health``, polled at 2 Hz),
    ``"pad"`` (``pad.report``), ``"tof"`` (``tof.frame``), ``"remote"``
    (``robot.remoteSessionActive``, polled at 1 Hz) or ``"hello"`` (one record
    at the head of every recording, carrying the daemon's handshake).
``params``
    The notification params / request result verbatim, unmodified. Nothing is
    renamed, rounded or dropped: the recording is evidence, and a recorder that
    edits its input is a recorder nobody can trust.

Records appear in **arrival order** — the order this process observed them,
which is what a replay must reproduce. That is why every streamed source is
drained from :meth:`~microduck_cli.ipc.client.RobotClient.notifications`, the
client's arrival-ordered queue, rather than from its peek slots: a slot keeps
only the latest frame per method, so sampling slots at any rate below the
stream's would coalesce frames and reorder sources against each other.

One recording, three links
--------------------------
``robotd`` does not serve every stream. ``pad.input`` belongs to ``padd`` and
``tof.stream`` to ``tofd``, each on its own socket, so the recorder opens a
separate client per source it can reach (see
:mod:`microduck_cli.duck.addressing`) and **never** asks the robot socket for
either. A source with no socket on this box — the pad on a sim host, the ToF on
a duck without one — is a fact about the robot, recorded as a named
``record-source-absent`` drop on stderr, not a reason to abandon the recording.

**Stdout carries JSONL and nothing else.** Every diagnostic (a refused
subscription, a failed poll, the closing summary) goes to stderr through
:mod:`microduck_cli.behavior.senselog`. A single stray human line on stdout
makes the whole file unparseable, so nothing in this module writes prose to the
record stream.

Schema ownership: :data:`RECORD_SCHEMA` is imported from
``microduck_cli.behavior.replay`` when that module exists (the replay reader
owns the shape it reads), and defined here otherwise, so the two halves cannot
drift apart.
"""

from __future__ import annotations

import dataclasses
import json
import queue
import time
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any, Callable, Mapping, TextIO

from microduck_cli.behavior import senselog
from microduck_cli.ipc import proto
from microduck_cli.ipc.client import RobotClient, RpcError

#: The three keys every record carries, in write order.
RECORD_KEYS: tuple[str, ...] = ("ts", "source", "params")

#: Every legal ``source`` tag.
RECORD_SOURCES: tuple[str, ...] = ("state", "health", "pad", "tof", "remote", "hello")

#: ``source`` -> the wire method the record came from. ``"hello"`` is the
#: handshake result the client already holds, not a fresh call.
SOURCE_METHODS: Mapping[str, str] = MappingProxyType(
    {
        "state": proto.ROBOT_STATE,
        "health": proto.ROBOT_HEALTH,
        "pad": proto.PAD_REPORT,
        "tof": proto.TOF_FRAME,
        "remote": proto.ROBOT_SESSION_ACTIVE,
        "hello": proto.HELLO,
    }
)

#: Notification method -> the ``source`` tag its records carry. The inverse of the
#: streamed half of :data:`SOURCE_METHODS`; anything else off the wire is not a
#: recordable source.
NOTIFICATION_SOURCES: Mapping[str, str] = MappingProxyType(
    {
        proto.ROBOT_STATE: "state",
        proto.PAD_REPORT: "pad",
        proto.TOF_FRAME: "tof",
    }
)

#: How often ``robot.health`` is polled. Health is a request, not a stream.
HEALTH_POLL_HZ = 2.0

#: How often ``robot.remoteSessionActive`` is polled.
REMOTE_POLL_HZ = 1.0

#: The subscription rate asked of the daemon for ``robot.state``.
DEFAULT_STATE_HZ = 50

#: How long the loop sleeps between drains. Nothing is lost by looking less often
#: — the client's notification queue holds arrivals in order — so this only bounds
#: how promptly a frame reaches the file, and keeps the loop from spinning.
POLL_SLEEP_S = 0.005

# Named drop reasons, on the ``microduck.sense`` logger (stderr only).
DROP_SUBSCRIBE = "record-subscribe-failed"
DROP_POLL = "record-poll-failed"
#: A stream this recorder wanted has no socket on this box (no padd in the sim, no
#: tofd on a duck without a depth sensor). Named, so an empty column in a recording
#: is never mistaken for a quiet sensor.
DROP_SOURCE_ABSENT = "record-source-absent"
#: A notification arrived that no ``source`` tag covers — a daemon pushing something
#: this recorder's schema has no column for.
DROP_UNKNOWN_METHOD = "record-unknown-method"
_STAGE = "record"


def _schema_from_replay() -> Mapping[str, Any] | None:
    """The replay reader's schema, when that module exists (it owns the shape)."""
    try:
        from microduck_cli.behavior.replay import RECORD_SCHEMA as _replay_params
    except ImportError:
        return None
    # The replay reader publishes the per-source PARAM paths it reads; the record
    # contract wraps that map under the line-level keys so both halves agree.
    return MappingProxyType(
        {
            "format": "jsonl",
            "keys": RECORD_KEYS,
            "sources": RECORD_SOURCES,
            "ts": "monotonic seconds (float), read when the record is written",
            "order": "arrival",
            "params": _replay_params,
        }
    )


#: The recording contract, as data — for tests, for ``explain``, and for the
#: replay reader to agree with. See the module docstring for the prose.
RECORD_SCHEMA: Mapping[str, Any] = _schema_from_replay() or MappingProxyType(
    {
        "format": "jsonl",
        "keys": RECORD_KEYS,
        "sources": RECORD_SOURCES,
        "ts": "monotonic seconds (float), read when the record is written",
        "order": "arrival",
    }
)


@dataclass(frozen=True)
class RecordSummary:
    """What a finished recording contains. Reported on stderr, never on stdout."""

    seconds: float
    records: int
    destination: str
    by_source: dict[str, int] = field(default_factory=dict)

    def to_dict(self) -> dict[str, object]:
        return {
            "seconds": round(self.seconds, 3),
            "records": self.records,
            "destination": self.destination,
            "by_source": dict(self.by_source),
        }

    def as_text(self) -> str:
        counts = ", ".join(f"{src}={n}" for src, n in sorted(self.by_source.items())) or "none"
        return (
            f"recorded {self.records} records over {self.seconds:.2f}s "
            f"to {self.destination} ({counts})"
        )


def encode(ts: float, source: str, params: Any) -> str:
    """Render one record as its JSONL line (no trailing newline).

    Raises ``ValueError`` for a source outside :data:`RECORD_SOURCES` — an
    unrecognised tag would silently produce a recording the replay reader
    cannot classify.
    """
    if source not in RECORD_SOURCES:
        raise ValueError(f"unknown record source {source!r}; expected one of {RECORD_SOURCES}")
    return json.dumps({"ts": float(ts), "source": source, "params": params}, ensure_ascii=False)


class Recorder:
    """Drains one duck's links into JSONL.

    ``client`` is the ``robotd`` link — ``robot.state``, ``robot.health``,
    ``robot.remoteSessionActive`` and the handshake. ``pad_client`` and
    ``tof_client`` are the links to ``padd`` and ``tofd``, each on its **own**
    socket; pass ``None`` for a source this box does not serve and its absence is
    recorded as a named drop. The robot link is never asked for ``pad.input`` or
    ``tof.stream``: those methods do not live there.

    ``clock`` and ``sleep`` are injected so a test can drive the loop without
    real time; the default is ``time.monotonic`` / ``time.sleep``. The stream is
    resolved once at construction and only ever receives JSONL lines.
    """

    def __init__(
        self,
        client: RobotClient,
        stream: TextIO,
        *,
        pad_client: RobotClient | None = None,
        tof_client: RobotClient | None = None,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
        state_hz: int = DEFAULT_STATE_HZ,
        health_hz: float = HEALTH_POLL_HZ,
        remote_hz: float = REMOTE_POLL_HZ,
        poll_s: float = POLL_SLEEP_S,
        destination: str = "-",
    ) -> None:
        self._client = client
        self._pad_client = pad_client
        self._tof_client = tof_client
        self._stream = stream
        self._clock = clock
        self._sleep = sleep
        self._state_hz = int(state_hz)
        self._health_period = 1.0 / health_hz if health_hz > 0 else None
        self._remote_period = 1.0 / remote_hz if remote_hz > 0 else None
        self._poll_s = poll_s
        self._destination = destination
        self._counts: dict[str, int] = {}
        self._total = 0
        # Subscribe to every link's arrival-ordered notification queue *before*
        # anything is asked for on the wire, so no frame can land in the gap
        # between subscribing and starting to listen.
        self._queues: list[queue.Queue[tuple[float, str, Any]]] = [
            link.notifications()
            for link in (self._client, self._pad_client, self._tof_client)
            if link is not None
        ]

    # -- writing ------------------------------------------------------------

    def write(self, source: str, params: Any, ts: float | None = None) -> None:
        """Write one record, at ``ts`` or the current clock reading. JSONL only."""
        self._stream.write(encode(self._clock() if ts is None else ts, source, params) + "\n")
        self._stream.flush()
        self._counts[source] = self._counts.get(source, 0) + 1
        self._total += 1

    # -- setup --------------------------------------------------------------

    def _subscribe(self) -> None:
        """Open every stream this recorder wants, each on its own daemon's socket.

        Three failures, three named drops, none of them fatal: a daemon that
        refuses a subscription, a source with no socket on this box, and (later,
        in the loop) a queue that overran are all *recordable facts about the
        robot*, not reasons to abandon the recording.
        """
        try:
            self._client.subscribe(self._state_hz)
        except RpcError as exc:
            senselog.drop(_STAGE, "robotd", DROP_SUBSCRIBE, f"{proto.ROBOT_SUBSCRIBE}: {exc}")
        for label, link, method, served_by in (
            ("pad", self._pad_client, proto.PAD_INPUT, "padd (DUCK_PAD_SOCKET, /run/padd)"),
            ("tof", self._tof_client, proto.TOF_STREAM, "tofd (<state>/<duck>-tof.sock)"),
        ):
            if link is None:
                senselog.drop(
                    _STAGE,
                    label,
                    DROP_SOURCE_ABSENT,
                    f"no link to {served_by}: {method} is not recorded",
                )
                continue
            call = link.subscribe_pad if label == "pad" else link.subscribe_tof
            try:
                call()
            except RpcError as exc:
                senselog.drop(_STAGE, label, DROP_SUBSCRIBE, f"{label} stream unavailable: {exc}")

    def _record_hello(self) -> None:
        self.write("hello", dataclasses.asdict(self._client.daemon))

    # -- the loop -----------------------------------------------------------

    def _drain_notifications(self) -> None:
        """Write every notification each link has queued, in the order it arrived.

        Frames carry the reader thread's own stamp, so nothing is coalesced by how
        often this loop looks and nothing is reordered within a source. The peek
        slots are left alone — they belong to the engine's tick, not to a
        recording.
        """
        for pending in self._queues:
            while True:
                try:
                    stamp, method, params = pending.get_nowait()
                except queue.Empty:
                    break
                source = NOTIFICATION_SOURCES.get(method)
                if source is None:
                    senselog.drop(_STAGE, "robotd", DROP_UNKNOWN_METHOD, f"{method} not recorded")
                    continue
                self.write(source, params, ts=stamp)

    def _poll(self, source: str, call: Callable[[], Any]) -> None:
        result = call()
        if result is None:
            senselog.drop(_STAGE, "robotd", DROP_POLL, f"{SOURCE_METHODS[source]} answered nothing")
            return
        self.write(source, result)

    def run(self, seconds: float) -> RecordSummary:
        """Record for *seconds*, then return the summary. Never writes prose to the stream.

        ``KeyboardInterrupt`` is the operator's own stop: the loop unwinds and
        the summary reports what was captured, so a Ctrl-C'd recording is still
        a valid file.
        """
        started = self._clock()
        self._record_hello()
        self._subscribe()
        next_health = started
        next_remote = started
        try:
            while True:
                now = self._clock()
                if now - started >= seconds:
                    break
                self._drain_notifications()
                if self._health_period is not None and now >= next_health:
                    next_health = now + self._health_period
                    self._poll("health", self._client.poll_health)
                if self._remote_period is not None and now >= next_remote:
                    next_remote = now + self._remote_period
                    self._poll("remote", self._client.poll_remote_session)
                self._sleep(self._poll_s)
        except KeyboardInterrupt:
            senselog.stage(_STAGE, "operator", "interrupted", "Ctrl-C: closing the recording")
        return RecordSummary(
            seconds=self._clock() - started,
            records=self._total,
            destination=self._destination,
            by_source=dict(self._counts),
        )


__all__ = [
    "DEFAULT_STATE_HZ",
    "DROP_POLL",
    "DROP_SOURCE_ABSENT",
    "DROP_SUBSCRIBE",
    "DROP_UNKNOWN_METHOD",
    "HEALTH_POLL_HZ",
    "NOTIFICATION_SOURCES",
    "POLL_SLEEP_S",
    "RECORD_KEYS",
    "RECORD_SCHEMA",
    "RECORD_SOURCES",
    "REMOTE_POLL_HZ",
    "SOURCE_METHODS",
    "RecordSummary",
    "Recorder",
    "encode",
]
