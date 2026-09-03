"""The human-driving gate — when a person is at the controls, the engine shuts up.

robotd arbitrates nothing between its clients: a gamepad frame and an engine's
``robot.move`` are the same kind of message arriving on the same socket, and the
last one written wins. So a person driving with the pad while a behaviour streams
twist at 50 Hz is not "shared control", it is two authors fighting, and the human
loses roughly half the frames.

This gate is the answer. While a human is driving — a ``pad.report`` seen within
:data:`DEFAULT_PAD_RECENT_S`, an explicit ``pad_active``, or
``robot.remoteSessionActive`` — every MOTION channel the engine composed is
withheld:

* withheld: ``twist``, ``head`` (and therefore ``look``), ``pose``, ``skill``,
  ``mode`` and **``stop``**;
* passed: ``sound`` and ``mouth``.

**``stop`` is withheld too**, which is the one choice here worth arguing about
(decision q4, option B). A stop looks like the safe thing to force through — but
the person holding the pad can already stop the duck, instantly and without
asking, and an engine that overrides a human's input *for their own good* is
exactly the behaviour that makes a robot untrustworthy to stand next to. The
engine never overrides a person. Expression is left alone for the same reason
from the other side: a chirp or a beak movement contends for nothing the pad is
driving, so silencing it would cost the duck its liveliness and buy nothing.

**Rules keep running.** The gate withholds *output*, not perception: sense is
read, rules are evaluated, behaviours are admitted, expired and recorded exactly
as they would be otherwise. The engine keeps a truthful picture of what it wanted
to do, which is what makes a JSONL recording of a human-driven session worth
anything.

**One line per transition, a counter per tick.** A gate that logged every
withheld tick would emit fifty lines a second and bury every other drop in the
file. So the *transitions* are ``stage`` events (``human-driving-start`` /
``human-driving-end``, with the reason) and the per-tick withholding accumulates
on :attr:`HumanGate.withheld` under the ``human-driving`` name — visible, counted
and greppable, without the flood.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from typing import Any, Callable, Iterable

from microduck_cli.behavior import senselog
from microduck_cli.behavior.sense import EMPTY_SENSE, Sense

#: ``senselog`` stage token for gate events.
STAGE = "gate"

#: The named drop reason. One name, used by the gate, the sink wrapper and the
#: idle behaviour, so ``grep 'event=human-driving'`` finds every one of them.
DROP_HUMAN_DRIVING = "human-driving"

#: Stage events for the two edges.
EVENT_START = "human-driving-start"
EVENT_END = "human-driving-end"

#: How recent a ``pad.report`` must be to mean "a person is driving right now".
#: The pad streams while it is touched, so this is a silence timeout, not a
#: sample rate: half a second is long enough to bridge the gap between frames and
#: short enough that the engine takes over promptly once the pad is put down.
DEFAULT_PAD_RECENT_S = 0.5

#: Channels the gate withholds while a human drives. ``stop`` and ``mode`` are
#: not :data:`~microduck_cli.behavior.model.CHANNELS`; they are the discrete keys
#: :mod:`~microduck_cli.behavior.sink` also accepts in a pose dict, and they are
#: named here so a caller cannot route around the gate by using them.
WITHHELD_CHANNELS: frozenset[str] = frozenset({"twist", "head", "pose", "skill", "mode", "stop"})

#: Channels that pass through untouched. Kept as an explicit set rather than
#: "everything not withheld" so a NEW channel is withheld by default: the safe
#: side of the decision is silence, not a fresh way to fight the pad.
PASSED_CHANNELS: frozenset[str] = frozenset({"sound", "mouth"})

#: Why the gate is closed, most specific first.
REASON_PAD_ACTIVE = "pad-active"
REASON_PAD_RECENT = "pad-recent"
REASON_REMOTE_SESSION = "remote-session"


@dataclass(frozen=True)
class GateVerdict:
    """Whether a human is driving, and what said so.

    ``driving`` is the decision; ``reason`` names the evidence (``None`` when the
    gate is open). ``age_s`` echoes the pad age the decision saw, so a log line or
    a ``--json`` payload can show *how* recent "recent" was.
    """

    driving: bool
    reason: str | None = None
    age_s: float | None = None


#: The verdict for a snapshot with no evidence of a human anywhere.
OPEN = GateVerdict(driving=False)


class HumanGate:
    """Judges "is a person driving?" from a :class:`~microduck_cli.behavior.sense.Sense`.

    Stateless in its judgement (:meth:`judge` is pure) and stateful only in what
    it has *reported*: :meth:`update` remembers the last verdict so the two edges
    can be logged once each instead of once per tick.

    :param pad_recent_s: pad silence timeout; see :data:`DEFAULT_PAD_RECENT_S`.
    """

    def __init__(self, pad_recent_s: float = DEFAULT_PAD_RECENT_S) -> None:
        self.pad_recent_s = pad_recent_s
        self._verdict: GateVerdict = OPEN
        #: Per-channel withheld counts under :data:`DROP_HUMAN_DRIVING`, plus the
        #: total under that same name.
        self.withheld: Counter[str] = Counter()
        #: How many times the gate closed over this process's life.
        self.transitions = 0

    # -- judgement ---------------------------------------------------------

    def judge(self, sense: Sense = EMPTY_SENSE) -> GateVerdict:
        """Is a human driving, per *sense*? Pure: nothing is logged or remembered.

        ``pad_active`` is believed when it is a real reading — ``True`` closes the
        gate, and ``False`` is a positive "the pad is not being touched" that a
        stale timestamp must not override. ``None`` means no reading at all, and
        the pad's own freshness (``pad_age_s``) decides instead: a report that
        landed within :attr:`pad_recent_s` is somebody's thumb on a stick. A
        remote session closes the gate regardless of the pad.
        """
        age = sense.pad_age_s
        if sense.pad_active is True:
            return GateVerdict(True, REASON_PAD_ACTIVE, age)
        if sense.pad_active is None and age is not None and age <= self.pad_recent_s:
            return GateVerdict(True, REASON_PAD_RECENT, age)
        if sense.remote_session is True:
            return GateVerdict(True, REASON_REMOTE_SESSION, age)
        return GateVerdict(False, None, age)

    @property
    def verdict(self) -> GateVerdict:
        """The last verdict :meth:`update` produced (open until the first call)."""
        return self._verdict

    @property
    def active(self) -> bool:
        """Is the gate currently closed? Reads the last :meth:`update`."""
        return self._verdict.driving

    def update(self, sense: Sense = EMPTY_SENSE) -> GateVerdict:
        """Judge *sense*, log the EDGES only, and remember the verdict."""
        verdict = self.judge(sense)
        if verdict.driving != self._verdict.driving:
            if verdict.driving:
                self.transitions += 1
                senselog.stage(
                    STAGE, verdict.reason or "human", EVENT_START, "withholding motion channels"
                )
            else:
                senselog.stage(
                    STAGE,
                    self._verdict.reason or "human",
                    EVENT_END,
                    f"engine resumes; withheld {self.withheld[DROP_HUMAN_DRIVING]} tick(s) total",
                )
        self._verdict = verdict
        return verdict

    # -- filtering ---------------------------------------------------------

    def filter_pose(self, pose: dict[str, Any]) -> dict[str, Any]:
        """The pose with every withheld channel removed, counting what it dropped.

        Counts land on :attr:`withheld`: one per channel name, plus one per TICK
        under :data:`DROP_HUMAN_DRIVING` (not one per channel — "how many ticks
        did the engine stay quiet" is the number an operator is asking for).
        """
        if not self._verdict.driving:
            return pose
        kept = {name: value for name, value in pose.items() if name not in WITHHELD_CHANNELS}
        dropped = [name for name in pose if name in WITHHELD_CHANNELS]
        if dropped:
            for name in dropped:
                self.withheld[name] += 1
            self.withheld[DROP_HUMAN_DRIVING] += 1
        return kept

    def driver(self) -> Callable[[Any], None]:
        """A :class:`~microduck_cli.behavior.engine.TickBus` driver that calls
        :meth:`update` with each tick's sense.

        Use it when the gate is *observed* (a JSONL recording of when a human took
        over). To make the gate actually withhold, wrap the sink in
        :class:`GatedSink` — the seam runs AFTER the write, so a driver alone
        would always be one tick behind the pose it meant to stop.
        """

        def _drive(ctx: Any) -> None:
            self.update(getattr(ctx, "sense", EMPTY_SENSE))

        _drive.name = "human-gate"  # type: ignore[attr-defined]
        return _drive

    def snapshot(self) -> dict[str, object]:
        """A plain-dict readout for ``--json`` or a ``state.json`` heartbeat."""
        return {
            "driving": self._verdict.driving,
            "reason": self._verdict.reason,
            "pad_age_s": self._verdict.age_s,
            "pad_recent_s": self.pad_recent_s,
            "transitions": self.transitions,
            "withheld": dict(self.withheld),
        }


class GatedSink:
    """A :class:`~microduck_cli.behavior.engine.TargetSink` wrapper that enforces the gate.

    Wrap the real sink::

        sink = GatedSink(RobotSink(client), gate, sense=lambda: read_sense(providers, clock()))

    ``sense`` is a zero-arg peek returning the CURRENT snapshot. It exists because
    the sink protocol is ``write(pose)`` and nothing else: the gate needs the
    reading the tick was composed from, and taking it here — before the write —
    is what keeps the decision on the same tick as the pose it withholds. Omit it
    and the wrapper uses whatever verdict the gate last :meth:`HumanGate.update`
    produced (a ``TickBus`` driver, one tick behind).

    A fully-withheld tick writes NOTHING to the inner sink: not an empty pose, not
    a zeroed one. Sending zeros would be the engine driving too.
    """

    def __init__(
        self,
        inner: Any,
        gate: HumanGate | None = None,
        *,
        sense: Callable[[], Sense] | None = None,
    ) -> None:
        self.inner = inner
        self.gate = gate if gate is not None else HumanGate()
        self._sense = sense

    def write(self, pose: dict[str, object]) -> None:
        """Filter, then delegate. Never raises for a gate decision."""
        if self._sense is not None:
            self.gate.update(_peek_sense(self._sense))
        kept = self.gate.filter_pose(dict(pose))
        if not kept:
            return
        self.inner.write(kept)

    def __getattr__(self, name: str) -> Any:
        """Delegate everything else (``drops``, ``close``, ``sent``) to the inner sink."""
        return getattr(self.inner, name)


def _peek_sense(source: Callable[[], Sense]) -> Sense:
    """Read the sense peek, degrading a failure to "nothing sensed".

    Same contract as :func:`~microduck_cli.behavior.sense.read_sense`: a broken
    provider must not be able to kill a write. It DOES mean a broken peek opens
    the gate, which is the honest reading — with no evidence of a human we cannot
    claim one is there — and the engine's own drops stay loud either way.
    """
    try:
        return source()
    except Exception:
        return EMPTY_SENSE


def withheld_channels(pose: Iterable[str]) -> tuple[str, ...]:
    """The channels of *pose* the gate would withhold, sorted. For tests and docs."""
    return tuple(sorted(name for name in pose if name in WITHHELD_CHANNELS))


__all__ = [
    "DEFAULT_PAD_RECENT_S",
    "DROP_HUMAN_DRIVING",
    "EVENT_END",
    "EVENT_START",
    "OPEN",
    "PASSED_CHANNELS",
    "REASON_PAD_ACTIVE",
    "REASON_PAD_RECENT",
    "REASON_REMOTE_SESSION",
    "STAGE",
    "WITHHELD_CHANNELS",
    "GateVerdict",
    "GatedSink",
    "HumanGate",
    "withheld_channels",
]
