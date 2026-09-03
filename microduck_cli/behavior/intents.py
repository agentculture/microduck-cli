"""Intents and the ONE admission registry — every path in, one validator.

An :class:`Intent` is a value object: a ``kind`` from
:data:`~microduck_cli.behavior.sense.ACTIONS`, a ``payload`` dict, and the
provenance of the submission (``origin`` — ``rule`` / ``cli`` / ``agent`` — plus
the ``rule_id`` when a rule fired it and the ``submitted_at`` stamp). Nothing
about an intent says how it reached us beyond that record, and nothing about how
it reached us changes how it is judged.

Why one registry
----------------
A rule firing and a human typing ``microduck rules intent move '{"vyaw": 9}'`` are
the same act: an under-specified request to move the robot. If the rule path had
its own validator, the two would drift, and the drift would be discovered by the
duck. So :class:`KindRegistry` owns **exactly one** :meth:`KindRegistry.validate`
entry point and **exactly one** :meth:`KindRegistry.admit`; the rule engine
(:mod:`microduck_cli.behavior.rule_engine`) calls the same
:meth:`~KindRegistry.admit` that :meth:`KindRegistry.inject` — the CLI/agent
convenience — calls. The obligation this module carries is that a rule-fired and
an injected intent with the same over-limit payload receive **byte-identical
refusal text**; that falls out of construction here, and ``tests/test_intents.py``
pins it.

Fail-closed, never clamped
--------------------------
The payload that reaches a validator did not come from a reviewed call site: it
came from a rules TOML an operator edited, or from an agent's tool call. So an
unknown field, an out-of-range axis, a non-numeric value, or a runaway duration
is REFUSED outright with a named reason — never silently clamped to the nearest
legal value, which would hide the bug that produced the wild value instead of
surfacing it. This mirrors ``reachy/behavior/goto_intent.py``'s stance and its
``MAX_DURATION_S`` precedent pattern: bounds are module constants, and each one
cites where its number comes from.

Refusal grammar
---------------
Every refusal is ``"<code>: <message>"`` with the remediation appended in
parentheses when there is one — the codes being :data:`REASON_UNKNOWN_KIND`,
:data:`REASON_INVALID` and :data:`REASON_BLOCKED`. A refusal is therefore always
*named* (the leading token) and always *actionable* (the rest), whichever path
submitted it.

Import boundary: this module imports :mod:`microduck_cli.behavior.model`,
:mod:`microduck_cli.behavior.sense` and the shared ``CliError`` type only. No
``microduck_cli.cli`` command module, no ``microduck_cli.ipc``, no socket, no
thread. Turning an admitted :class:`~microduck_cli.behavior.model.Behavior`'s
per-channel contribution into a wire request is the sink's job (t16), not this
leaf's: the contribution functions here carry the validated parameters through
under their channel name and invent no wire shape.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Callable, Iterable, Sequence

from microduck_cli.behavior.model import Behavior, BehaviorSpec, Lifetime, StopClass
from microduck_cli.behavior.model import admit as model_admit
from microduck_cli.behavior.sense import Sense
from microduck_cli.cli._errors import EXIT_USER_ERROR, CliError

# --------------------------------------------------------------------------- #
# Origins and reason codes                                                    #
# --------------------------------------------------------------------------- #

#: A rule in the loaded rules file fired this intent.
ORIGIN_RULE = "rule"
#: A human (or an agent) typed it at the CLI.
ORIGIN_CLI = "cli"
#: An agent injected it programmatically.
ORIGIN_AGENT = "agent"

#: The provenance values an :class:`Intent` may carry.
ORIGINS: frozenset[str] = frozenset({ORIGIN_RULE, ORIGIN_CLI, ORIGIN_AGENT})

#: The admission succeeded.
REASON_ADMITTED = "admitted"
#: No such kind is registered.
REASON_UNKNOWN_KIND = "unknown-kind"
#: The payload failed validation (unknown field, bad axis, runaway duration...).
REASON_INVALID = "invalid"
#: An UNSTOPPABLE/STOPPING incumbent holds a channel this intent claims.
REASON_BLOCKED = "blocked"

# --------------------------------------------------------------------------- #
# Boundary constants — cited precedent per bound                              #
# --------------------------------------------------------------------------- #

#: The ceiling on any one-shot intent's duration. There is no "amplitude"
#: precedent for time, so this mirrors ``reachy/behavior/goto_intent.py``'s
#: ``MAX_DURATION_S = 10.0``: a plain, deliberately small sanity ceiling — long
#: enough for any deliberate move, short enough that a runaway or duplicated
#: intent can never hold a channel for an unbounded time.
MAX_DURATION_S = 10.0

#: Per-kind one-shot defaults, used when a payload names no ``duration_s``.
DEFAULT_SKILL_DURATION_S = 5.0
DEFAULT_LOOK_DURATION_S = 1.0
DEFAULT_MOVE_DURATION_S = 1.0
DEFAULT_SOUND_DURATION_S = 2.0
DEFAULT_STOP_DURATION_S = 0.5
DEFAULT_MODE_DURATION_S = 0.5

#: The planar twist ceiling ``(vx, vy, vyaw)`` in m/s, m/s and rad/s. These are the
#: gamepad's own limits, quoted from upstream ``docs/robot/duckctl.md``: the pad
#: and the keys W/A/S/D and Q/E drive "at a gamepad's 0.3 m/s and 1.5 rad/s".
#: A human at the pad is the established precedent for how fast this duck is
#: driven, so an intent may ask for no more than a human already can.
MAX_TWIST: tuple[float, float, float] = (0.3, 0.3, 1.5)

#: The twist axes, in :data:`MAX_TWIST` order, with the unit each error names.
TWIST_AXES: tuple[str, ...] = ("vx", "vy", "vyaw")
_TWIST_LIMITS: dict[str, tuple[float, str]] = {
    "vx": (MAX_TWIST[0], "m/s"),
    "vy": (MAX_TWIST[1], "m/s"),
    "vyaw": (MAX_TWIST[2], "rad/s"),
}

#: The fields ``robot.look`` takes (``LookParams`` in the pinned
#: ``duck-ipc-proto/src/lib.rs``): a trunk-frame point in metres plus a posture
#: angle the IK holds and aims around.
LOOK_AXES: tuple[str, ...] = ("x", "y", "z", "neck_pitch")

#: How far from the trunk origin a look target may sit, in metres. Room scale:
#: ``LookParams`` documents the point as trunk-frame metres and notes the floor
#: is only ~0.12 m below the origin, and the daemon's IK clamps whatever it
#: cannot reach anyway — so this is a sanity ceiling on a *plausible* target,
#: not a reachability claim.
LOOK_REACH_M = 2.0

#: ``neck_pitch`` travel, radians — the MJCF joint range for ``neck_pitch`` in
#: upstream ``kinematics/assets/alpha/robot_walk.xml`` (``range="-1.5707963267948966
#: 1.0471975511965976"``), i.e. [-pi/2, +pi/3]. The servos enforce it
#: mechanically, so asking beyond it is asking for a pose the robot cannot hold.
NECK_PITCH_MIN_RAD = -1.5707963267948966
NECK_PITCH_MAX_RAD = 1.0471975511965976

#: Beak opening: ``MouthParams.open`` is documented "0 closed, 1 fully open".
MOUTH_MIN = 0.0
MOUTH_MAX = 1.0

#: The voice-bank tags ``robot.sound`` accepts (``SoundTag`` in the pinned
#: ``duck-ipc-proto/src/lib.rs``). The payload field is ``name``; it maps to the
#: wire's ``tag``.
SOUND_NAMES: frozenset[str] = frozenset(
    {"alarm", "greet", "inquire", "peck", "chirp", "coo", "wheee"}
)

#: The only tag for which ``hold`` is meaningful ("the held joy ride").
HOLD_SOUND = "wheee"

#: The operating modes ``robot.setMode`` accepts (``SetModeParams``: "walk" or
#: "roller").
MODES: frozenset[str] = frozenset({"walk", "roller"})

#: Every kind accepts this optional field; it bounds the admitted behaviour's
#: :class:`~microduck_cli.behavior.model.Lifetime`.
DURATION_FIELD = "duration_s"


# --------------------------------------------------------------------------- #
# Value objects                                                               #
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class Intent:
    """One submitted request, with its provenance.

    ``kind`` names an action verb (see
    :data:`~microduck_cli.behavior.sense.ACTIONS`); ``payload`` is the raw,
    unvalidated request. ``origin``/``rule_id``/``submitted_at`` are the RECORD
    of how it arrived — they are reported, never consulted by validation: a
    rule-fired intent and a hand-injected one are judged identically.
    """

    kind: str
    payload: dict = field(default_factory=dict)
    origin: str = ORIGIN_CLI
    rule_id: str | None = None
    submitted_at: float | None = None


@dataclass(frozen=True)
class Admission:
    """The outcome of offering an :class:`Intent` to the live behaviour set.

    ``reason`` is the NAMED reason: exactly :data:`REASON_ADMITTED` on success,
    and on a refusal the full refusal text, which always begins with its code
    (:data:`REASON_UNKNOWN_KIND` / :data:`REASON_INVALID` /
    :data:`REASON_BLOCKED`) — the text an operator sees and the text a rule
    engine records as a drop, identical by construction.

    ``behavior`` is the built behaviour on success and ``None`` on a refusal.
    ``evicted`` are the incumbents a STOPPING admission removes (the caller
    applies them); ``blocked`` names contested channels — the cause of a refusal,
    and merely informational on an accepted newcomer that must wait its turn.
    ``at`` echoes the ``now`` the admission was judged at.
    """

    admitted: bool
    reason: str
    behavior: Behavior | None = None
    code: str = ""
    evicted: tuple[Behavior, ...] = ()
    blocked: tuple[str, ...] = ()
    at: float = 0.0


@dataclass(frozen=True)
class KindSpec:
    """One registered kind: its validator and its behaviour builder.

    ``validator(payload) -> params`` raises :class:`CliError` on anything it
    will not accept and otherwise returns the NORMALISED parameters.
    ``to_behavior(params, behavior_id) -> Behavior`` never validates — by the
    time it runs, the parameters have already passed the one gate.
    """

    kind: str
    validator: Callable[[Mapping], dict]
    to_behavior: Callable[[dict, str], Behavior]


# --------------------------------------------------------------------------- #
# Validation helpers — every one raises, none clamps                          #
# --------------------------------------------------------------------------- #


def _reject(kind: str, message: str, remediation: str = "") -> None:
    raise CliError(code=EXIT_USER_ERROR, message=f"{kind}: {message}", remediation=remediation)


def _as_number(kind: str, value: object, label: str) -> float:
    """Coerce to ``float``, refusing bools, non-numerics and non-finite values."""
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        _reject(kind, f"{label} must be a number (got {value!r})")
    return float(value)  # type: ignore[arg-type]  # _reject always raises


def _check_symmetric(kind: str, value: float, limit: float, label: str, unit: str) -> None:
    if value < -limit or value > limit:
        _reject(
            kind,
            f"{label} out of range: {value!r} (allowed [-{limit}, {limit}] {unit})",
            remediation=f"submit a {label} within ±{limit} {unit}",
        )


def _check_between(kind: str, value: float, low: float, high: float, label: str, unit: str) -> None:
    if value < low or value > high:
        _reject(
            kind,
            f"{label} out of range: {value!r} (allowed [{low}, {high}] {unit})",
            remediation=f"submit a {label} between {low} and {high} {unit}",
        )


def _reject_unknown_fields(kind: str, payload: Mapping, allowed: Iterable[str]) -> None:
    allowed_set = set(allowed) | {DURATION_FIELD}
    unknown = sorted(str(name) for name in set(payload) - allowed_set)
    if unknown:
        _reject(
            kind,
            f"unknown field(s) {unknown} (allowed: {sorted(allowed_set)})",
            remediation="drop the unknown field(s) and resubmit",
        )


def _validate_duration(kind: str, raw: object, *, default: float | None) -> float | None:
    """The one duration gate: ``> 0`` and at most :data:`MAX_DURATION_S`."""
    if raw is None:
        return default
    value = _as_number(kind, raw, DURATION_FIELD)
    if value <= 0:
        _reject(kind, f"{DURATION_FIELD} must be > 0 (got {value!r})")
    if value > MAX_DURATION_S:
        _reject(
            kind,
            f"{DURATION_FIELD} out of range: {value!r} (allowed (0, {MAX_DURATION_S}] s)",
            remediation=f"submit a {DURATION_FIELD} of at most {MAX_DURATION_S} seconds",
        )
    return value


def _require_choice(kind: str, raw: object, choices: frozenset[str], label: str) -> str:
    if not isinstance(raw, str) or raw not in choices:
        _reject(
            kind,
            f"{label} is unknown (got {raw!r})",
            remediation=f"use one of: {', '.join(sorted(choices))}",
        )
    return raw  # type: ignore[return-value]  # _reject always raises


def _require_text(kind: str, raw: object, label: str) -> str:
    if not isinstance(raw, str) or not raw.strip():
        _reject(kind, f"{label} must be a non-empty string (got {raw!r})")
    return raw  # type: ignore[return-value]


# --------------------------------------------------------------------------- #
# Per-kind validators                                                         #
# --------------------------------------------------------------------------- #

DO = "do"
LOOK = "look"
MOVE = "move"
SOUND = "sound"
STOP = "stop"
MODE = "mode"
IDLE = "idle"


def _validate_do(payload: Mapping) -> dict:
    """``do`` runs a named canned skill (``robot.do``). Payload: ``{skill}``."""
    _reject_unknown_fields(DO, payload, {"skill"})
    skill = _require_text(DO, payload.get("skill"), "skill")
    duration = _validate_duration(DO, payload.get(DURATION_FIELD), default=DEFAULT_SKILL_DURATION_S)
    return {"skill": skill, DURATION_FIELD: duration}


def _validate_look(payload: Mapping) -> dict:
    """``look`` aims the head at a trunk-frame point (``robot.look``)."""
    _reject_unknown_fields(LOOK, payload, LOOK_AXES)
    params: dict = {}
    for axis in LOOK_AXES:
        raw = payload.get(axis)
        if raw is None:
            params[axis] = 0.0  # LookParams derives Default; every field defaults to 0
            continue
        value = _as_number(LOOK, raw, f"look.{axis}")
        if axis == "neck_pitch":
            _check_between(
                LOOK, value, NECK_PITCH_MIN_RAD, NECK_PITCH_MAX_RAD, "look.neck_pitch", "rad"
            )
        else:
            _check_symmetric(LOOK, value, LOOK_REACH_M, f"look.{axis}", "m")
        params[axis] = value
    params[DURATION_FIELD] = _validate_duration(
        LOOK, payload.get(DURATION_FIELD), default=DEFAULT_LOOK_DURATION_S
    )
    return params


def _validate_move(payload: Mapping) -> dict:
    """``move`` drives the planar twist (``robot.move``), bounded by the pad's limits."""
    _reject_unknown_fields(MOVE, payload, TWIST_AXES)
    params: dict = {}
    for axis in TWIST_AXES:
        raw = payload.get(axis)
        if raw is None:
            params[axis] = 0.0
            continue
        value = _as_number(MOVE, raw, f"move.{axis}")
        limit, unit = _TWIST_LIMITS[axis]
        _check_symmetric(MOVE, value, limit, f"move.{axis}", unit)
        params[axis] = value
    params[DURATION_FIELD] = _validate_duration(
        MOVE, payload.get(DURATION_FIELD), default=DEFAULT_MOVE_DURATION_S
    )
    return params


def _validate_sound(payload: Mapping) -> dict:
    """``sound`` plays a voice-bank tag, optionally opening the beak with it."""
    _reject_unknown_fields(SOUND, payload, {"name", "hold", "mouth"})
    name = _require_choice(SOUND, payload.get("name"), SOUND_NAMES, "sound.name")
    hold = payload.get("hold")
    if hold is not None:
        if not isinstance(hold, bool):
            _reject(SOUND, f"sound.hold must be a boolean (got {hold!r})")
        if name != HOLD_SOUND:
            _reject(
                SOUND,
                f"sound.hold is only meaningful for {HOLD_SOUND!r} (got name={name!r})",
                remediation=f"drop 'hold', or play {HOLD_SOUND!r}",
            )
    mouth = payload.get("mouth")
    if mouth is not None:
        mouth = _as_number(SOUND, mouth, "sound.mouth")
        _check_between(SOUND, mouth, MOUTH_MIN, MOUTH_MAX, "sound.mouth", "open")
    return {
        "name": name,
        "hold": hold,
        "mouth": mouth,
        DURATION_FIELD: _validate_duration(
            SOUND, payload.get(DURATION_FIELD), default=DEFAULT_SOUND_DURATION_S
        ),
    }


def _validate_stop(payload: Mapping) -> dict:
    """``stop`` halts the duck: no payload of its own."""
    _reject_unknown_fields(STOP, payload, ())
    return {
        DURATION_FIELD: _validate_duration(
            STOP, payload.get(DURATION_FIELD), default=DEFAULT_STOP_DURATION_S
        )
    }


def _validate_mode(payload: Mapping) -> dict:
    """``mode`` switches walk/roller (``robot.setMode``)."""
    _reject_unknown_fields(MODE, payload, {"mode"})
    mode = _require_choice(MODE, payload.get("mode"), MODES, "mode.mode")
    return {
        "mode": mode,
        DURATION_FIELD: _validate_duration(
            MODE, payload.get(DURATION_FIELD), default=DEFAULT_MODE_DURATION_S
        ),
    }


def _validate_idle(payload: Mapping) -> dict:
    """``idle`` is the resting layer: no payload, and it LOOPS.

    ``duration_s`` is optional and, when present, bounds the loop — which is how
    a rule admits it safely (``rules.LOOPING_ACTIONS`` refuses an unbounded
    looping react rule at load time, so a rule-fired idle always carries one).
    An idle injected with no duration loops until it is evicted.
    """
    _reject_unknown_fields(IDLE, payload, ())
    return {DURATION_FIELD: _validate_duration(IDLE, payload.get(DURATION_FIELD), default=None)}


# --------------------------------------------------------------------------- #
# Per-kind behaviour builders                                                 #
# --------------------------------------------------------------------------- #

#: ``stop`` claims ``pose`` as well as ``twist`` so a STOPPING stop evicts a
#: posture behaviour too; it abstains on ``pose`` (contributes nothing for it),
#: which lets the channel fall through rather than freeze.
STOP_CHANNELS = frozenset({"twist", "pose"})


def _one_shot(params: dict) -> Lifetime:
    return Lifetime(duration=params.get(DURATION_FIELD), looping=False)


def _behavior(
    name: str,
    behavior_id: str,
    channels: Iterable[str],
    stop_class: StopClass,
    lifetime: Lifetime,
    fn,
    params: dict,
) -> Behavior:
    spec = BehaviorSpec(
        name=name, channels=frozenset(channels), stop_class=stop_class, lifetime=lifetime
    )
    problems = spec.errors()
    if problems:  # pragma: no cover - defensive: validation already ruled these out
        raise CliError(
            code=EXIT_USER_ERROR,
            message=f"{name}: cannot build a behavior ({'; '.join(problems)})",
            remediation="this is a bug in the kind's builder",
        )
    return Behavior(id=behavior_id, spec=spec, fn=fn, params=params)


def _contribute_do(_t: float, params: dict, _s: Sense) -> dict:
    return {"skill": params["skill"]}


def _contribute_look(_t: float, params: dict, _s: Sense) -> dict:
    return {"head": {axis: params[axis] for axis in LOOK_AXES}}


def _contribute_move(_t: float, params: dict, _s: Sense) -> dict:
    return {"twist": tuple(params[axis] for axis in TWIST_AXES)}


def _contribute_sound(_t: float, params: dict, _s: Sense) -> dict:
    out: dict = {"sound": {"name": params["name"], "hold": params["hold"]}}
    if params.get("mouth") is not None:
        out["mouth"] = params["mouth"]
    return out


def _contribute_stop(_t: float, _params: dict, _s: Sense) -> dict:
    return {"twist": (0.0, 0.0, 0.0)}


def _contribute_mode(_t: float, _params: dict, _s: Sense) -> dict:
    # A mode switch happens stopped: the twist it owns is held at zero while it
    # runs, and the mode itself rides in the behaviour's params for the sink.
    return {"twist": (0.0, 0.0, 0.0)}


def _contribute_idle(_t: float, _params: dict, _s: Sense) -> dict:
    # The resting layer's actual posture is t16's idle.py; what matters here is
    # that idle PASSIVELY claims pose, so any real behaviour outranks it.
    return {"pose": {}}


def _build_do(params: dict, behavior_id: str) -> Behavior:
    # UNSTOPPABLE: a canned skill (a stand-up, say) must finish or leave the duck
    # mid-motion — the exact case model.BLOCKING_CLASSES exists for.
    return _behavior(
        DO, behavior_id, {"skill"}, StopClass.UNSTOPPABLE, _one_shot(params), _contribute_do, params
    )


def _build_look(params: dict, behavior_id: str) -> Behavior:
    return _behavior(
        LOOK,
        behavior_id,
        {"head"},
        StopClass.STOPPABLE,
        _one_shot(params),
        _contribute_look,
        params,
    )


def _build_move(params: dict, behavior_id: str) -> Behavior:
    return _behavior(
        MOVE,
        behavior_id,
        {"twist"},
        StopClass.STOPPABLE,
        _one_shot(params),
        _contribute_move,
        params,
    )


def _build_sound(params: dict, behavior_id: str) -> Behavior:
    return _behavior(
        SOUND,
        behavior_id,
        {"sound", "mouth"},
        StopClass.STOPPABLE,
        _one_shot(params),
        _contribute_sound,
        params,
    )


def _build_stop(params: dict, behavior_id: str) -> Behavior:
    return _behavior(
        STOP,
        behavior_id,
        STOP_CHANNELS,
        StopClass.STOPPING,
        _one_shot(params),
        _contribute_stop,
        params,
    )


def _build_mode(params: dict, behavior_id: str) -> Behavior:
    return _behavior(
        MODE,
        behavior_id,
        {"twist"},
        StopClass.STOPPING,
        _one_shot(params),
        _contribute_mode,
        params,
    )


def _build_idle(params: dict, behavior_id: str) -> Behavior:
    return _behavior(
        IDLE,
        behavior_id,
        {"pose"},
        StopClass.PASSIVE,
        Lifetime(duration=params.get(DURATION_FIELD), looping=True),
        _contribute_idle,
        params,
    )


#: The default kinds, one per name in :data:`~microduck_cli.behavior.sense.ACTIONS`.
DEFAULT_KINDS: tuple[KindSpec, ...] = (
    KindSpec(DO, _validate_do, _build_do),
    KindSpec(LOOK, _validate_look, _build_look),
    KindSpec(MOVE, _validate_move, _build_move),
    KindSpec(SOUND, _validate_sound, _build_sound),
    KindSpec(STOP, _validate_stop, _build_stop),
    KindSpec(MODE, _validate_mode, _build_mode),
    KindSpec(IDLE, _validate_idle, _build_idle),
)


# --------------------------------------------------------------------------- #
# The one registry                                                            #
# --------------------------------------------------------------------------- #


def _refusal(code: str, message: str, remediation: str = "") -> str:
    """The ONE refusal-text formatter — the source of the byte-identity guarantee."""
    return f"{code}: {message}" + (f" ({remediation})" if remediation else "")


class KindRegistry:
    """``kind -> (validator, builder)``: the single validation + admission gate.

    Register a kind at composition time, then submit intents through
    :meth:`admit` (the rule engine's path) or :meth:`inject` (the CLI/agent
    convenience, which calls the same :meth:`admit`). New kinds are registered
    into an instance — never by editing a caller.
    """

    def __init__(self) -> None:
        self._kinds: dict[str, KindSpec] = {}
        self._seq = 0

    # -- registration ------------------------------------------------------ #

    def register(
        self,
        kind: str,
        validator: Callable[[Mapping], dict],
        to_behavior: Callable[[dict, str], Behavior],
    ) -> "KindRegistry":
        """Register (or replace) a kind's validator and builder; returns self."""
        self._kinds[kind] = KindSpec(kind=kind, validator=validator, to_behavior=to_behavior)
        return self

    def kinds(self) -> list[str]:
        """The registered kind names, in registration order."""
        return list(self._kinds)

    def knows(self, kind: str) -> bool:
        return kind in self._kinds

    # -- the one validation entry point ------------------------------------ #

    def validate(self, kind: str, payload: Mapping | None) -> dict:
        """Validate *payload* for *kind*, returning normalised parameters.

        THE single validation entry point: every path — a rule firing, a CLI
        injection, an agent's tool call — reaches a kind's validator only
        through here, so no path can acquire limits of its own. Raises
        :class:`~microduck_cli.cli._errors.CliError` on an unknown kind or any
        payload the kind refuses; it never clamps.
        """
        spec = self._kinds.get(kind)
        if spec is None:
            raise CliError(
                code=EXIT_USER_ERROR,
                message=f"no intent kind {kind!r} is registered",
                remediation=f"use one of: {', '.join(sorted(self._kinds)) or '(none registered)'}",
            )
        if payload is None:
            payload = {}
        if not isinstance(payload, Mapping):
            raise CliError(
                code=EXIT_USER_ERROR,
                message=f"{kind}: payload must be an object (got {payload!r})",
                remediation="submit a JSON object of parameters",
            )
        if any(not isinstance(key, str) for key in payload):
            raise CliError(
                code=EXIT_USER_ERROR,
                message=f"{kind}: payload keys must be strings",
                remediation="submit a JSON object of parameters",
            )
        return spec.validator(payload)

    # -- the one admission path -------------------------------------------- #

    def admit(self, intent: Intent, now: float = 0.0, active: Sequence[Behavior] = ()) -> Admission:
        """Validate *intent* and offer the behaviour it builds to *active*.

        The ONE admission path. A refusal comes back as an :class:`Admission`
        with ``admitted=False`` and a named ``reason``; nothing is raised, so a
        caller draining many intents cannot be derailed by one bad one.
        """
        if not self.knows(intent.kind):
            return Admission(
                admitted=False,
                reason=_refusal(
                    REASON_UNKNOWN_KIND,
                    f"no intent kind {intent.kind!r} is registered",
                    f"use one of: {', '.join(sorted(self._kinds)) or '(none registered)'}",
                ),
                code=REASON_UNKNOWN_KIND,
                at=now,
            )
        try:
            params = self.validate(intent.kind, intent.payload)
        except CliError as err:
            return Admission(
                admitted=False,
                reason=_refusal(REASON_INVALID, err.message, err.remediation),
                code=REASON_INVALID,
                at=now,
            )

        self._seq += 1
        behavior_id = f"{intent.kind}-{self._seq}"
        try:
            behavior = self._kinds[intent.kind].to_behavior(params, behavior_id)
        except CliError as err:  # pragma: no cover - defensive
            return Admission(
                admitted=False,
                reason=_refusal(REASON_INVALID, err.message, err.remediation),
                code=REASON_INVALID,
                at=now,
            )

        result = model_admit(behavior, list(active))
        if not result.admitted:
            channels = ", ".join(result.blocked)
            return Admission(
                admitted=False,
                reason=_refusal(
                    REASON_BLOCKED,
                    f"channel(s) {channels} are held by an unstoppable or stopping behavior",
                    "wait for the incumbent to finish, or submit a stop",
                ),
                code=REASON_BLOCKED,
                blocked=result.blocked,
                at=now,
            )
        return Admission(
            admitted=True,
            reason=REASON_ADMITTED,
            behavior=behavior,
            code=REASON_ADMITTED,
            evicted=result.evicted,
            blocked=result.blocked,
            at=now,
        )

    def inject(
        self,
        kind: str,
        payload: Mapping | None = None,
        *,
        now: float = 0.0,
        active: Sequence[Behavior] = (),
        origin: str = ORIGIN_CLI,
        rule_id: str | None = None,
    ) -> Admission:
        """The CLI/agent convenience: wrap a payload and call the same :meth:`admit`.

        A thin front door, deliberately: it must not be able to accept anything
        :meth:`admit` would refuse, because it *is* :meth:`admit`.
        """
        if origin not in ORIGINS:
            raise CliError(
                code=EXIT_USER_ERROR,
                message=f"unknown intent origin {origin!r}",
                remediation=f"use one of: {', '.join(sorted(ORIGINS))}",
            )
        intent = Intent(
            kind=kind,
            payload=dict(payload or {}),
            origin=origin,
            rule_id=rule_id,
            submitted_at=now,
        )
        return self.admit(intent, now, active)


def default_registry() -> KindRegistry:
    """A registry carrying :data:`DEFAULT_KINDS` — one kind per action verb."""
    registry = KindRegistry()
    for spec in DEFAULT_KINDS:
        registry.register(spec.kind, spec.validator, spec.to_behavior)
    return registry
