"""The duck behavior-rules schema — data-only react/inhibit rules + modes.

A rules file has FOUR sections:

* ``schema_version`` — a required top-level int, pinned at :data:`SCHEMA_VERSION`
  (currently ``1``). Every rules file names the schema it was written against so
  a stale or malformed file is refused loudly instead of half-interpreted.
* **react rules** (``[[react]]``) — ``when`` a :class:`Predicate` over the live
  sense snapshot holds, ``run`` a named action (see :data:`ACTIONS`), with
  optional parameter overrides.
* **inhibit rules** (``[[inhibit]]``) — ``when`` a predicate holds, ``disable`` a
  named set of actions.
* **modes** (``[modes.<name>]``) — named, purely declarative parameter sets, one
  of which may be selected as the file's ``active_mode``.

A rule entry may carry ``enabled = false``, which makes it a **tombstone**: it
contributes no rule of its own and, when layered over a base config via
:func:`merge_rules`, DISABLES the rule of that id contributed by the base. ``id``
is the only field a tombstone needs. ``enabled = true`` is the implicit default
and a plain no-op.

Every rule (react or inhibit) is uniquely ``id``-entified and carries
``cooldown_s`` (minimum seconds between firings, default 5.0) and ``hysteresis``
(the anti-flap margin around a threshold, default 0.0) — both validated ``>= 0``
numbers. A :class:`Predicate` is DATA (``field``/``op``/``value``), never a
string of code: ``field`` is one of :data:`SENSE_FIELDS` and ``op`` is one of
:data:`COMPARATORS`.

A REACT rule may additionally carry ``duration_s`` (a validated ``> 0`` number).
When present it bounds how long the admitted action may run. A react rule whose
``run`` is a LOOPING action (see :data:`LOOPING_ACTIONS`) MUST carry
``duration_s`` — a looping action admitted with no bound would hold its channel
forever, so that shape is refused fail-closed at load time rather than left to
an evaluator to notice at runtime.

:meth:`RulesConfig.from_dict` is the SINGLE validation gate. It refuses,
**naming the offending rule id where one exists**:

* any field outside the fixed declarative schema at any level (top-level, rule,
  predicate, mode) — no ``fn``/``code``/``source``/``exec``/free-form fields;
* any value that is not plain JSON-safe data (a callable/lambda/class instance
  anywhere in the structure);
* an unknown predicate ``field``/``op``, a boolean-op predicate carrying a
  ``value``, or a numeric-op predicate missing/mistyping one;
* an unknown ``run``/``disable`` action name;
* a negative ``cooldown_s``/``hysteresis``, or a duplicate rule ``id``;
* a ``duration_s`` that is not a positive number (``<= 0``, non-numeric, or a
  ``bool``);
* a react rule that runs a looping action (:data:`LOOPING_ACTIONS`) with no
  ``duration_s`` of its own — refused FAIL-CLOSED;
* a missing or unknown ``schema_version`` — the message names the expected
  version;
* an ``active_mode`` that does not name a defined mode, or defined modes with no
  ``active_mode`` selected.

Every failure raises :class:`~microduck_cli.cli._errors.CliError` (exit-code 1,
user error) with a specific, actionable message — never a bare
``KeyError``/``TypeError``/``tomllib.TOMLDecodeError`` escaping to a caller.

:func:`merge_rules` layers one :class:`RulesConfig` (an "overlay") over another
(a "base") per rule id: a matching id in both replaces the base entry wholesale
(never a field-by-field merge), keeping the base's ordering position; an id only
in the overlay is appended; an id only in the base is carried through untouched;
and an overlay tombstone (``enabled = false``) removes the base rule of that id.

:func:`load_rules` is stdlib-only (:mod:`tomllib`). This module is intentionally
PURE: parsing, validation, and dataclasses only. It has no engine coupling and no
evaluation logic — interpreting a :class:`Predicate` against a live sense
reading, applying ``cooldown_s``/``hysteresis`` timing, and actually running or
disabling actions is the job of a separate, dependent evaluator. It imports
nothing from :mod:`microduck_cli.cli` (beyond the shared :class:`CliError`
exception type) and touches no socket, subprocess, or transport.
"""

from __future__ import annotations

import math
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

from microduck_cli.cli._errors import EXIT_USER_ERROR, CliError

# --------------------------------------------------------------------------- #
# Schema constants                                                            #
# --------------------------------------------------------------------------- #

#: The only ``schema_version`` this module currently accepts.
SCHEMA_VERSION = 1

# TODO(t4/t11): import from behavior.sense once it lands. Defined locally here
# so t3 (this module) does not block on t4's behavior/sense.py landing in a
# parallel task — these are PROVISIONAL duck values, not a final vocabulary.
SENSE_FIELDS: frozenset[str] = frozenset(
    {
        "fallen",
        "battery_frac",
        "hottest_servo_c",
        "loop_hz",
        "pad_active",
        "remote_session",
        "tof_nearest_m",
        "skills_ready",
        "self_moving",
    }
)

# TODO(t4/t11): import from behavior.sense once it lands. Defined locally here
# so t3 (this module) does not block on t4's behavior/sense.py landing in a
# parallel task — these are PROVISIONAL duck values, not a final vocabulary.
ACTIONS: frozenset[str] = frozenset({"do", "look", "move", "sound", "stop", "mode", "idle"})

#: Actions that loop indefinitely by nature. A react rule that ``run``s one of
#: these MUST carry ``duration_s`` — see :meth:`RulesConfig.from_dict`.
LOOPING_ACTIONS: frozenset[str] = frozenset({"idle"})

#: Ordered numeric comparators — require a numeric ``value``.
_ORDERED_OPS: frozenset[str] = frozenset({"lt", "gt", "ge", "le"})
#: Equality comparators — require a ``value`` (any JSON scalar).
_EQUALITY_OPS: frozenset[str] = frozenset({"eq", "ne"})
#: Boolean-presence comparators — take NO ``value``.
_BOOLEAN_OPS: frozenset[str] = frozenset({"is_true", "is_false"})
#: "Has this field been missing/absent for at least N seconds" — a duration op.
_DURATION_OPS: frozenset[str] = frozenset({"absent_for"})
#: The full set of valid predicate comparators.
COMPARATORS: frozenset[str] = _ORDERED_OPS | _EQUALITY_OPS | _BOOLEAN_OPS | _DURATION_OPS

KIND_REACT = "react"
KIND_INHIBIT = "inhibit"

DEFAULT_COOLDOWN_S = 5.0
DEFAULT_HYSTERESIS = 0.0

_PREDICATE_FIELDS = frozenset({"field", "op", "value"})
_TOP_LEVEL_FIELDS = frozenset({"schema_version", "active_mode", "react", "inhibit", "modes"})
_REACT_FIELDS = frozenset(
    {"id", "enabled", "when", "run", "params", "cooldown_s", "hysteresis", "duration_s"}
)
_INHIBIT_FIELDS = frozenset({"id", "enabled", "when", "disable", "cooldown_s", "hysteresis"})
_REACT_REQUIRED = frozenset({"id", "when", "run"})
_INHIBIT_REQUIRED = frozenset({"id", "when", "disable"})

# Plain JSON scalar types. Anything outside (str, list, dict) + these is a code
# smell (a function, a class instance, a lambda, ...).
_JSON_SCALARS = (str, int, float, type(None))


def _error(message: str, remediation: str = "") -> CliError:
    return CliError(code=EXIT_USER_ERROR, message=message, remediation=remediation)


def _reject_code_smell(value: object, *, path: str) -> None:
    """Recursively reject anything that isn't plain JSON-safe declarative data.

    ``bool`` is fine as general JSON data here (numeric fields reject it
    separately); the walk only needs to catch non-JSON types (callables, class
    instances, sets, bytes, ...).
    """
    if isinstance(value, bool):
        return
    if isinstance(value, _JSON_SCALARS):
        return
    if isinstance(value, Mapping):
        for key, val in value.items():
            if not isinstance(key, str):
                raise _error(f"{path}: dict keys must be strings (got {key!r})")
            _reject_code_smell(val, path=f"{path}.{key}")
        return
    if isinstance(value, (list, tuple)):
        for i, item in enumerate(value):
            _reject_code_smell(item, path=f"{path}[{i}]")
        return
    raise _error(
        f"{path}: value of type {type(value).__name__!r} is not declarative JSON data "
        "(rules files must contain no code — no functions, lambdas, or objects)",
        remediation="rules files are plain TOML/JSON-serializable data only",
    )


def _require_str(data: Mapping, key: str, *, path: str) -> str:
    value = data.get(key)
    if not isinstance(value, str) or not value.strip():
        raise _error(
            f"{path}.{key} must be a non-empty string (got {value!r})",
            remediation=f"provide a string value for {key!r}",
        )
    return value


def _validate_nonneg_float(raw: object, *, name: str, path: str, default: float) -> float:
    if raw is None:
        return default
    if isinstance(raw, bool) or not isinstance(raw, (int, float)):
        raise _error(f"{path}.{name} must be a number (got {raw!r})")
    value = float(raw)
    if value < 0:
        raise _error(f"{path}.{name} must be >= 0 (got {value!r})")
    return value


def _validate_positive_float(raw: object, *, name: str, path: str) -> float | None:
    """Validate an optional strictly-positive number: ``None`` when absent."""
    if raw is None:
        return None
    if isinstance(raw, bool) or not isinstance(raw, (int, float)) or not math.isfinite(raw):
        raise _error(f"{path}.{name} must be a number (got {raw!r})")
    value = float(raw)
    if value <= 0:
        raise _error(f"{path}.{name} must be > 0 (got {value!r})")
    return value


# --------------------------------------------------------------------------- #
# Dataclasses                                                                 #
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class Predicate:
    """A data-only sense predicate: ``field``/``op``/``value``.

    ``field`` is one of :data:`SENSE_FIELDS`; ``op`` is one of
    :data:`COMPARATORS`; ``value`` is the operand — ``None`` for
    ``is_true``/``is_false``, a non-negative number of seconds for
    ``absent_for``, and any JSON scalar for every other comparator. This is
    DATA, never a string of code — a predicate is only ever *interpreted* by a
    separate (not-yet-built) rules evaluator.
    """

    field: str
    op: str
    value: object = None


@dataclass(frozen=True)
class Mode:
    """A named, purely declarative parameter set — one of a rules file's modes.

    ``params`` is a flat ``name -> number`` map; what the names mean is up to
    whatever evaluator chooses to read them.
    """

    name: str
    params: dict[str, float] = field(default_factory=dict)


@dataclass(frozen=True)
class Rule:
    """One validated rule — either a REACT rule or an INHIBIT rule.

    ``kind`` (:data:`KIND_REACT` or :data:`KIND_INHIBIT`) discriminates the two
    flavors sharing this one dataclass:

    * REACT — when :attr:`when` holds, run :attr:`action` (a name in
      :data:`ACTIONS`) with :attr:`params` overriding its defaults.
      :attr:`disable` is always empty.
    * INHIBIT — when :attr:`when` holds, disable every action named in
      :attr:`disable`. :attr:`action`/:attr:`params` are unused (``None``/empty).

    Every rule carries :attr:`cooldown_s` and :attr:`hysteresis` (both
    validated ``>= 0``); this module only carries the validated numbers —
    *acting* on cooldown/hysteresis timing is the evaluator's job.

    :attr:`duration_s` is REACT-ONLY (always ``None`` on an INHIBIT rule).
    """

    id: str
    kind: str
    when: Predicate
    cooldown_s: float = DEFAULT_COOLDOWN_S
    hysteresis: float = DEFAULT_HYSTERESIS
    action: str | None = None
    params: dict[str, object] = field(default_factory=dict)
    disable: frozenset[str] = field(default_factory=frozenset)
    duration_s: float | None = None


@dataclass(frozen=True)
class RulesConfig:
    """A fully validated rules file: react rules, inhibit rules, and modes.

    Construct via :meth:`from_dict` (never directly) so every instance is
    guaranteed to have passed schema validation. The all-empty default (with
    ``schema_version`` unset) is not itself a valid loaded document — a real
    rules file always has a ``schema_version`` — it exists only as a base for
    dataclass construction inside :meth:`from_dict`/:func:`merge_rules`.

    :attr:`disabled` carries the ids THIS layer tombstoned (``enabled =
    false``). Within a single layer a tombstone simply means "this rule is not
    in force"; across layers :func:`merge_rules` uses it to remove the rule of
    that id contributed by a lower (base) layer.
    """

    schema_version: int = SCHEMA_VERSION
    react: tuple[Rule, ...] = ()
    inhibit: tuple[Rule, ...] = ()
    modes: dict[str, Mode] = field(default_factory=dict)
    active_mode: str | None = None
    disabled: frozenset[str] = frozenset()

    @classmethod
    def from_dict(cls, data: object) -> "RulesConfig":
        """Validate *data* (a parsed TOML/JSON mapping) against the rules schema.

        Raises :class:`~microduck_cli.cli._errors.CliError` (exit-code 1) with a
        specific, actionable message on anything malformed or smelling of code —
        never a bare ``KeyError``/``TypeError``/``AttributeError``. Where the bad
        shape belongs to a specific rule, the message names that rule's ``id``.
        """
        if not isinstance(data, Mapping):
            raise _error(f"a rules file must be a TOML/JSON object (got {type(data).__name__!r})")

        unknown = set(data) - _TOP_LEVEL_FIELDS
        if unknown:
            raise _error(
                f"rules file has unexpected top-level field(s) {sorted(unknown)} — rules "
                "files are declarative-only data (no code/source/lambdas/free-form fields)",
                remediation=f"the allowed sections are: {', '.join(sorted(_TOP_LEVEL_FIELDS))}",
            )

        # Structural code-smell sweep BEFORE any semantic validation — catches a
        # lambda/callable/class-instance anywhere in the tree with one clean error.
        _reject_code_smell(dict(data), path="rules")

        schema_version = _validate_schema_version(
            data.get("schema_version"), present="schema_version" in data
        )

        react_raw = data.get("react", [])
        if not isinstance(react_raw, list):
            raise _error(f"'react' must be a list of rule tables (got {react_raw!r})")
        inhibit_raw = data.get("inhibit", [])
        if not isinstance(inhibit_raw, list):
            raise _error(f"'inhibit' must be a list of rule tables (got {inhibit_raw!r})")

        react_rules, react_off = _partition_entries(
            react_raw, kind=KIND_REACT, validate=_validate_react_rule
        )
        inhibit_rules, inhibit_off = _partition_entries(
            inhibit_raw, kind=KIND_INHIBIT, validate=_validate_inhibit_rule
        )
        disabled = react_off | inhibit_off

        all_ids = [r.id for r in react_rules] + [r.id for r in inhibit_rules] + sorted(disabled)
        duplicates = sorted({rule_id for rule_id in all_ids if all_ids.count(rule_id) > 1})
        if duplicates:
            raise _error(
                f"rules file has duplicate rule id(s): {duplicates} — every rule id must be "
                "unique across react + inhibit",
                remediation="rename one of the duplicated rules",
            )

        modes = _validate_modes(data.get("modes"))
        active_mode = _validate_active_mode(data.get("active_mode"), modes)

        return cls(
            schema_version=schema_version,
            react=react_rules,
            inhibit=inhibit_rules,
            modes=modes,
            active_mode=active_mode,
            disabled=disabled,
        )


# --------------------------------------------------------------------------- #
# Field-level validators                                                     #
# --------------------------------------------------------------------------- #


def _validate_schema_version(raw: object, *, present: bool) -> int:
    if not present or raw is None:
        raise _error(
            f"rules file is missing 'schema_version' — expected schema_version = {SCHEMA_VERSION}",
            remediation=f"add schema_version = {SCHEMA_VERSION} at the top of the file",
        )
    if isinstance(raw, bool) or not isinstance(raw, int) or raw != SCHEMA_VERSION:
        raise _error(
            f"rules file has unknown schema_version {raw!r} — expected schema_version = "
            f"{SCHEMA_VERSION}",
            remediation=f"set schema_version = {SCHEMA_VERSION}",
        )
    return raw


def _validate_predicate(raw: object, *, path: str) -> Predicate:
    if not isinstance(raw, Mapping):
        raise _error(f"{path}.when must be an object (got {raw!r})")
    unknown = set(raw) - _PREDICATE_FIELDS
    if unknown:
        raise _error(
            f"{path}.when has unexpected field(s) {sorted(unknown)}",
            remediation=f"allowed fields: {', '.join(sorted(_PREDICATE_FIELDS))}",
        )

    field_name = raw.get("field")
    if not isinstance(field_name, str) or field_name not in SENSE_FIELDS:
        raise _error(
            f"{path}.when.field is unknown (got {field_name!r})",
            remediation=f"use one of: {', '.join(sorted(SENSE_FIELDS))}",
        )

    op = raw.get("op")
    if not isinstance(op, str) or op not in COMPARATORS:
        raise _error(
            f"{path}.when.op is unknown (got {op!r})",
            remediation=f"use one of: {', '.join(sorted(COMPARATORS))}",
        )

    value = _validate_predicate_value(raw, op=op, path=path)
    return Predicate(field=field_name, op=op, value=value)


def _validate_predicate_value(raw: Mapping, *, op: str, path: str) -> object:
    """Normalize + validate the ``value`` field against *op*'s comparator class."""
    has_value = "value" in raw
    value = raw.get("value")

    if op in _BOOLEAN_OPS:
        if has_value and value is not None:
            raise _error(
                f"{path}.when: op {op!r} takes no 'value' (got {value!r})",
                remediation="remove 'value' for is_true/is_false predicates",
            )
        return None
    if op in _ORDERED_OPS or op in _DURATION_OPS:
        if not has_value or isinstance(value, bool) or not isinstance(value, (int, float)):
            raise _error(
                f"{path}.when: op {op!r} requires a numeric 'value' (got {value!r})",
                remediation="provide a numeric 'value'",
            )
        if value < 0:
            raise _error(f"{path}.when: 'value' for op {op!r} must be >= 0 (got {value!r})")
        return float(value)
    # equality ops
    if not has_value:
        raise _error(f"{path}.when: op {op!r} requires a 'value' field")
    if isinstance(value, (dict, list)):
        raise _error(f"{path}.when.value must be a scalar for op {op!r} (got {value!r})")
    return value


def _validate_action_name(name: object, *, path: str) -> str:
    if not isinstance(name, str) or name not in ACTIONS:
        raise _error(
            f"{path}: unknown action {name!r}",
            remediation=f"use one of: {', '.join(sorted(ACTIONS))}",
        )
    return name


def _validate_run_params(raw: object, *, path: str) -> dict[str, object]:
    if raw is None:
        return {}
    if not isinstance(raw, Mapping):
        raise _error(f"{path}.params must be an object (got {raw!r})")
    params: dict[str, object] = {}
    for key, value in raw.items():
        if not isinstance(key, str):
            raise _error(f"{path}.params: keys must be strings (got {key!r})")
        params[key] = value
    return params


def _validate_disable(raw: object, *, path: str) -> frozenset[str]:
    if not isinstance(raw, list) or not raw:
        raise _error(
            f"{path}.disable must be a non-empty list of action names (got {raw!r})",
            remediation=f"choose from: {', '.join(sorted(ACTIONS))}",
        )
    names: set[str] = set()
    for item in raw:
        if not isinstance(item, str) or item not in ACTIONS:
            raise _error(
                f"{path}.disable has an unknown action {item!r}",
                remediation=f"choose from: {', '.join(sorted(ACTIONS))}",
            )
        names.add(item)
    return frozenset(names)


def _is_tombstone(raw: Mapping, *, path: str) -> bool:
    """Is this entry a ``enabled = false`` tombstone? Validates the flag's type."""
    if "enabled" not in raw:
        return False  # absent flag: enabled, the default
    enabled = raw["enabled"]
    if not isinstance(enabled, bool):
        raise _error(
            f"{path}.enabled must be a boolean (got {enabled!r})",
            remediation="use enabled = false to disable a rule of this id from a lower layer",
        )
    return not enabled


def _partition_entries(
    raw_entries: list, *, kind: str, validate
) -> tuple[tuple[Rule, ...], frozenset[str]]:
    """Split a ``[[react]]``/``[[inhibit]]`` list into live rules and tombstones.

    A tombstone (``enabled = false``) contributes no :class:`Rule` — only its
    id, which :func:`merge_rules` uses to remove a base layer's rule of that id.
    """
    allowed = _REACT_FIELDS if kind == KIND_REACT else _INHIBIT_FIELDS
    rules: list[Rule] = []
    disabled: set[str] = set()
    for index, raw in enumerate(raw_entries):
        path = f"{kind}[{index}]"
        if not isinstance(raw, Mapping):
            raise _error(f"{path} must be an object (got {raw!r})")
        unknown = set(raw) - allowed
        if unknown:
            rule_id = raw.get("id")
            id_note = f" (id={rule_id!r})" if isinstance(rule_id, str) else ""
            raise _error(
                f"{path}{id_note} has unexpected field(s) {sorted(unknown)}",
                remediation=f"allowed fields: {', '.join(sorted(allowed))}",
            )
        if _is_tombstone(raw, path=path):
            disabled.add(_require_str(raw, "id", path=path))
            continue
        rules.append(validate(raw, index=index))
    return tuple(rules), frozenset(disabled)


def _validate_react_rule(raw: Mapping, *, index: int) -> Rule:
    # Shape (Mapping) and unknown-field checks already happened in
    # _partition_entries, against this same _REACT_FIELDS allow-list.
    path = f"react[{index}]"
    missing = _REACT_REQUIRED - set(raw)
    if missing:
        rule_id = raw.get("id")
        id_note = f" (id={rule_id!r})" if isinstance(rule_id, str) else ""
        raise _error(f"{path}{id_note} is missing required field(s): {sorted(missing)}")

    rule_id = _require_str(raw, "id", path=path)
    path = f"react[{index}] (id={rule_id!r})"
    action = _validate_action_name(raw.get("run"), path=f"{path}.run")
    when = _validate_predicate(raw["when"], path=path)
    params = _validate_run_params(raw.get("params"), path=path)
    cooldown_s = _validate_nonneg_float(
        raw.get("cooldown_s"), name="cooldown_s", path=path, default=DEFAULT_COOLDOWN_S
    )
    hysteresis = _validate_nonneg_float(
        raw.get("hysteresis"), name="hysteresis", path=path, default=DEFAULT_HYSTERESIS
    )
    duration_s = _validate_positive_float(raw.get("duration_s"), name="duration_s", path=path)

    if duration_s is None and action in LOOPING_ACTIONS:
        raise _error(
            f"{path} runs {action!r}, a looping action, with no duration_s — admitting it "
            "would let it hold its channel forever",
            remediation=f"add duration_s = <seconds> to react rule {rule_id!r}",
        )

    return Rule(
        id=rule_id,
        kind=KIND_REACT,
        when=when,
        cooldown_s=cooldown_s,
        hysteresis=hysteresis,
        action=action,
        params=params,
        disable=frozenset(),
        duration_s=duration_s,
    )


def _validate_inhibit_rule(raw: Mapping, *, index: int) -> Rule:
    # Shape (Mapping) and unknown-field checks already happened in
    # _partition_entries, against this same _INHIBIT_FIELDS allow-list.
    path = f"inhibit[{index}]"
    missing = _INHIBIT_REQUIRED - set(raw)
    if missing:
        rule_id = raw.get("id")
        id_note = f" (id={rule_id!r})" if isinstance(rule_id, str) else ""
        raise _error(f"{path}{id_note} is missing required field(s): {sorted(missing)}")

    rule_id = _require_str(raw, "id", path=path)
    path = f"inhibit[{index}] (id={rule_id!r})"
    when = _validate_predicate(raw["when"], path=path)
    disable = _validate_disable(raw.get("disable"), path=path)
    cooldown_s = _validate_nonneg_float(
        raw.get("cooldown_s"), name="cooldown_s", path=path, default=DEFAULT_COOLDOWN_S
    )
    hysteresis = _validate_nonneg_float(
        raw.get("hysteresis"), name="hysteresis", path=path, default=DEFAULT_HYSTERESIS
    )

    return Rule(
        id=rule_id,
        kind=KIND_INHIBIT,
        when=when,
        cooldown_s=cooldown_s,
        hysteresis=hysteresis,
        action=None,
        params={},
        disable=disable,
    )


def _validate_mode(name: str, raw: object) -> Mode:
    path = f"modes.{name}"
    if not isinstance(raw, Mapping):
        raise _error(f"{path} must be an object (got {raw!r})")
    params: dict[str, float] = {}
    for key, value in raw.items():
        if not isinstance(key, str):
            raise _error(f"{path}: parameter keys must be strings (got {key!r})")
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise _error(f"{path}.{key} must be a number (got {value!r})")
        params[key] = float(value)
    return Mode(name=name, params=params)


def _validate_modes(raw: object) -> dict[str, Mode]:
    if raw is None:
        return {}
    if not isinstance(raw, Mapping):
        raise _error(f"'modes' must be an object (got {raw!r})")
    modes: dict[str, Mode] = {}
    for name, value in raw.items():
        if not isinstance(name, str) or not name.strip():
            raise _error(f"'modes' has an invalid mode name {name!r}")
        modes[name] = _validate_mode(name, value)
    return modes


def _validate_active_mode(raw: object, modes: dict[str, Mode]) -> str | None:
    if raw is None:
        if modes:
            raise _error(
                f"rules file defines mode(s) {sorted(modes)} but no 'active_mode' is selected",
                remediation=f"set active_mode to one of: {', '.join(sorted(modes))}",
            )
        return None
    if not isinstance(raw, str) or raw not in modes:
        raise _error(
            f"'active_mode' {raw!r} is not a defined mode",
            remediation=f"use one of: {', '.join(sorted(modes)) or '(no modes defined)'}",
        )
    return raw


# --------------------------------------------------------------------------- #
# Merging + loading                                                          #
# --------------------------------------------------------------------------- #


def merge_rules(base: RulesConfig, overlay: RulesConfig) -> RulesConfig:
    """Layer *overlay* over *base*, resolving collisions per RULE ID.

    Precedence rules, all keyed on the rule ``id`` (already unique across
    react + inhibit within one layer):

    * an id in BOTH layers → the overlay's entry wins **wholesale**, keeping
      the base's ordering position. Whole-entry replacement, never a
      field-by-field merge: a rule's ``when``/``run``/``params`` are one
      thought. (The overlay may therefore also change an id's kind from react
      to inhibit or back.)
    * an id only in *base* → carried through untouched. This is what lets a
      new base rule reach an already-overlaid config without touching the
      overlay's own tuning of other ids.
    * an id only in *overlay* → appended after the base's entries.
    * an id in ``overlay.disabled`` (an ``enabled = false`` tombstone) →
      removed entirely. A tombstone naming an id no layer defines is inert,
      not an error.

    Modes merge by name (overlay wins per name), and the overlay's
    ``active_mode`` wins only if it selects one. The merged config's
    ``schema_version`` is the overlay's.
    """
    overlay_by_id = {rule.id: rule for rule in (*overlay.react, *overlay.inhibit)}
    disabled = base.disabled | overlay.disabled

    ordered: list[Rule] = []
    seen: set[str] = set()
    for rule in (*base.react, *base.inhibit, *overlay.react, *overlay.inhibit):
        if rule.id in seen:
            continue
        seen.add(rule.id)
        winner = overlay_by_id.get(rule.id, rule)
        if winner.id in disabled and winner.id not in overlay_by_id:
            continue
        ordered.append(winner)

    modes = {**base.modes, **overlay.modes}
    active_mode = overlay.active_mode if overlay.active_mode is not None else base.active_mode
    if active_mode is not None and active_mode not in modes:  # pragma: no cover - defensive
        active_mode = None

    return RulesConfig(
        schema_version=overlay.schema_version,
        react=tuple(r for r in ordered if r.kind == KIND_REACT),
        inhibit=tuple(r for r in ordered if r.kind == KIND_INHIBIT),
        modes=modes,
        active_mode=active_mode,
        disabled=frozenset(rule_id for rule_id in disabled if rule_id not in overlay_by_id),
    )


def load_rules(path: Path) -> RulesConfig:
    """Read + validate a rules TOML file at *path*.

    Raises :class:`~microduck_cli.cli._errors.CliError` on missing/unreadable
    file, invalid TOML syntax, or a schema failure caught by
    :meth:`RulesConfig.from_dict`. This is the only I/O this module performs —
    reading one local file, nothing more.
    """
    try:
        raw_text = path.read_text(encoding="utf-8")
    except OSError as err:
        raise _error(
            f"rules file {path} could not be read: {err}",
            remediation="check the path and file permissions",
        ) from err

    try:
        data = tomllib.loads(raw_text)
    except tomllib.TOMLDecodeError as err:
        raise _error(
            f"rules file {path} is not valid TOML: {err}",
            remediation="fix the TOML syntax",
        ) from err

    return RulesConfig.from_dict(data)
