"""Tests for microduck_cli.behavior.rules — the data-only rules schema."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from microduck_cli.behavior import rules as rules_mod
from microduck_cli.behavior.rules import (
    ACTIONS,
    SCHEMA_VERSION,
    SENSE_FIELDS,
    Predicate,
    RulesConfig,
    load_rules,
    merge_rules,
)
from microduck_cli.cli._errors import CliError


def _base() -> dict:
    return {"schema_version": SCHEMA_VERSION}


def _react(**overrides) -> dict:
    rule = {
        "id": "r1",
        "when": {"field": "battery_frac", "op": "lt", "value": 0.2},
        "run": "sound",
    }
    rule.update(overrides)
    return rule


def _inhibit(**overrides) -> dict:
    rule = {
        "id": "i1",
        "when": {"field": "fallen", "op": "is_true"},
        "disable": ["move"],
    }
    rule.update(overrides)
    return rule


# --------------------------------------------------------------------------- #
# Valid shapes                                                               #
# --------------------------------------------------------------------------- #


def test_minimal_valid_config():
    cfg = RulesConfig.from_dict(_base())
    assert cfg.schema_version == SCHEMA_VERSION
    assert cfg.react == ()
    assert cfg.inhibit == ()
    assert cfg.modes == {}
    assert cfg.active_mode is None


def test_valid_react_and_inhibit_rule():
    data = {**_base(), "react": [_react()], "inhibit": [_inhibit()]}
    cfg = RulesConfig.from_dict(data)
    assert len(cfg.react) == 1
    assert cfg.react[0].id == "r1"
    assert cfg.react[0].when == Predicate(field="battery_frac", op="lt", value=0.2)
    assert cfg.react[0].action == "sound"
    assert cfg.react[0].cooldown_s == 5.0
    assert cfg.react[0].hysteresis == 0.0
    assert len(cfg.inhibit) == 1
    assert cfg.inhibit[0].disable == frozenset({"move"})


def test_react_rule_defaults_and_overrides():
    data = {
        **_base(),
        "react": [_react(cooldown_s=1.5, hysteresis=0.3, duration_s=2.0, params={"speed": 0.5})],
    }
    cfg = RulesConfig.from_dict(data)
    rule = cfg.react[0]
    assert rule.cooldown_s == 1.5
    assert rule.hysteresis == 0.3
    assert rule.duration_s == 2.0
    assert rule.params == {"speed": 0.5}


def test_modes_and_active_mode():
    data = {
        **_base(),
        "modes": {"calm": {"speed": 0.5}, "playful": {"speed": 1.5}},
        "active_mode": "calm",
    }
    cfg = RulesConfig.from_dict(data)
    assert set(cfg.modes) == {"calm", "playful"}
    assert cfg.modes["calm"].params == {"speed": 0.5}
    assert cfg.active_mode == "calm"


def test_looping_action_with_duration_s_is_accepted():
    data = {**_base(), "react": [_react(run="idle", duration_s=3.0)]}
    cfg = RulesConfig.from_dict(data)
    assert cfg.react[0].action == "idle"
    assert cfg.react[0].duration_s == 3.0


def test_tombstone_react_rule():
    data = {**_base(), "react": [{"id": "gone", "enabled": False}]}
    cfg = RulesConfig.from_dict(data)
    assert cfg.react == ()
    assert cfg.disabled == frozenset({"gone"})


# --------------------------------------------------------------------------- #
# Refusals — each must name the offending rule id where one exists           #
# --------------------------------------------------------------------------- #


def test_refuses_missing_schema_version():
    with pytest.raises(CliError) as exc:
        RulesConfig.from_dict({})
    assert "schema_version" in exc.value.message
    assert str(SCHEMA_VERSION) in exc.value.message


def test_refuses_unknown_schema_version():
    with pytest.raises(CliError) as exc:
        RulesConfig.from_dict({"schema_version": 99})
    assert "schema_version" in exc.value.message
    assert "99" in exc.value.message
    assert str(SCHEMA_VERSION) in exc.value.message


def test_refuses_unknown_top_level_field():
    with pytest.raises(CliError) as exc:
        RulesConfig.from_dict({**_base(), "bogus": 1})
    assert "bogus" in exc.value.message


def test_refuses_non_json_safe_value():
    with pytest.raises(CliError) as exc:
        RulesConfig.from_dict({**_base(), "react": [_react(params={"fn": lambda: 1})]})
    assert "not declarative JSON data" in exc.value.message


def test_refuses_unknown_field_at_rule_level_names_id():
    data = {**_base(), "react": [_react(bogus_field=1)]}
    with pytest.raises(CliError) as exc:
        RulesConfig.from_dict(data)
    assert "id='r1'" in exc.value.message or "r1" in exc.value.message


def test_refuses_unknown_field_in_predicate():
    data = {**_base(), "react": [_react(when={"field": "fallen", "op": "is_true", "extra": 1})]}
    with pytest.raises(CliError) as exc:
        RulesConfig.from_dict(data)
    assert "extra" in exc.value.message


def test_refuses_unknown_predicate_field():
    data = {**_base(), "react": [_react(when={"field": "nope", "op": "is_true"})]}
    with pytest.raises(CliError) as exc:
        RulesConfig.from_dict(data)
    assert "r1" in exc.value.message
    assert "nope" in exc.value.message


def test_refuses_unknown_predicate_op():
    data = {**_base(), "react": [_react(when={"field": "fallen", "op": "nope"})]}
    with pytest.raises(CliError) as exc:
        RulesConfig.from_dict(data)
    assert "r1" in exc.value.message
    assert "nope" in exc.value.message


def test_refuses_boolean_op_carrying_a_value():
    data = {**_base(), "react": [_react(when={"field": "fallen", "op": "is_true", "value": 1})]}
    with pytest.raises(CliError) as exc:
        RulesConfig.from_dict(data)
    assert "r1" in exc.value.message
    assert "is_true" in exc.value.message


def test_refuses_numeric_op_missing_value():
    data = {**_base(), "react": [_react(when={"field": "battery_frac", "op": "lt"})]}
    with pytest.raises(CliError) as exc:
        RulesConfig.from_dict(data)
    assert "r1" in exc.value.message
    assert "lt" in exc.value.message


def test_refuses_unknown_action_in_run():
    data = {**_base(), "react": [_react(run="fly")]}
    with pytest.raises(CliError) as exc:
        RulesConfig.from_dict(data)
    assert "r1" in exc.value.message
    assert "fly" in exc.value.message


def test_refuses_unknown_action_in_disable():
    data = {**_base(), "inhibit": [_inhibit(disable=["fly"])]}
    with pytest.raises(CliError) as exc:
        RulesConfig.from_dict(data)
    assert "i1" in exc.value.message
    assert "fly" in exc.value.message


def test_refuses_negative_cooldown_s():
    data = {**_base(), "react": [_react(cooldown_s=-1.0)]}
    with pytest.raises(CliError) as exc:
        RulesConfig.from_dict(data)
    assert "r1" in exc.value.message
    assert "cooldown_s" in exc.value.message


def test_refuses_negative_hysteresis():
    data = {**_base(), "react": [_react(hysteresis=-0.1)]}
    with pytest.raises(CliError) as exc:
        RulesConfig.from_dict(data)
    assert "r1" in exc.value.message
    assert "hysteresis" in exc.value.message


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), float("-inf")])
def test_refuses_nonfinite_cooldown_s(bad):
    """A NaN cooldown makes every comparison false, so the rule would fire every
    tick — non-finite numbers must be refused fail-closed, not silently pass the
    ``>= 0`` check (``nan < 0`` is ``False``)."""
    data = {**_base(), "react": [_react(cooldown_s=bad)]}
    with pytest.raises(CliError) as exc:
        RulesConfig.from_dict(data)
    assert "r1" in exc.value.message
    assert "cooldown_s" in exc.value.message


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), float("-inf")])
def test_refuses_nonfinite_hysteresis(bad):
    data = {**_base(), "react": [_react(hysteresis=bad)]}
    with pytest.raises(CliError) as exc:
        RulesConfig.from_dict(data)
    assert "r1" in exc.value.message
    assert "hysteresis" in exc.value.message


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), float("-inf")])
def test_refuses_nonfinite_duration_s(bad):
    data = {**_base(), "react": [_react(duration_s=bad)]}
    with pytest.raises(CliError) as exc:
        RulesConfig.from_dict(data)
    assert "r1" in exc.value.message
    assert "duration_s" in exc.value.message


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), float("-inf")])
def test_refuses_nonfinite_predicate_value(bad):
    data = {**_base(), "react": [_react(when={"field": "battery_frac", "op": "lt", "value": bad})]}
    with pytest.raises(CliError) as exc:
        RulesConfig.from_dict(data)
    assert "r1" in exc.value.message


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), float("-inf")])
def test_refuses_nonfinite_numeric_param(bad):
    data = {**_base(), "react": [_react(params={"speed": bad})]}
    with pytest.raises(CliError) as exc:
        RulesConfig.from_dict(data)
    assert "r1" in exc.value.message
    assert "speed" in exc.value.message


def test_refuses_duplicate_ids():
    data = {**_base(), "react": [_react(id="dup")], "inhibit": [_inhibit(id="dup")]}
    with pytest.raises(CliError) as exc:
        RulesConfig.from_dict(data)
    assert "dup" in exc.value.message


def test_refuses_non_positive_duration_s():
    data = {**_base(), "react": [_react(duration_s=0)]}
    with pytest.raises(CliError) as exc:
        RulesConfig.from_dict(data)
    assert "r1" in exc.value.message
    assert "duration_s" in exc.value.message


def test_refuses_non_numeric_duration_s():
    data = {**_base(), "react": [_react(duration_s="soon")]}
    with pytest.raises(CliError) as exc:
        RulesConfig.from_dict(data)
    assert "r1" in exc.value.message


def test_refuses_looping_action_with_no_duration_s():
    data = {**_base(), "react": [_react(id="loopy", run="idle")]}
    with pytest.raises(CliError) as exc:
        RulesConfig.from_dict(data)
    assert "loopy" in exc.value.message
    assert "duration_s" in exc.value.message


def test_refuses_active_mode_not_defined():
    data = {**_base(), "active_mode": "ghost"}
    with pytest.raises(CliError) as exc:
        RulesConfig.from_dict(data)
    assert "ghost" in exc.value.message


def test_refuses_modes_with_no_active_mode_selected():
    data = {**_base(), "modes": {"calm": {"speed": 0.5}}}
    with pytest.raises(CliError) as exc:
        RulesConfig.from_dict(data)
    assert "calm" in exc.value.message


def test_refuses_missing_required_field_names_partial_id():
    data = {
        **_base(),
        "react": [{"id": "r-missing-run", "when": {"field": "fallen", "op": "is_true"}}],
    }
    with pytest.raises(CliError) as exc:
        RulesConfig.from_dict(data)
    assert "r-missing-run" in exc.value.message
    assert "run" in exc.value.message


def test_refuses_non_mapping_top_level():
    with pytest.raises(CliError):
        RulesConfig.from_dict([1, 2, 3])


def test_never_loads_a_partially_valid_file(tmp_path: Path):
    """A rules file with one good rule and one bad rule must load NOTHING."""
    text = f"""
schema_version = {SCHEMA_VERSION}

[[react]]
id = "good"
when = {{ field = "fallen", op = "is_true" }}
run = "stop"

[[react]]
id = "bad"
when = {{ field = "fallen", op = "is_true" }}
run = "not-a-real-action"
"""
    path = tmp_path / "rules.toml"
    path.write_text(text, encoding="utf-8")
    with pytest.raises(CliError) as exc:
        load_rules(path)
    assert "bad" in exc.value.message


# --------------------------------------------------------------------------- #
# merge_rules                                                                #
# --------------------------------------------------------------------------- #


def test_merge_matching_id_replaces_wholesale_keeping_position():
    base = RulesConfig.from_dict(
        {
            **_base(),
            "react": [
                _react(id="a", run="do"),
                _react(id="b", run="look"),
                _react(id="c", run="move"),
            ],
        }
    )
    overlay = RulesConfig.from_dict(
        {**_base(), "react": [_react(id="b", run="sound", cooldown_s=9.0)]}
    )
    merged = merge_rules(base, overlay)
    assert [r.id for r in merged.react] == ["a", "b", "c"]
    b = next(r for r in merged.react if r.id == "b")
    assert b.action == "sound"
    assert b.cooldown_s == 9.0


def test_merge_new_id_appends():
    base = RulesConfig.from_dict({**_base(), "react": [_react(id="a")]})
    overlay = RulesConfig.from_dict({**_base(), "react": [_react(id="z")]})
    merged = merge_rules(base, overlay)
    assert [r.id for r in merged.react] == ["a", "z"]


def test_merge_enabled_false_tombstones_base_rule():
    base = RulesConfig.from_dict({**_base(), "react": [_react(id="a"), _react(id="b")]})
    overlay = RulesConfig.from_dict({**_base(), "react": [{"id": "a", "enabled": False}]})
    merged = merge_rules(base, overlay)
    assert [r.id for r in merged.react] == ["b"]


def test_merge_base_upgrade_survives_alongside_overlay_tuning():
    """A NEW base rule (an 'upgrade') reaches the merged result, while the
    overlay's tuning of a different, pre-existing id survives untouched."""
    base_v1 = RulesConfig.from_dict({**_base(), "react": [_react(id="a", cooldown_s=5.0)]})
    overlay = RulesConfig.from_dict({**_base(), "react": [_react(id="a", cooldown_s=1.0)]})
    merged_v1 = merge_rules(base_v1, overlay)
    assert merged_v1.react[0].cooldown_s == 1.0

    # Base is "upgraded" — a new rule id ships, the old one is untouched.
    base_v2 = RulesConfig.from_dict(
        {**_base(), "react": [_react(id="a", cooldown_s=5.0), _react(id="new-shipped")]}
    )
    merged_v2 = merge_rules(base_v2, overlay)
    ids = [r.id for r in merged_v2.react]
    assert ids == ["a", "new-shipped"]
    a = next(r for r in merged_v2.react if r.id == "a")
    assert a.cooldown_s == 1.0  # overlay tuning of 'a' survived the upgrade
    assert any(r.id == "new-shipped" for r in merged_v2.react)  # new base rule reached the merge


def test_merge_modes_overlay_wins_per_name_and_active_mode():
    base = RulesConfig.from_dict(
        {**_base(), "modes": {"calm": {"speed": 0.5}}, "active_mode": "calm"}
    )
    overlay = RulesConfig.from_dict(
        {
            **_base(),
            "modes": {"calm": {"speed": 0.9}, "wild": {"speed": 2.0}},
            "active_mode": "wild",
        }
    )
    merged = merge_rules(base, overlay)
    assert merged.modes["calm"].params == {"speed": 0.9}
    assert merged.modes["wild"].params == {"speed": 2.0}
    assert merged.active_mode == "wild"


# --------------------------------------------------------------------------- #
# load_rules                                                                 #
# --------------------------------------------------------------------------- #


def test_load_rules_reads_valid_toml(tmp_path: Path):
    text = f"""
schema_version = {SCHEMA_VERSION}

[[react]]
id = "greet"
when = {{ field = "pad_active", op = "is_true" }}
run = "sound"
"""
    path = tmp_path / "rules.toml"
    path.write_text(text, encoding="utf-8")
    cfg = load_rules(path)
    assert cfg.react[0].id == "greet"


def test_load_rules_missing_file_raises_cli_error(tmp_path: Path):
    with pytest.raises(CliError):
        load_rules(tmp_path / "nope.toml")


def test_load_rules_bad_toml_syntax_raises_cli_error(tmp_path: Path):
    path = tmp_path / "rules.toml"
    path.write_text("this is not [ valid toml", encoding="utf-8")
    with pytest.raises(CliError):
        load_rules(path)


# --------------------------------------------------------------------------- #
# Purity: no cli/transport imports                                           #
# --------------------------------------------------------------------------- #


def test_module_imports_nothing_from_cli_or_transport():
    """microduck_cli.behavior.rules must import nothing from microduck_cli.cli
    (except the shared CliError exception type) and no socket/subprocess module."""
    source = Path(rules_mod.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)

    banned_modules = {"socket", "subprocess", "asyncio.subprocess"}
    imported_names: set[str] = set()

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imported_names.add(alias.name)
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            imported_names.add(module)
            if module == "microduck_cli.cli._errors":
                names = {alias.name for alias in node.names}
                assert names <= {
                    "CliError",
                    "EXIT_USER_ERROR",
                }, f"unexpected import from microduck_cli.cli._errors: {names}"
            elif module.startswith("microduck_cli.cli"):
                pytest.fail(f"rules.py must not import from {module!r}")

    assert not (imported_names & banned_modules), imported_names & banned_modules


def test_sense_fields_and_actions_are_frozen_sets():
    assert isinstance(SENSE_FIELDS, frozenset)
    assert isinstance(ACTIONS, frozenset)
    assert "fallen" in SENSE_FIELDS
    assert "idle" in ACTIONS
