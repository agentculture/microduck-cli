"""The idle base — the duck looks alive when nothing else is asking it to do anything.

A robot that holds perfectly still between commands reads as *off*, and an
operator who cannot tell "idle" from "crashed" learns to distrust the whole
stack. So the resting layer is small, slow head motion plus an occasional chirp:
enough to say "I am here", little enough that nobody mistakes it for a command.

It is :attr:`~microduck_cli.behavior.model.StopClass.PASSIVE` and that is the
whole of its contention story. PASSIVE owns a channel only when no other
behaviour claims it, so a look, a walk or a skill takes the head away the instant
it is admitted and hands it back when it ends — arbitration's job
(:func:`~microduck_cli.behavior.model.arbitrate`), not idle's. This module never
checks what else is running.

**Four silences, all of them abstentions rather than motion.** Idle contributes
NOTHING at all when:

* ``sense.fallen`` — a fallen duck wiggling its head is a robot that has not
  noticed it fell;
* ``sense.limp`` — nothing is holding the joints, so a command is a lie;
* ``sense.enabled is not True`` — note the shape: only a positive ``True``
  counts. ``None`` means *no reading*, and "we do not know whether the actuators
  are on" is not permission to move them;
* the human gate is closed — see :mod:`~microduck_cli.behavior.human_gate`. Idle
  is exactly the behaviour that would otherwise fight a person's pad input over
  the head channel forever, since it never ends on its own.

An abstention (an omitted channel) is not the same as a zero: composition drops
the channel entirely, the sink sends nothing for it, and the daemon's own last
value stands. Idle never asserts a neutral pose it was not asked for.

Donor: ``reachy/behavior/library.py``'s ``feel-alive`` entry — a PASSIVE, looping,
sum-of-unsynchronised-sinusoids idle. Re-implemented rather than copied: this duck
has a neck and a beak where that robot has antennas and a body yaw, and the
bounds below are the duck's own.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Callable

from microduck_cli.behavior import intents
from microduck_cli.behavior.human_gate import HumanGate
from microduck_cli.behavior.model import Behavior, BehaviorSpec, Lifetime, StopClass
from microduck_cli.behavior.sense import EMPTY_SENSE, Sense
from microduck_cli.cli._errors import EXIT_USER_ERROR, CliError

#: The library name and the intent kind. One string: the kind an operator types
#: and the behaviour it admits are the same thing.
NAME = intents.IDLE

#: The channels idle claims. ``head`` for the motion, ``sound`` for the chirp.
#: Not ``mouth``: opening the beak without a sound looks like the duck is trying
#: to speak, and idle has nothing to say.
CHANNELS: frozenset[str] = frozenset({"head", "sound"})

#: Neck-pitch nod amplitude, radians. Deliberately a small fraction of the joint's
#: real travel (:data:`~microduck_cli.behavior.intents.NECK_PITCH_MIN_RAD` ..
#: :data:`~microduck_cli.behavior.intents.NECK_PITCH_MAX_RAD`, i.e. -pi/2..+pi/3):
#: idle motion has to be visible from across a room and unmistakable for a
#: command, and ~4.6 degrees of nod is both.
NECK_PITCH_AMPLITUDE_RAD = 0.08

#: Head-yaw wander amplitude, radians (~7 degrees). NOT an upstream-derived
#: limit: the pinned protocol documents no ``head_yaw`` range, so this is a
#: conservative amplitude chosen to stay far inside any plausible one, and
#: :data:`HEAD_YAW_MAX_RAD` is the ceiling this module will accept for it.
HEAD_YAW_AMPLITUDE_RAD = 0.12

#: The ceiling on a configured yaw amplitude. Conservative for the same reason.
HEAD_YAW_MAX_RAD = 0.5

#: Cycle lengths, seconds. Mutually prime-ish on purpose: two sinusoids at 7 s and
#: 11 s never repeat the same combined pose within a minute, so the motion reads as
#: alive rather than as a loop.
NECK_PERIOD_S = 7.0
HEAD_YAW_PERIOD_S = 11.0

#: The floor on the gap between chirps. A duck that chirps every few seconds is
#: an alarm; once every half minute is company.
CHIRP_INTERVAL_S = 30.0

#: The chirp's voice-bank tag. In both
#: :data:`~microduck_cli.behavior.intents.SOUND_NAMES` and the daemon's
#: ``SoundTag`` enum.
CHIRP_TAG = "chirp"

#: The default knobs, all overridable through an intent payload's ``params``
#: (once a caller wires one) or by :func:`make_idle`'s keyword arguments.
DEFAULT_PARAMS: dict[str, Any] = {
    "neck_amplitude": NECK_PITCH_AMPLITUDE_RAD,
    "neck_period": NECK_PERIOD_S,
    "yaw_amplitude": HEAD_YAW_AMPLITUDE_RAD,
    "yaw_period": HEAD_YAW_PERIOD_S,
    "chirp_every": CHIRP_INTERVAL_S,
    "chirp_tag": CHIRP_TAG,
}


def _reject(message: str, remediation: str = "") -> None:
    raise CliError(code=EXIT_USER_ERROR, message=f"{NAME}: {message}", remediation=remediation)


def _positive(value: Any, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        _reject(f"{label} must be a number (got {value!r})")
    number = float(value)  # type: ignore[arg-type]
    if not math.isfinite(number) or number <= 0.0:
        _reject(f"{label} must be a finite number > 0 (got {value!r})")
    return number


def _amplitude(value: Any, label: str, ceiling: float) -> float:
    """A non-negative amplitude no larger than *ceiling*. Refused, never clamped."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        _reject(f"{label} must be a number (got {value!r})")
    number = float(value)  # type: ignore[arg-type]
    if not math.isfinite(number) or number < 0.0:
        _reject(f"{label} must be a finite number >= 0 (got {value!r})")
    if number > ceiling:
        _reject(
            f"{label} out of range: {number!r} (allowed [0, {ceiling}] rad)",
            remediation=f"idle motion must stay small: use at most {ceiling} rad",
        )
    return number


def resolve_params(overrides: dict[str, Any] | None = None) -> dict[str, Any]:
    """:data:`DEFAULT_PARAMS` with *overrides* applied and every knob checked.

    Fail-closed like :mod:`~microduck_cli.behavior.intents`: an unknown knob, a
    non-number, a negative period or an amplitude past its ceiling is REFUSED with
    a named reason. Nothing is clamped — a caller asking the resting layer to
    swing the neck 1.2 rad has a bug, and quietly giving them 0.5 hides it.
    """
    params = dict(DEFAULT_PARAMS)
    for key, value in (overrides or {}).items():
        if key not in DEFAULT_PARAMS:
            _reject(
                f"unknown idle knob {key!r} (allowed: {sorted(DEFAULT_PARAMS)})",
                remediation="drop the unknown knob and resubmit",
            )
        params[key] = value
    params["neck_amplitude"] = _amplitude(
        params["neck_amplitude"], "neck_amplitude", abs(intents.NECK_PITCH_MAX_RAD)
    )
    params["yaw_amplitude"] = _amplitude(params["yaw_amplitude"], "yaw_amplitude", HEAD_YAW_MAX_RAD)
    params["neck_period"] = _positive(params["neck_period"], "neck_period")
    params["yaw_period"] = _positive(params["yaw_period"], "yaw_period")
    params["chirp_every"] = _positive(params["chirp_every"], "chirp_every")
    tag = params["chirp_tag"]
    if tag is not None and tag not in intents.SOUND_NAMES:
        _reject(
            f"chirp_tag is unknown (got {tag!r})",
            remediation=f"use one of: {', '.join(sorted(intents.SOUND_NAMES))}",
        )
    return params


def is_silent(sense: Sense, gate: HumanGate | None = None) -> str | None:
    """Why idle must contribute nothing right now, or ``None`` when it may move.

    Returned as a NAMED reason rather than a bool so a caller (a test, a JSONL
    recording, a ``--json`` payload) can say *which* silence it is.
    """
    if sense.fallen:
        return "fallen"
    if sense.limp:
        return "limp"
    if sense.enabled is not True:
        return "not-enabled"
    if gate is not None and gate.judge(sense).driving:
        return "human-driving"
    return None


@dataclass
class _Chirp:
    """The one piece of state idle carries: when it last spoke, in local time."""

    every: float
    last: float = 0.0

    def due(self, t_local: float) -> bool:
        """Is a chirp due at *t_local*? Records it when it is.

        Monotonic in behaviour-local time, and self-healing if that time jumps
        backwards (a behaviour re-admitted with a fresh clock): a ``last`` in the
        future is reset rather than trusted, so the duck cannot go silent for
        hours because of one bad reading.
        """
        if t_local < self.last:
            self.last = t_local
            return False
        if t_local - self.last < self.every:
            return False
        self.last = t_local
        return True


def make_idle(
    gate: HumanGate | None = None, params: dict[str, Any] | None = None
) -> Callable[[float, dict, Sense], dict]:
    """Build idle's contribution function. Stateful — one per behaviour instance.

    The state is the chirp clock and nothing else; the head motion is a pure
    function of behaviour-local time, so two engines at the same tick produce the
    same pose. Pass a *gate* to make idle yield to a person at the pad (the
    composition root's job); with no gate, only the three sense silences apply.
    """
    resolved = resolve_params(params)
    chirp = _Chirp(every=resolved["chirp_every"])

    def contribute(t_local: float, _params: dict, sense: Sense = EMPTY_SENSE) -> dict:
        if is_silent(sense, gate) is not None:
            return {}
        out: dict[str, Any] = {
            "head": {
                "neck_pitch": resolved["neck_amplitude"]
                * math.sin(2.0 * math.pi * t_local / resolved["neck_period"]),
                "head_yaw": resolved["yaw_amplitude"]
                * math.sin(2.0 * math.pi * t_local / resolved["yaw_period"]),
            }
        }
        if resolved["chirp_tag"] is not None and chirp.due(t_local):
            # One tick's contribution, not a held state: ``robot.sound`` is a
            # notification, so exactly one frame goes out and the chirp is over.
            out["sound"] = {"name": resolved["chirp_tag"], "hold": False}
        return out

    return contribute


def idle_behavior(
    behavior_id: str = "idle-0",
    *,
    gate: HumanGate | None = None,
    params: dict[str, Any] | None = None,
    duration: float | None = None,
) -> Behavior:
    """One live idle :class:`~microduck_cli.behavior.model.Behavior`.

    Looping, PASSIVE, and ``wants_sense=True`` — the sense flag is load-bearing:
    without it the engine feeds
    :data:`~microduck_cli.behavior.sense.EMPTY_SENSE`, ``enabled`` reads ``None``,
    and idle would correctly refuse to ever move.
    """
    spec = BehaviorSpec(
        name=NAME,
        channels=CHANNELS,
        stop_class=StopClass.PASSIVE,
        lifetime=Lifetime(duration=duration, looping=True),
    )
    return Behavior(
        id=behavior_id,
        spec=spec,
        fn=make_idle(gate=gate, params=params),
        params=dict(params or {}),
        wants_sense=True,
    )


#: The registered ``idle`` kind from :mod:`~microduck_cli.behavior.intents` — the
#: source of the validator this module reuses. Reused rather than re-written so
#: the "one validator per kind" guarantee survives the registration swap below.
_IDLE_KIND = next(spec for spec in intents.DEFAULT_KINDS if spec.kind == intents.IDLE)

#: The one validator for an idle payload (no fields but the optional
#: ``duration_s``).
validate_idle = _IDLE_KIND.validator


def register(registry: intents.KindRegistry, gate: HumanGate | None = None):
    """Point *registry*'s ``idle`` kind at THIS behaviour; returns the registry.

    The registry ships a placeholder idle that passively claims ``pose`` and
    contributes an empty posture — a stand-in for exactly this module. Registering
    over it (the registry's documented "register a kind at composition time"
    path) is how the real resting layer arrives, and it keeps the same validator,
    so an ``idle`` payload is judged identically before and after.
    """

    def to_behavior(params: dict, behavior_id: str) -> Behavior:
        return idle_behavior(behavior_id, gate=gate, duration=params.get(intents.DURATION_FIELD))

    return registry.register(NAME, validate_idle, to_behavior)


__all__ = [
    "CHANNELS",
    "CHIRP_INTERVAL_S",
    "CHIRP_TAG",
    "DEFAULT_PARAMS",
    "HEAD_YAW_AMPLITUDE_RAD",
    "HEAD_YAW_MAX_RAD",
    "HEAD_YAW_PERIOD_S",
    "NAME",
    "NECK_PERIOD_S",
    "NECK_PITCH_AMPLITUDE_RAD",
    "idle_behavior",
    "is_silent",
    "make_idle",
    "register",
    "resolve_params",
    "validate_idle",
]
