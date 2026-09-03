"""Sense snapshot + provider degradation contract (t4)."""

from __future__ import annotations

import ast
import dataclasses
import pathlib
import re

import pytest

from microduck_cli.behavior import sense as sense_mod
from microduck_cli.behavior.sense import (
    ACTIONS,
    CONTINUOUS_FIELDS,
    EMPTY_SENSE,
    NO_PROVIDERS,
    SENSE_FIELDS,
    Sense,
    SenseProviders,
    read_sense,
)

#: Every value field and the provider attribute that feeds it.
VALUE_FIELDS = [
    ("fallen", "fallen", True),
    ("limp", "limp", True),
    ("gravity", "gravity", (0.0, 0.0, -9.81)),
    ("loop_hz", "loop_hz", 50.0),
    ("policy", "policy", "held"),
    ("move_applied", "move_applied", (0.1, 0.0, 0.0)),
    ("move_requested", "move_requested", (0.2, 0.0, 0.0)),
    ("battery_frac", "battery_frac", 0.5),
    ("hottest_servo_c", "hottest_servo_c", 41.5),
    ("remote_session", "remote_session", True),
    ("mode", "mode", "walk"),
    ("pad_active", "pad_active", True),
    ("tof_nearest_m", "tof_nearest_m", 0.25),
    ("skills", "skills", ("wave",)),
    ("enabled", "enabled", True),
    ("self_moving", "self_moving", True),
]

AGE_PAIRS = [
    ("state_age_s", "state_stamp"),
    ("health_age_s", "health_stamp"),
    ("pad_age_s", "pad_stamp"),
    ("tof_age_s", "tof_stamp"),
]


def _boom():
    raise RuntimeError("sensor exploded")


def test_empty_sense_is_all_none():
    for f in dataclasses.fields(Sense):
        assert getattr(EMPTY_SENSE, f.name) is None


def test_no_providers_reads_empty():
    assert read_sense(NO_PROVIDERS, 100.0) == EMPTY_SENSE
    assert read_sense() == EMPTY_SENSE


def test_sense_is_frozen():
    snap = Sense()
    with pytest.raises(dataclasses.FrozenInstanceError):
        snap.fallen = True  # type: ignore[misc]


@pytest.mark.parametrize("field,provider,value", VALUE_FIELDS)
def test_wired_provider_lands_on_the_snapshot(field, provider, value):
    snap = read_sense(SenseProviders(**{provider: lambda: value}), 0.0)
    assert getattr(snap, field) == value


# --- acceptance 1: raising / None / missing are indistinguishable ----------


@pytest.mark.parametrize("field,provider,value", VALUE_FIELDS)
def test_raising_none_and_missing_providers_all_yield_none(field, provider, value):
    """A provider that raises, returns None, or is absent yields the same None."""
    raising = read_sense(SenseProviders(**{provider: _boom}), 7.0)
    nones = read_sense(SenseProviders(**{provider: lambda: None}), 7.0)
    missing = read_sense(NO_PROVIDERS, 7.0)
    assert getattr(raising, field) is None
    assert getattr(nones, field) is None
    assert getattr(missing, field) is None
    assert raising == nones == missing


@pytest.mark.parametrize("field,provider", AGE_PAIRS)
def test_raising_none_and_missing_stamp_providers_all_yield_none(field, provider):
    raising = read_sense(SenseProviders(**{provider: _boom}), 7.0)
    nones = read_sense(SenseProviders(**{provider: lambda: None}), 7.0)
    assert getattr(raising, field) is None
    assert getattr(nones, field) is None
    assert raising == nones == read_sense(NO_PROVIDERS, 7.0)


def test_one_bad_provider_does_not_poison_the_others():
    snap = read_sense(
        SenseProviders(fallen=_boom, battery_frac=lambda: 0.42, loop_hz=lambda: None),
        0.0,
    )
    assert snap.fallen is None
    assert snap.loop_hz is None
    assert snap.battery_frac == 0.42


def test_read_sense_never_propagates_even_when_every_provider_raises():
    every = {f.name: _boom for f in dataclasses.fields(SenseProviders)}
    assert read_sense(SenseProviders(**every), 3.0) == EMPTY_SENSE


def test_keyboard_interrupt_is_not_swallowed():
    """Only Exception degrades; a BaseException must still stop the process."""

    def interrupt():
        raise KeyboardInterrupt

    providers = SenseProviders(fallen=interrupt)
    with pytest.raises(KeyboardInterrupt):
        read_sense(providers, 0.0)


# --- coercion: a malformed reading is a non-reading, never a raise --------


@pytest.mark.parametrize(
    "value",
    ["1.5", b"1.5", float("nan"), float("inf"), object(), None],
)
def test_malformed_float_is_a_non_reading(value):
    assert read_sense(SenseProviders(battery_frac=lambda: value), 0.0).battery_frac is None


@pytest.mark.parametrize("provider", ["gravity", "move_applied", "move_requested"])
@pytest.mark.parametrize("value", ["xyz", (1.0, 2.0), (1.0, 2.0, "z"), 5, (1, 2, float("nan"))])
def test_a_malformed_vector_is_a_non_reading(provider, value):
    assert getattr(read_sense(SenseProviders(**{provider: lambda: value}), 0.0), provider) is None


def test_a_vector_accepts_a_three_sequence_of_numbers():
    snap = read_sense(SenseProviders(gravity=lambda: [0, 1, -9]), 0.0)
    assert snap.gravity == (0.0, 1.0, -9.0)


@pytest.mark.parametrize("value", [5, 5.0, True, ["walk"], b"walk"])
def test_a_non_string_mode_or_policy_is_a_non_reading(value):
    assert read_sense(SenseProviders(mode=lambda: value), 0.0).mode is None
    assert read_sense(SenseProviders(policy=lambda: value), 0.0).policy is None


@pytest.mark.parametrize("value", ["wave", b"wave", ["wave", 3], 7])
def test_malformed_skills_is_a_non_reading(value):
    assert read_sense(SenseProviders(skills=lambda: value), 0.0).skills is None


def test_empty_skills_tuple_is_a_real_reading():
    """ "advertises nothing" and "never asked" are different facts."""
    assert read_sense(SenseProviders(skills=lambda: []), 0.0).skills == ()


def test_bools_are_coerced_but_absence_is_preserved():
    assert read_sense(SenseProviders(fallen=lambda: 0), 0.0).fallen is False
    assert read_sense(SenseProviders(fallen=lambda: 1), 0.0).fallen is True
    assert read_sense(SenseProviders(fallen=lambda: True), 0.0).fallen is True


@pytest.mark.parametrize("value", ["false", "true", "yes", "no", "0", "1", 0.0, 1.0, 2, [], {}])
def test_malformed_bool_is_a_non_reading(value):
    """Python truthiness must never authorise motion: only bool or int 0/1 count.

    The string "false" is the canonical trap — Python truthiness would coerce
    it to ``True`` and could authorise idle motion or corrupt
    fallen/limp/remote_session. Anything that is not a real ``bool`` (or the
    ints 0/1) is a non-reading, not a guess.
    """
    assert read_sense(SenseProviders(fallen=lambda: value), 0.0).fallen is None
    assert read_sense(SenseProviders(limp=lambda: value), 0.0).limp is None
    assert read_sense(SenseProviders(remote_session=lambda: value), 0.0).remote_session is None


# --- freshness ------------------------------------------------------------


def test_ages_are_measured_against_now():
    providers = SenseProviders(
        state_stamp=lambda: 10.0,
        health_stamp=lambda: 8.5,
        pad_stamp=lambda: 12.0,
        tof_stamp=lambda: 0.0,
    )
    snap = read_sense(providers, 12.0)
    assert snap.state_age_s == 2.0
    assert snap.health_age_s == 3.5
    assert snap.pad_age_s == 0.0
    assert snap.tof_age_s == 12.0


def test_a_stamp_ahead_of_now_clamps_to_zero():
    snap = read_sense(SenseProviders(state_stamp=lambda: 99.0), 1.0)
    assert snap.state_age_s == 0.0


# --- acceptance 3: the vocabulary is declared once and is honest ----------


def test_every_sense_field_name_is_an_attribute_of_sense():
    attrs = {f.name for f in dataclasses.fields(Sense)}
    assert SENSE_FIELDS <= attrs, sorted(SENSE_FIELDS - attrs)


def test_sense_fields_excludes_ages_and_continuous_vectors():
    assert not any(name.endswith("_age_s") for name in SENSE_FIELDS)
    assert SENSE_FIELDS.isdisjoint(CONTINUOUS_FIELDS)
    assert CONTINUOUS_FIELDS <= {f.name for f in dataclasses.fields(Sense)}


def test_every_sensed_field_except_the_excluded_ones_is_nameable():
    attrs = {f.name for f in dataclasses.fields(Sense)}
    expected = {n for n in attrs if not n.endswith("_age_s")} - CONTINUOUS_FIELDS
    assert SENSE_FIELDS == expected


def test_providers_cover_every_sense_field():
    provider_attrs = {f.name for f in dataclasses.fields(SenseProviders)}
    value_attrs = {f.name for f in dataclasses.fields(Sense) if not f.name.endswith("_age_s")}
    assert value_attrs <= provider_attrs
    for age_field, stamp in AGE_PAIRS:
        assert stamp in provider_attrs
        assert age_field in {f.name for f in dataclasses.fields(Sense)}


def test_the_recorded_daemon_shapes_have_a_home_on_the_snapshot():
    """robotd 0.10.0 --fake (API_VERSION 16) payload paths map onto these fields."""
    attrs = {f.name for f in dataclasses.fields(Sense)}
    # state.safety.{fallen,limp,gravity}, state.loop.hz, state.policy, state.move.*
    assert {"fallen", "limp", "gravity", "loop_hz", "policy"} <= attrs
    assert {"move_applied", "move_requested"} <= attrs
    # robot.remoteSessionActive.active, robot.mode.mode
    assert {"remote_session", "mode"} <= attrs
    # Absent on --fake, declared anyway and simply read None.
    assert {"battery_frac", "hottest_servo_c", "skills"} <= attrs


def test_actions_vocabulary():
    assert ACTIONS == frozenset({"do", "look", "move", "sound", "stop", "mode", "idle"})


def test_vocabulary_is_declared_only_here():
    """Any other behavior module defining these must be the t3 copy t11 removes."""
    pkg = pathlib.Path(sense_mod.__file__).parent
    pattern = re.compile(r"^(SENSE_FIELDS|ACTIONS)\b.*=", re.MULTILINE)
    for path in sorted(pkg.glob("*.py")):
        if path.name == "sense.py":
            continue
        text = path.read_text(encoding="utf-8")
        if pattern.search(text):
            assert "TODO(t4" in text, f"{path.name} redeclares the vocabulary untagged"


def test_module_is_a_stdlib_only_leaf():
    """sense.py imports no package sibling and no third party — no I/O, no CLI."""
    tree = ast.parse(pathlib.Path(sense_mod.__file__).read_text(encoding="utf-8"))
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".")[0])
    assert imported <= {"__future__", "math", "dataclasses", "typing"}
