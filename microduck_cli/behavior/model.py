"""The pure behaviour model — channels, contention classes, lifetimes, arbitration.

No I/O, no transport, no CLI: every type here is a plain value object and every
function is pure, so the contention core is trivially unit-testable and stays
usable from the engine, from a dry-run planner, and from a test with equal ease.
The only sibling import is :mod:`microduck_cli.behavior.sense`, itself a
stdlib-only leaf.

A :class:`Behavior` pairs an immutable :class:`BehaviorSpec` — which channels it
claims, how it contends, how long it lives — with a contribution function that
maps behaviour-local time and the latest :class:`~microduck_cli.behavior.sense.Sense`
to a per-channel dict of desired values. Most behaviours are *pure*: they ignore
``sense`` and return the same values for the same local time, so motion is
reproducible regardless of when the behaviour was admitted.
"""

from __future__ import annotations

import enum
import math
from dataclasses import dataclass, field
from typing import Callable

from microduck_cli.behavior.sense import EMPTY_SENSE, Sense

# The duck's intent families, and the units of arbitration. Each channel is
# resolved atomically to a single owner per tick, and they mirror the intent
# families robotd exposes:
#
#   twist  -> robot.move            (planar body velocity)
#   head   -> robot.head / look     (head aim)
#   pose   -> robot.pose            (whole-body posture)
#   mouth  -> robot.mouth           (beak)
#   sound  -> robot.sound           (audio out)
#   skill  -> robot.do              (a named canned skill)
#
# This tuple is the single source of truth: arbitration and composition iterate
# it and never the literals, so splitting or renaming a channel stays local.
CHANNELS = ("twist", "head", "pose", "mouth", "sound", "skill")


class StopClass(enum.Enum):
    """How a behaviour contends for the channels it claims.

    Ordered by :attr:`priority`: the higher-priority claimant owns a contested
    channel at tick time, and on admission the classes decide whether a newcomer
    evicts, is refused, or simply waits its turn.
    """

    PASSIVE = "passive"
    STOPPABLE = "stoppable"
    STOPPING = "stopping"
    UNSTOPPABLE = "unstoppable"

    @property
    def priority(self) -> int:
        """Tick-time rank; higher wins a contested channel."""
        return _PRIORITY[self]


# UNSTOPPABLE and STOPPING both hold a channel against a newcomer; UNSTOPPABLE
# ranks highest so it also wins a same-tick contest. STOPPABLE drives but yields,
# and PASSIVE only fills a channel nobody else claims.
_PRIORITY: dict[StopClass, int] = {
    StopClass.PASSIVE: 0,
    StopClass.STOPPABLE: 1,
    StopClass.STOPPING: 2,
    StopClass.UNSTOPPABLE: 3,
}

#: Classes that BLOCK admission of a newcomer on a channel they hold. A duck
#: mid-``stopping`` (bringing itself to a halt) or mid-``unstoppable`` (a skill
#: that must finish, e.g. a stand-up) must not be interrupted, so a newcomer
#: claiming one of those channels is refused rather than silently outranked.
#: STOPPABLE is deliberately absent: it is the polite default and yields.
BLOCKING_CLASSES = frozenset({StopClass.UNSTOPPABLE, StopClass.STOPPING})


@dataclass(frozen=True)
class Lifetime:
    """How long a behaviour runs.

    * one-shot (``looping=False``) — runs once for ``duration`` seconds and then
      expires; ``duration`` is required and must be finite and > 0;
    * looping (``looping=True``) — repeats until ``duration`` seconds elapse, or
      forever when ``duration is None``, until stopped or evicted.
    """

    duration: float | None = None
    looping: bool = False

    def errors(self) -> list[str]:
        """Human-readable validity problems; an empty list means valid."""
        problems: list[str] = []
        if self.duration is not None:
            if not isinstance(self.duration, (int, float)) or isinstance(self.duration, bool):
                problems.append("duration must be a number")
            elif not math.isfinite(self.duration):
                problems.append("duration must be a finite number")
            elif self.duration <= 0:
                problems.append("duration must be > 0")
        elif not self.looping:
            problems.append("a one-shot behavior needs a duration")
        return problems

    def is_expired(self, t_local: float) -> bool:
        """True once a finite lifetime has elapsed (looping-forever never expires)."""
        return self.duration is not None and t_local >= self.duration


@dataclass(frozen=True)
class BehaviorSpec:
    """The immutable half of a behaviour: identity and contention shape.

    ``channels`` is what this behaviour claims — arbitration resolves each of
    them independently, so a behaviour claiming ``head`` and ``sound`` may well
    own one and not the other on the same tick.
    """

    name: str
    channels: frozenset[str]
    stop_class: StopClass = StopClass.STOPPABLE
    lifetime: Lifetime = Lifetime(looping=True)

    def errors(self) -> list[str]:
        """Validity problems: an unknown channel, an empty claim, a bad lifetime."""
        problems: list[str] = []
        if not self.name:
            problems.append("name must not be empty")
        if not self.channels:
            problems.append("a behavior must claim at least one channel")
        for channel in sorted(self.channels):
            if channel not in CHANNELS:
                problems.append(f"unknown channel {channel!r}: use one of {', '.join(CHANNELS)}")
        problems.extend(self.lifetime.errors())
        return problems


#: A contribution is ``{channel: value}`` for this instant. A channel the
#: behaviour claims but omits (or maps to ``None``) is an ABSTENTION for the
#: tick: arbitration falls that channel through to the next-priority claimant
#: rather than freezing it, and the behaviour itself stays alive. Channels the
#: behaviour does not claim are ignored even if present.
Contribution = dict[str, object]

#: ``fn(t_local, params, sense) -> Contribution``.
ContributeFn = Callable[[float, dict, Sense], Contribution]


@dataclass(frozen=True)
class Behavior:
    """A live behaviour: a spec, its parameters, and its contribution function.

    ``id`` is assigned by the engine (``"look-3"``); ``spec.name`` is the library
    entry it came from. ``wants_sense`` marks the sensor-driven exception to
    purity: only those behaviours are fed the live snapshot, so a behaviour
    cannot become sensor-dependent by accident just because something else is
    sensing.
    """

    id: str
    spec: BehaviorSpec
    fn: ContributeFn = field(repr=False, compare=False)
    params: dict = field(default_factory=dict)
    wants_sense: bool = False

    @property
    def name(self) -> str:
        return self.spec.name

    @property
    def channels(self) -> frozenset[str]:
        return self.spec.channels

    @property
    def stop_class(self) -> StopClass:
        return self.spec.stop_class

    @property
    def lifetime(self) -> Lifetime:
        return self.spec.lifetime

    def contribute(self, t_local: float, sense: Sense = EMPTY_SENSE) -> Contribution:
        """Desired per-channel values at ``t_local`` seconds since this started.

        A behaviour that did not ask for sense is handed
        :data:`~microduck_cli.behavior.sense.EMPTY_SENSE` regardless of what the
        caller passed, which is what keeps purity checkable.
        """
        result = self.fn(t_local, self.params, sense if self.wants_sense else EMPTY_SENSE)
        return result if isinstance(result, dict) else {}

    def is_expired(self, t_local: float) -> bool:
        """True once this behaviour's finite lifetime has elapsed."""
        return self.spec.lifetime.is_expired(t_local)


def arbitrate(
    behaviors: list[Behavior], contribs: dict[str, Contribution] | None = None
) -> dict[str, Behavior | None]:
    """Assign every channel its owner. ``behaviors`` is oldest-first.

    The owner of a channel is the claimant with the highest
    :attr:`StopClass.priority`, ties broken by most-recently-admitted. A channel
    nobody claims maps to ``None``, and a PASSIVE behaviour therefore owns a
    channel only when no non-passive behaviour claims it.

    Pass ``contribs`` (this tick's ``{behavior_id: Contribution}``) to make the
    result abstention-aware: a claimant that omits a channel this tick is
    skipped for it, so the channel falls through to the next claimant instead of
    being held frozen by a behaviour with nothing to say.
    """
    owners: dict[str, Behavior | None] = dict.fromkeys(CHANNELS)
    indexed = list(enumerate(behaviors))
    for channel in CHANNELS:
        candidates = [(i, b) for i, b in indexed if channel in b.channels]
        if contribs is not None:
            candidates = [
                (i, b)
                for i, b in candidates
                if isinstance(contribs.get(b.id), dict) and contribs[b.id].get(channel) is not None
            ]
        if not candidates:
            continue
        _, best = max(candidates, key=lambda pair: (pair[1].stop_class.priority, pair[0]))
        owners[channel] = best
    return owners


@dataclass(frozen=True)
class AdmitResult:
    """The outcome of offering a behaviour to a live set.

    ``admitted`` is the decision. ``evicted`` are the STOPPABLE incumbents a
    STOPPING newcomer removed. ``blocked`` names the newcomer's channels it will
    not own — the reason for a refusal, and merely informational on an accepted
    newcomer that must wait its turn. ``reason`` is a short machine-readable
    token (``"blocked"``) or ``None`` when admitted.
    """

    admitted: bool
    evicted: tuple[Behavior, ...] = ()
    blocked: tuple[str, ...] = ()
    reason: str | None = None


def admit(new: Behavior, behaviors: list[Behavior]) -> AdmitResult:
    """Decide whether ``new`` joins the live set, and what joining removes.

    * A PASSIVE newcomer is always admitted and removes nothing: it fills only
      unclaimed channels anyway, so it cannot disturb an incumbent.
    * Any other newcomer sharing a channel with an incumbent in
      :data:`BLOCKING_CLASSES` is REFUSED — a duck that is stopping itself or
      running an uninterruptible skill finishes first. The contested channels
      come back in ``blocked``.
    * Otherwise the newcomer is admitted; a STOPPING newcomer evicts the
      STOPPABLE incumbents it shares a channel with, and anything left that
      still outranks it on a channel simply keeps that channel until it ends
      (reported in ``blocked``).

    Pure: nothing here mutates ``behaviors``; the caller applies the outcome.
    """
    if new.stop_class is StopClass.PASSIVE:
        return AdmitResult(admitted=True)

    blockers = sorted(
        {
            channel
            for b in behaviors
            if b.stop_class in BLOCKING_CLASSES
            for channel in (b.channels & new.channels)
        }
    )
    if blockers:
        return AdmitResult(admitted=False, blocked=tuple(blockers), reason="blocked")

    evicted: tuple[Behavior, ...] = ()
    if new.stop_class is StopClass.STOPPING:
        evicted = tuple(
            b
            for b in behaviors
            if b.stop_class is StopClass.STOPPABLE and (b.channels & new.channels)
        )

    evicted_ids = {b.id for b in evicted}
    remaining = [b for b in behaviors if b.id not in evicted_ids]
    # `new` goes last so it wins same-priority ties, matching how the engine will
    # order the live set once it appends.
    owners = arbitrate([*remaining, new])
    blocked = tuple(
        sorted(
            channel
            for channel in new.channels
            if owners[channel] is None or owners[channel].id != new.id
        )
    )
    return AdmitResult(admitted=True, evicted=evicted, blocked=blocked)
