"""Tests for microduck_cli.behavior.defaults — the shipped default_rules.toml."""

from __future__ import annotations

from microduck_cli.behavior.defaults import load_shipped_rules, shipped_rule_ids
from microduck_cli.behavior.rules import (
    ACTIONS,
    KIND_INHIBIT,
    KIND_REACT,
    SCHEMA_VERSION,
    Predicate,
    RulesConfig,
    merge_rules,
)

#: The pinned id list. THIS ORDER AND SET IS A PUBLIC INTERFACE — an operator
#: overlay overrides/tombstones a shipped rule by id, so a rename here is a
#: deliberate, visible edit, never a silent one.
EXPECTED_RULE_IDS = ("stop-when-limp", "fallen-inhibit", "low-battery-inhibit")


def test_shipped_file_loads_with_no_error():
    config = load_shipped_rules()
    assert isinstance(config, RulesConfig)
    assert config.schema_version == SCHEMA_VERSION


def test_at_most_four_rules():
    config = load_shipped_rules()
    total = len(config.react) + len(config.inhibit)
    assert total <= 4
    assert total == len(EXPECTED_RULE_IDS)


def test_shipped_rule_ids_pinned():
    assert shipped_rule_ids() == EXPECTED_RULE_IDS


def test_fallen_inhibit_fields():
    config = load_shipped_rules()
    by_id = {rule.id: rule for rule in config.inhibit}
    rule = by_id["fallen-inhibit"]
    assert rule.kind == KIND_INHIBIT
    assert rule.when == Predicate(field="fallen", op="is_true", value=None)
    # Every action except `stop` — recovery is the daemon's/skill's job, not
    # something this layer may fight.
    assert rule.disable == ACTIONS - {"stop"}


def test_low_battery_inhibit_fields():
    config = load_shipped_rules()
    by_id = {rule.id: rule for rule in config.inhibit}
    rule = by_id["low-battery-inhibit"]
    assert rule.kind == KIND_INHIBIT
    assert rule.when == Predicate(field="battery_frac", op="lt", value=0.15)
    assert rule.disable == frozenset({"do", "idle", "mode", "move"})


def test_stop_when_limp_fields():
    config = load_shipped_rules()
    by_id = {rule.id: rule for rule in config.react}
    rule = by_id["stop-when-limp"]
    assert rule.kind == KIND_REACT
    assert rule.when == Predicate(field="limp", op="is_true", value=None)
    assert rule.action == "stop"
    assert rule.cooldown_s == 5.0
    assert rule.duration_s is None


def test_no_rule_disables_stop():
    """stop must always stay available — a rule that disabled it could never halt."""
    config = load_shipped_rules()
    for rule in config.inhibit:
        assert "stop" not in rule.disable


# --------------------------------------------------------------------------- #
# Merges cleanly with an overlay: override + tombstone                       #
# --------------------------------------------------------------------------- #


def test_merges_with_overlay_override():
    base = load_shipped_rules()
    overlay = RulesConfig.from_dict(
        {
            "schema_version": SCHEMA_VERSION,
            "inhibit": [
                {
                    "id": "low-battery-inhibit",
                    "when": {"field": "battery_frac", "op": "lt", "value": 0.1},
                    "disable": ["do"],
                }
            ],
        }
    )
    merged = merge_rules(base, overlay)
    by_id = {rule.id: rule for rule in merged.inhibit}
    # the overlay's whole-entry replacement wins for this id...
    assert by_id["low-battery-inhibit"].when == Predicate(field="battery_frac", op="lt", value=0.1)
    assert by_id["low-battery-inhibit"].disable == frozenset({"do"})
    # ...while the shipped fallen-inhibit rule (untouched by the overlay) survives.
    assert by_id["fallen-inhibit"].disable == ACTIONS - {"stop"}
    merged_ids = {rule.id for rule in (*merged.react, *merged.inhibit)}
    assert merged_ids == set(EXPECTED_RULE_IDS)


def test_merges_with_overlay_tombstone():
    base = load_shipped_rules()
    overlay = RulesConfig.from_dict(
        {
            "schema_version": SCHEMA_VERSION,
            "inhibit": [{"id": "fallen-inhibit", "enabled": False}],
        }
    )
    merged = merge_rules(base, overlay)
    merged_ids = {rule.id for rule in (*merged.react, *merged.inhibit)}
    assert "fallen-inhibit" not in merged_ids
    # the other two shipped rules survive the tombstone untouched
    assert {"low-battery-inhibit", "stop-when-limp"} <= merged_ids


def test_merge_of_base_with_itself_is_stable():
    """Merging the shipped file with an empty overlay changes nothing observable."""
    base = load_shipped_rules()
    empty_overlay = RulesConfig.from_dict({"schema_version": SCHEMA_VERSION})
    merged = merge_rules(base, empty_overlay)
    merged_ids = tuple(rule.id for rule in (*merged.react, *merged.inhibit))
    assert set(merged_ids) == set(EXPECTED_RULE_IDS)
