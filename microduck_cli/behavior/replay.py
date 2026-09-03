"""Offline JSONL replay — run a rules file over a recorded sense stream.

:func:`replay` is the OFFLINE half of the rule engine: it drives
:class:`~microduck_cli.behavior.rule_engine.RuleEngine` tick by tick over a
sequence of previously-recorded records instead of a live daemon connection.
No socket, no thread, no wall-clock sleep — a fake, record-driven clock is
what makes 10,000 recorded ticks replay in milliseconds and makes the result
completely reproducible.

The record contract
--------------------
Each record is one JSON object, one per line (JSONL), shaped::

    {"ts": <float>, "source": "state" | "health" | "pad" | "tof" | "remote", "params": {...}}

``ts`` is MONOTONIC SECONDS since the recording started — not wall-clock, and
not required to be evenly spaced. ``source`` names which sensed group this
record refreshes, and ``params`` is that group's payload, shaped close to the
matching wire message (see :data:`RECORD_SCHEMA` for the exact paths this
module reads out of each). This is the ONE place that contract is defined:
``microduck_cli.cli._commands.duck``'s ``record`` verb (a later task) MUST
write records shaped exactly this way, and this module is what proves a
recording it wrote is replayable.

Mapping into :class:`~microduck_cli.behavior.sense.Sense` mirrors that
module's own field documentation exactly (``state.safety.fallen -> fallen``,
``state.loop.hz -> loop_hz``, ``health.battery.percent -> battery_frac``, and
so on — see :data:`RECORD_SCHEMA`). A field a given record's ``params`` does
not mention is left at whatever it last read: a sensor that has not reported
again has not necessarily changed, and a record stream need not repeat every
field on every line. A field that has NEVER been mentioned reads ``None``,
:class:`~microduck_cli.behavior.sense.Sense`'s ordinary "no reading" state.

Nothing here opens a socket, starts a thread, or sleeps: replaying 10,000
records is exactly as fast as evaluating 10,000 rules ticks, because that is
literally what it does.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field

from microduck_cli.behavior.intents import KindRegistry, default_registry
from microduck_cli.behavior.rule_engine import RuleEngine, TickResult
from microduck_cli.behavior.rules import RulesConfig
from microduck_cli.behavior.sense import Sense
from microduck_cli.cli._errors import EXIT_USER_ERROR, CliError

__all__ = [
    "RECORD_SCHEMA",
    "RECORD_SOURCES",
    "ReplayTick",
    "ReplayResult",
    "replay",
]

#: One entry per record ``source``: the dotted ``params`` paths that source
#: may carry, and (in the docstring above and inline comments below) the
#: :class:`~microduck_cli.behavior.sense.Sense` field each maps to. This is the
#: documented contract a recorder (``duck record``, a later task) must
#: produce, and the only shape this module reads.
RECORD_SCHEMA: dict[str, tuple[str, ...]] = {
    # Mirrors the `robot.state` notification (~50 Hz once subscribed).
    "state": (
        "safety.fallen",  # -> fallen
        "safety.limp",  # -> limp
        "safety.gravity",  # -> gravity
        "loop.hz",  # -> loop_hz
        "policy",  # -> policy
        "move.applied",  # -> move_applied
        "move.requested",  # -> move_requested
    ),
    # Mirrors a `robot.health` answer.
    "health": (
        "battery.percent",  # 0..100 -> battery_frac (0..1)
        "motors.hottest_c",  # -> hottest_servo_c
    ),
    # `pad.report` notifications (or the `pad.input` accepted answer).
    "pad": ("active",),  # -> pad_active
    # `tof.frame` notifications (or the `tof.stream` accepted answer).
    "tof": ("nearest_m",),  # -> tof_nearest_m
    # The two "dedicated request" fields sense.py documents together:
    # `robot.remoteSessionActive` and `robot.mode`. Recorded under one source
    # since neither is part of the ~50 Hz state stream.
    "remote": (
        "active",  # raw `robot.remoteSessionActive` result, as `duck record` writes it
        "remote_session",  # -> remote_session (already-mapped form)
        "mode",  # -> mode
    ),
    # The handshake record `duck record` writes first (daemon api_version etc.).
    # Carries no sense field; a replay skips it.
    "hello": (),
}

#: The valid record ``source`` values — the keys of :data:`RECORD_SCHEMA`.
RECORD_SOURCES: frozenset[str] = frozenset(RECORD_SCHEMA)

#: Which :class:`Sense` ``*_age_s`` field each record ``source`` refreshes.
_AGE_FIELD_FOR_SOURCE: dict[str, str] = {
    "state": "state_age_s",
    "health": "health_age_s",
    "pad": "pad_age_s",
    "tof": "tof_age_s",
}


def _error(message: str, remediation: str = "") -> CliError:
    return CliError(code=EXIT_USER_ERROR, message=message, remediation=remediation)


# --------------------------------------------------------------------------- #
# Value coercion — degrade to None, never raise on a bad reading              #
# --------------------------------------------------------------------------- #


def _as_bool(value: object) -> bool | None:
    return None if value is None else bool(value)


def _as_float(value: object) -> float | None:
    if value is None or isinstance(value, (str, bytes, bool)):
        return None
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def _as_str(value: object) -> str | None:
    return value if isinstance(value, str) else None


def _as_vec3(value: object) -> tuple[float, float, float] | None:
    if value is None or isinstance(value, (str, bytes)) or not isinstance(value, (list, tuple)):
        return None
    if len(value) != 3:
        return None
    out = tuple(_as_float(v) for v in value)
    if any(v is None for v in out):
        return None
    return out  # type: ignore[return-value]


def _get_path(params: Mapping, *path: str) -> object:
    """Walk a dotted path (``"safety", "fallen"``) through nested dicts."""
    current: object = params
    for key in path:
        if not isinstance(current, Mapping) or key not in current:
            return None
        current = current[key]
    return current


def _has_path(params: Mapping, *path: str) -> bool:
    current: object = params
    for key in path:
        if not isinstance(current, Mapping) or key not in current:
            return False
        current = current[key]
    return True


# --------------------------------------------------------------------------- #
# Record -> Sense field mapping                                              #
# --------------------------------------------------------------------------- #


def _apply_state(kwargs: dict, params: Mapping) -> None:
    if _has_path(params, "safety", "fallen"):
        kwargs["fallen"] = _as_bool(_get_path(params, "safety", "fallen"))
    if _has_path(params, "safety", "limp"):
        kwargs["limp"] = _as_bool(_get_path(params, "safety", "limp"))
    if _has_path(params, "safety", "gravity"):
        kwargs["gravity"] = _as_vec3(_get_path(params, "safety", "gravity"))
    if _has_path(params, "loop", "hz"):
        kwargs["loop_hz"] = _as_float(_get_path(params, "loop", "hz"))
    if "policy" in params:
        kwargs["policy"] = _as_str(params.get("policy"))
    if _has_path(params, "move", "applied"):
        kwargs["move_applied"] = _as_vec3(_get_path(params, "move", "applied"))
    if _has_path(params, "move", "requested"):
        kwargs["move_requested"] = _as_vec3(_get_path(params, "move", "requested"))


def _apply_health(kwargs: dict, params: Mapping) -> None:
    if _has_path(params, "battery", "percent"):
        pct = _as_float(_get_path(params, "battery", "percent"))
        kwargs["battery_frac"] = None if pct is None else pct / 100.0
    if _has_path(params, "motors", "hottest_c"):
        kwargs["hottest_servo_c"] = _as_float(_get_path(params, "motors", "hottest_c"))


def _apply_pad(kwargs: dict, params: Mapping) -> None:
    if "active" in params:
        kwargs["pad_active"] = _as_bool(params.get("active"))


def _apply_tof(kwargs: dict, params: Mapping) -> None:
    if "nearest_m" in params:
        kwargs["tof_nearest_m"] = _as_float(params.get("nearest_m"))


def _apply_remote(kwargs: dict, params: Mapping) -> None:
    if "remote_session" in params:
        kwargs["remote_session"] = _as_bool(params.get("remote_session"))
    elif "active" in params:  # the raw `robot.remoteSessionActive` answer
        kwargs["remote_session"] = _as_bool(params.get("active"))
    if "mode" in params:
        kwargs["mode"] = _as_str(params.get("mode"))


_APPLY_BY_SOURCE = {
    "state": _apply_state,
    "health": _apply_health,
    "pad": _apply_pad,
    "tof": _apply_tof,
    "remote": _apply_remote,
}


# --------------------------------------------------------------------------- #
# Results                                                                     #
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class ReplayTick:
    """One record replayed: the record it came from and what the engine decided."""

    ts: float
    source: str
    sense: Sense
    result: TickResult


@dataclass(frozen=True)
class ReplayResult:
    """The full replay: every tick, plus a rollup summary.

    ``summary`` carries ``ticks``, ``fires``, ``drops``, ``drops_by_reason``
    (``reason -> count``) and ``inhibited_actions`` (the sorted union of every
    action inhibited on any tick) — enough for a CLI (``rules check
    --replay``) to print a one-line verdict without re-walking ``ticks``
    itself.
    """

    ticks: tuple[ReplayTick, ...] = ()
    summary: dict[str, object] = field(default_factory=dict)


def _validate_record(record: object, *, index: int) -> tuple[float, str, Mapping]:
    if not isinstance(record, Mapping):
        raise _error(f"record[{index}] must be a JSON object (got {record!r})")
    ts = record.get("ts")
    if isinstance(ts, bool) or not isinstance(ts, (int, float)):
        raise _error(f"record[{index}].ts must be a number (got {ts!r})")
    source = record.get("source")
    if not isinstance(source, str) or source not in RECORD_SOURCES:
        raise _error(
            f"record[{index}].source is unknown (got {source!r})",
            remediation=f"use one of: {', '.join(sorted(RECORD_SOURCES))}",
        )
    params = record.get("params", {})
    if not isinstance(params, Mapping):
        raise _error(f"record[{index}].params must be an object (got {params!r})")
    return float(ts), source, params


def replay(
    config: RulesConfig,
    records: Iterable[Mapping],
    registry: KindRegistry | None = None,
    clock_start: float = 0.0,
) -> ReplayResult:
    """Evaluate *config* tick by tick over *records*, offline.

    One tick per record, in iteration order — ``ts`` need not be sorted by the
    caller, but a record's tick clock is ``clock_start + record["ts"]``, so an
    out-of-order stream will read as time moving backwards (:class:`RuleEngine`
    does not require monotonic input, but ``cooldown_s`` math will be
    nonsensical on it, exactly as it would against a live clock that jumped
    backwards). Each record updates only the :class:`Sense` fields its
    ``source`` covers (see :data:`RECORD_SCHEMA`); every other field carries
    forward from the previous tick unchanged.

    *registry* defaults to :func:`~microduck_cli.behavior.intents.default_registry`.
    Nothing here performs I/O: *records* is any iterable of already-parsed
    JSON objects (a list, or a generator reading a JSONL file line by line —
    reading the file is the caller's job).
    """
    if registry is None:
        registry = default_registry()

    _clock = [clock_start]
    engine = RuleEngine(config, registry, clock=lambda: _clock[0])

    sense_kwargs: dict[str, object] = {}
    last_seen: dict[str, float] = {}
    active: list = []
    ticks: list[ReplayTick] = []

    fires = 0
    drops = 0
    drops_by_reason: dict[str, int] = {}
    inhibited_actions: set[str] = set()

    for index, record in enumerate(records):
        ts, source, params = _validate_record(record, index=index)
        _APPLY_BY_SOURCE[source](sense_kwargs, params)
        last_seen[source] = ts
        _clock[0] = clock_start + ts

        for src, age_field in _AGE_FIELD_FOR_SOURCE.items():
            seen_at = last_seen.get(src)
            sense_kwargs[age_field] = None if seen_at is None else max(0.0, ts - seen_at)

        sense = Sense(**sense_kwargs)
        result = engine.evaluate(sense, active)
        active = list(result.active)

        fires += len(result.fires)
        drops += len(result.drops)
        for drop in result.drops:
            drops_by_reason[drop.reason] = drops_by_reason.get(drop.reason, 0) + 1
        inhibited_actions.update(result.inhibited)

        ticks.append(ReplayTick(ts=ts, source=source, sense=sense, result=result))

    summary = {
        "ticks": len(ticks),
        "fires": fires,
        "drops": drops,
        "drops_by_reason": drops_by_reason,
        "inhibited_actions": sorted(inhibited_actions),
    }
    return ReplayResult(ticks=tuple(ticks), summary=summary)
