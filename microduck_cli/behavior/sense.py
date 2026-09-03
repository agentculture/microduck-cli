"""The duck's sensor snapshot and the provider seam that fills it.

One frozen :class:`Sense` value is handed to every behaviour and every rule
predicate once per tick. Nothing in this module talks to a socket, a daemon or
the CLI: it names the *shape* of a reading and the *contract* a reading source
must satisfy, and the composition root supplies the concrete callables. That
keeps the module a stdlib-only leaf that imports nothing else in the package.

Two contracts are load-bearing here.

**Peek, never consume.** A provider is a zero-arg callable returning the latest
value its upstream source already holds for this tick. It must not perform a
consuming read, open a session, or block: several consumers may peek the same
provider within one tick and must all see the same sample.

**Degrade, never propagate.** A provider that is unwired (``None``), raises, or
answers with ``None`` or a malformed value yields ``None`` for its field —
identically in all four cases. :func:`read_sense` therefore cannot raise, and a
half-wired or misbehaving sensor can never kill the tick. Nothing is logged from
here; naming the drop is the sense-logging layer's job, not this leaf's.

**Field names are anchored to a real recording**, not to guesses: robotd 0.10.0
(``sim-remote-io``, run with ``--fake``, ``API_VERSION`` 16) was probed and each
field below documents the payload path it reads. Two caveats survive that
probe. ``battery_frac`` and ``hottest_servo_c`` have no source on ``--fake`` —
``robot.health`` carries loop/bus/IMU counters only, and the real robot is
expected to add them — and ``robot.skills`` answers ``METHOD_NOT_FOUND`` on this
build, so ``skills`` has no daemon source yet either. All three stay declared and
simply read ``None`` until a provider can be wired, which is exactly the "no
reading" case the contract above already covers.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Callable

# --- the snapshot ---------------------------------------------------------


@dataclass(frozen=True)
class Sense:
    """The latest duck sensor snapshot, as fed to behaviours and rule predicates.

    Every field is optional and ``None`` means exactly one thing: *no reading*.
    It is never a stand-in for a measured zero, a measured ``False``, or an
    empty set — a consumer that cannot act on "unknown" must check for ``None``
    rather than lean on falsiness.

    From the ``robot.state`` notification (~50 Hz after ``robot.subscribe``):

    - ``fallen`` — ``state.safety.fallen``.
    - ``limp`` — ``state.safety.limp``: the duck is not holding itself up.
      Distinct from ``fallen`` — a limp duck may still be upright on its stand.
    - ``gravity`` — ``state.safety.gravity``, the body-frame gravity vector
      ``(x, y, z)``. Continuous, so no rule predicate may name it (see
      :data:`SENSE_FIELDS`); a behaviour reads it off the snapshot directly.
    - ``loop_hz`` — ``state.loop.hz``, the daemon's own observed control rate,
      which is how a rule notices the robot side falling behind.
    - ``policy`` — ``state.policy``, the name of the policy currently driving.
      The recorded build reports ``"held"`` when nothing is driving. This is a
      *report*, not a substitute for ``enabled``: do not derive enablement from
      ``policy != "held"``.
    - ``move_applied`` / ``move_requested`` — ``state.move.applied`` and
      ``state.move.requested``, both 3-vectors of the planar twist. Kept as a
      pair on purpose: what was ASKED for and what the robot actually applied
      are different facts, and the later human-gate work compares them. Both are
      continuous, so neither is a predicate field.

    From ``robot.health`` (a request, not the notification stream):

    - ``battery_frac`` — pack charge as a fraction in ``0..1``. NOT present on
      the recorded ``--fake`` build (health carries ``control_loop``/``bus``/
      ``imu`` counters only); expected from the real robot.
    - ``hottest_servo_c`` — the maximum servo temperature in degrees Celsius,
      the one number a thermal rule needs without naming a joint. Also absent on
      ``--fake``.

    From dedicated requests:

    - ``remote_session`` — ``robot.remoteSessionActive`` -> ``.active``.
    - ``mode`` — ``robot.mode`` -> ``.mode`` (``"walk"`` / ``"roller"`` on the
      recorded build).

    Provider-fed, with no daemon source on the recorded build:

    - ``pad_active`` — whether a gamepad/teleop pad is currently driving.
    - ``tof_nearest_m`` — nearest time-of-flight return in metres, from the
      separate ToF socket rather than robotd.
    - ``skills`` — the skill names available, as a tuple. ``robot.skills``
      answers ``METHOD_NOT_FOUND`` on the recorded build, so this reads ``None``
      until a source exists.
    - ``enabled`` — whether actuators are enabled. Deliberately its own
      provider-fed field rather than a guess derived from ``policy``.
    - ``self_moving`` — whether we are currently COMMANDING motion. A latch the
      engine owns rather than a sensor reading; declared here so a rule may key
      on it, and ``None`` until the engine wires a provider.

    Freshness. Each sensed *group* carries its own age in seconds — how long ago
    that group's most recent reading landed, or ``None`` when none ever has.
    ``state_age_s`` covers the notification-stream fields, ``health_age_s`` the
    ``robot.health`` ones, ``pad_age_s`` the pad and ``tof_age_s`` the ToF read.
    Ages live on the snapshot rather than being looked up at call time so that
    two consumers of the same tick (a behaviour and a rule predicate) agree on
    staleness instead of each reading a slightly later clock.
    """

    fallen: bool | None = None
    limp: bool | None = None
    gravity: tuple[float, float, float] | None = None
    loop_hz: float | None = None
    policy: str | None = None
    move_applied: tuple[float, float, float] | None = None
    move_requested: tuple[float, float, float] | None = None
    battery_frac: float | None = None
    hottest_servo_c: float | None = None
    remote_session: bool | None = None
    mode: str | None = None
    pad_active: bool | None = None
    tof_nearest_m: float | None = None
    skills: tuple[str, ...] | None = None
    enabled: bool | None = None
    self_moving: bool | None = None

    state_age_s: float | None = None
    health_age_s: float | None = None
    pad_age_s: float | None = None
    tof_age_s: float | None = None


#: The "nothing sensed" snapshot: what a behaviour reads before anything is
#: wired, and what every field degrades to individually. A behaviour that gets
#: this must yield, never guess.
EMPTY_SENSE = Sense()


# --- the rule vocabulary --------------------------------------------------

#: Snapshot fields that are continuous vectors. No single field/op/value
#: predicate can say anything honest about a 3-vector, so these are consumed
#: off the snapshot by a behaviour and are absent from :data:`SENSE_FIELDS`.
CONTINUOUS_FIELDS: frozenset[str] = frozenset({"gravity", "move_applied", "move_requested"})

#: The snapshot fields a rule predicate may name, declared ONCE here.
#:
#: This is the schema-accepted vocabulary, not a promise that the current
#: composition feeds each one a live reading — an unwired field simply reads
#: ``None`` and a predicate on it cannot fire (``battery_frac``,
#: ``hottest_servo_c`` and ``skills`` are exactly that case on the recorded
#: ``--fake`` build).
#:
#: Two deliberate exclusions: :data:`CONTINUOUS_FIELDS`, and the ``*_age_s``
#: freshness floats — metadata about a reading rather than a reading, and a rule
#: keyed on staleness wants a dedicated "missing for N seconds" operator, not a
#: bare float comparison.
#:
#: NOTE: ``microduck_cli/behavior/rules.py`` (t3) carries a local copy of this
#: set and of :data:`ACTIONS` behind a ``TODO(t4)``; t11 deletes that copy and
#: imports from here, so this stays the single source of truth.
SENSE_FIELDS: frozenset[str] = frozenset(
    {
        "fallen",
        "limp",
        "loop_hz",
        "policy",
        "battery_frac",
        "hottest_servo_c",
        "remote_session",
        "mode",
        "pad_active",
        "tof_nearest_m",
        "skills",
        "enabled",
        "self_moving",
    }
)

#: The action vocabulary a rule may invoke, declared ONCE here alongside
#: :data:`SENSE_FIELDS` (a rule names a sense field and an action, so the two
#: vocabularies travel together). These are the duck's intent verbs, not CLI
#: command names: ``do`` runs a named skill, ``look`` aims the head, ``move``
#: drives the twist, ``sound`` plays audio, ``stop`` halts, ``mode`` switches
#: operating mode, ``idle`` returns to the resting layer.
ACTIONS: frozenset[str] = frozenset({"do", "look", "move", "sound", "stop", "mode", "idle"})


# --- the provider seam ----------------------------------------------------

#: A provider peeks its field's latest value. Zero-arg, non-blocking,
#: non-consuming; ``None`` is a legitimate answer meaning "no reading".
BoolProvider = Callable[[], bool | None]
FloatProvider = Callable[[], float | None]
StrProvider = Callable[[], str | None]
Vec3Provider = Callable[[], tuple[float, float, float] | None]
SkillsProvider = Callable[[], tuple[str, ...] | None]
#: A stamp provider returns the monotonic timestamp of its group's most recent
#: reading, on the same clock as :func:`read_sense`'s ``now``.
StampProvider = Callable[[], float | None]


@dataclass(frozen=True)
class SenseProviders:
    """The injected bundle of peek callables that fills a :class:`Sense`.

    Every field is optional: ``None`` means "no provider wired", which reads
    exactly like a provider that fails. The composition root builds one of these
    per process from whatever sources exist on the box, so an introspection-only
    run — or a box with no duck attached — simply wires nothing.

    The four ``*_stamp`` providers answer with the monotonic timestamp of their
    group's last reading; :func:`read_sense` turns each into the matching
    ``*_age_s`` against the tick's own ``now``.

    Each provider name matches the :class:`Sense` field it feeds; the payload
    path a real provider peeks is documented on that field. The
    ``fallen`` / ``limp`` / ``gravity`` / ``loop_hz`` / ``policy`` /
    ``move_*`` providers are expected to peek ONE held ``robot.state``
    notification sample (they belong to the same group and share
    ``state_stamp``), never to issue a request of their own.
    """

    fallen: BoolProvider | None = None
    limp: BoolProvider | None = None
    gravity: Vec3Provider | None = None
    loop_hz: FloatProvider | None = None
    policy: StrProvider | None = None
    move_applied: Vec3Provider | None = None
    move_requested: Vec3Provider | None = None
    battery_frac: FloatProvider | None = None
    hottest_servo_c: FloatProvider | None = None
    remote_session: BoolProvider | None = None
    mode: StrProvider | None = None
    pad_active: BoolProvider | None = None
    tof_nearest_m: FloatProvider | None = None
    skills: SkillsProvider | None = None
    enabled: BoolProvider | None = None
    self_moving: BoolProvider | None = None

    state_stamp: StampProvider | None = None
    health_stamp: StampProvider | None = None
    pad_stamp: StampProvider | None = None
    tof_stamp: StampProvider | None = None


#: A bundle with nothing wired. ``read_sense(NO_PROVIDERS, now)`` equals
#: :data:`EMPTY_SENSE` for every ``now``.
NO_PROVIDERS = SenseProviders()


def _peek(provider):
    """Call *provider* if wired, tolerating every failure -> ``None``.

    The whole degradation contract lives in these four lines: unwired, raising,
    and ``None``-returning providers are indistinguishable to the caller, and no
    exception ever leaves this function. ``BaseException`` is deliberately NOT
    caught — a ``KeyboardInterrupt`` or ``SystemExit`` must still stop the
    process rather than be swallowed as a missing reading.
    """
    if provider is None:
        return None
    try:
        return provider()
    except Exception:
        return None


def _as_bool(value) -> bool | None:
    """A present value -> ``bool``; a missing one stays ``None``."""
    return None if value is None else bool(value)


def _as_float(value) -> float | None:
    """A finite number -> ``float``; anything else is a non-reading.

    Strings are rejected on purpose: ``float("1.5")`` would otherwise let a
    stringly-typed payload masquerade as a measurement.
    """
    if value is None or isinstance(value, (str, bytes, bool)):
        return None
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out if math.isfinite(out) else None


def _as_str(value) -> str | None:
    """A string -> itself; anything else (including a number) is a non-reading."""
    return value if isinstance(value, str) else None


def _as_vec3(value) -> tuple[float, float, float] | None:
    """A 3-sequence of finite numbers -> a float triple; else ``None``.

    Strings are excluded before unpacking — a 3-character string is iterable and
    would otherwise pass for a vector.
    """
    if value is None or isinstance(value, (str, bytes)):
        return None
    try:
        x, y, z = value
    except (TypeError, ValueError):
        return None
    out = (_as_float(x), _as_float(y), _as_float(z))
    if any(v is None for v in out):
        return None
    return out  # type: ignore[return-value]


def _as_skills(value) -> tuple[str, ...] | None:
    """A sequence of strings -> a tuple. An empty tuple is a real reading.

    "The daemon advertises no skills" and "we never asked" are different facts,
    so ``()`` is preserved rather than folded into ``None``. A bare string is a
    malformed reading, not a one-skill list.
    """
    if value is None or isinstance(value, (str, bytes)):
        return None
    try:
        items = list(value)
    except TypeError:
        return None
    if not all(isinstance(item, str) for item in items):
        return None
    return tuple(items)


def _age(stamp, now: float) -> float | None:
    """Seconds between *stamp* and *now*, clamped at 0; ``None`` when unknown.

    A stamp ahead of ``now`` (a clock that stepped, or a provider stamping with
    a different clock) yields ``0.0`` — "as fresh as it gets" — rather than a
    negative age no consumer knows how to read.
    """
    value = _as_float(stamp)
    if value is None:
        return None
    return max(0.0, now - value)


def read_sense(providers: SenseProviders = NO_PROVIDERS, now: float = 0.0) -> Sense:
    """Build one :class:`Sense` by peeking every wired provider in *providers*.

    ``now`` is the tick's monotonic clock, used only to turn each group's stamp
    into an age. This function never raises and never blocks longer than the
    providers themselves: every failure mode collapses to ``None`` on the field
    that failed, leaving the rest of the snapshot intact.
    """
    return Sense(
        fallen=_as_bool(_peek(providers.fallen)),
        limp=_as_bool(_peek(providers.limp)),
        gravity=_as_vec3(_peek(providers.gravity)),
        loop_hz=_as_float(_peek(providers.loop_hz)),
        policy=_as_str(_peek(providers.policy)),
        move_applied=_as_vec3(_peek(providers.move_applied)),
        move_requested=_as_vec3(_peek(providers.move_requested)),
        battery_frac=_as_float(_peek(providers.battery_frac)),
        hottest_servo_c=_as_float(_peek(providers.hottest_servo_c)),
        remote_session=_as_bool(_peek(providers.remote_session)),
        mode=_as_str(_peek(providers.mode)),
        pad_active=_as_bool(_peek(providers.pad_active)),
        tof_nearest_m=_as_float(_peek(providers.tof_nearest_m)),
        skills=_as_skills(_peek(providers.skills)),
        enabled=_as_bool(_peek(providers.enabled)),
        self_moving=_as_bool(_peek(providers.self_moving)),
        state_age_s=_age(_peek(providers.state_stamp), now),
        health_age_s=_age(_peek(providers.health_stamp), now),
        pad_age_s=_age(_peek(providers.pad_stamp), now),
        tof_age_s=_age(_peek(providers.tof_stamp), now),
    )
