"""Tests for microduck_cli.behavior.replay — offline JSONL replay."""

from __future__ import annotations

import ast
import pathlib

import pytest

from microduck_cli.behavior import replay as replay_mod
from microduck_cli.behavior.defaults import load_shipped_rules
from microduck_cli.behavior.intents import default_registry
from microduck_cli.behavior.replay import RECORD_SCHEMA, RECORD_SOURCES, ReplayResult, replay
from microduck_cli.behavior.rule_engine import REASON_INHIBITED
from microduck_cli.behavior.rules import ACTIONS, SCHEMA_VERSION, RulesConfig, merge_rules
from microduck_cli.cli._errors import CliError


def _kick_on_ball_overlay() -> RulesConfig:
    return RulesConfig.from_dict(
        {
            "schema_version": SCHEMA_VERSION,
            "react": [
                {
                    "id": "kick-on-ball",
                    "when": {"field": "tof_nearest_m", "op": "lt", "value": 0.1},
                    "run": "do",
                    "params": {"skill": "kick_left"},
                }
            ],
        }
    )


# --------------------------------------------------------------------------- #
# The record contract                                                        #
# --------------------------------------------------------------------------- #


def test_record_schema_names_the_documented_sources():
    assert RECORD_SOURCES == {"state", "health", "pad", "tof", "remote", "hello"}
    assert set(RECORD_SCHEMA) == RECORD_SOURCES


def test_module_is_offline_no_sockets_no_threads():
    source = pathlib.Path(replay_mod.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            modules.add(node.module or "")
    assert not modules & {"socket", "subprocess", "threading", "asyncio"}


def test_replay_rejects_unknown_source():
    config = load_shipped_rules()
    with pytest.raises(CliError):
        replay(config, [{"ts": 0.0, "source": "nope", "params": {}}])


def test_replay_rejects_missing_ts():
    config = load_shipped_rules()
    with pytest.raises(CliError):
        replay(config, [{"source": "state", "params": {}}])


def test_replay_rejects_non_dict_record():
    config = load_shipped_rules()
    with pytest.raises(CliError):
        replay(config, ["not-a-dict"])  # type: ignore[list-item]


def test_replay_of_empty_stream():
    config = load_shipped_rules()
    result = replay(config, [])
    assert isinstance(result, ReplayResult)
    assert result.ticks == ()
    assert result.summary["ticks"] == 0


# --------------------------------------------------------------------------- #
# Field mapping                                                              #
# --------------------------------------------------------------------------- #


def test_state_record_maps_every_documented_field():
    config = load_shipped_rules()
    records = [
        {
            "ts": 0.0,
            "source": "state",
            "params": {
                "safety": {"fallen": False, "limp": False, "gravity": [0.0, 0.0, -9.8]},
                "loop": {"hz": 50.0},
                "policy": "walk",
                "move": {"applied": [0.1, 0.0, 0.0], "requested": [0.1, 0.0, 0.0]},
            },
        }
    ]
    result = replay(config, records)
    sense = result.ticks[0].sense
    assert sense.fallen is False
    assert sense.limp is False
    assert sense.gravity == (0.0, 0.0, -9.8)
    assert sense.loop_hz == 50.0
    assert sense.policy == "walk"
    assert sense.move_applied == (0.1, 0.0, 0.0)
    assert sense.move_requested == (0.1, 0.0, 0.0)


def test_health_record_maps_battery_percent_to_fraction():
    config = load_shipped_rules()
    records = [
        {"ts": 0.0, "source": "health", "params": {"battery": {"percent": 42.0}}},
    ]
    result = replay(config, records)
    assert result.ticks[0].sense.battery_frac == pytest.approx(0.42)


def test_health_record_maps_hottest_servo():
    config = load_shipped_rules()
    records = [{"ts": 0.0, "source": "health", "params": {"motors": {"hottest_c": 61.5}}}]
    result = replay(config, records)
    assert result.ticks[0].sense.hottest_servo_c == 61.5


def test_pad_and_tof_and_remote_records():
    config = load_shipped_rules()
    records = [
        {"ts": 0.0, "source": "pad", "params": {"active": True}},
        {"ts": 0.01, "source": "tof", "params": {"nearest_m": 0.3}},
        {"ts": 0.02, "source": "remote", "params": {"remote_session": True, "mode": "walk"}},
    ]
    result = replay(config, records)
    last = result.ticks[-1].sense
    assert last.pad_active is True
    assert last.tof_nearest_m == 0.3
    assert last.remote_session is True
    assert last.mode == "walk"


def test_fields_carry_forward_between_records():
    config = load_shipped_rules()
    records = [
        {"ts": 0.0, "source": "state", "params": {"safety": {"fallen": False}}},
        {"ts": 0.02, "source": "tof", "params": {"nearest_m": 0.5}},
    ]
    result = replay(config, records)
    # tick 2 (a tof record) still carries the fallen reading from tick 1.
    assert result.ticks[1].sense.fallen is False
    assert result.ticks[1].sense.tof_nearest_m == 0.5


def test_unmentioned_field_stays_none():
    config = load_shipped_rules()
    result = replay(config, [{"ts": 0.0, "source": "pad", "params": {"active": True}}])
    assert result.ticks[0].sense.fallen is None
    assert result.ticks[0].sense.battery_frac is None


# --------------------------------------------------------------------------- #
# Acceptance: a fall inhibits every action but stop within one tick          #
# --------------------------------------------------------------------------- #


def test_fallen_inhibits_every_action_except_stop_within_one_tick_shipped_rules_only():
    config = load_shipped_rules()
    records = [
        {"ts": 0.0, "source": "state", "params": {"safety": {"fallen": False}}},
        {"ts": 0.02, "source": "state", "params": {"safety": {"fallen": True}}},
    ]
    result = replay(config, records)
    assert result.ticks[0].result.inhibited == {}
    fell_tick = result.ticks[1]
    assert fell_tick.sense.fallen is True
    assert set(fell_tick.result.inhibited) == ACTIONS - {"stop"}
    assert all(rule_id == "fallen-inhibit" for rule_id in fell_tick.result.inhibited.values())
    assert "idle" in fell_tick.result.inhibited  # the idle base is covered
    assert "stop" not in fell_tick.result.inhibited


def test_a_react_rule_that_would_fire_is_dropped_as_inhibited_when_fallen():
    base = load_shipped_rules()
    config = merge_rules(base, _kick_on_ball_overlay())
    records = [
        # tick 1: tof close enough to fire the react rule; fallen not yet true.
        {"ts": 0.0, "source": "tof", "params": {"nearest_m": 0.05}},
        # tick 2: the duck falls. The react rule still matches (tof unchanged)
        # but its action ("do") is now inhibited.
        {"ts": 0.02, "source": "state", "params": {"safety": {"fallen": True}}},
    ]
    result = replay(config, records, registry=default_registry())

    first = result.ticks[0].result
    assert [fire.rule_id for fire in first.fires] == ["kick-on-ball"]

    fell = result.ticks[1].result
    assert fell.fires == ()
    inhibited_drops = [d for d in fell.drops if d.rule_id == "kick-on-ball"]
    assert len(inhibited_drops) == 1
    assert inhibited_drops[0].reason == REASON_INHIBITED
    assert "fallen-inhibit" in inhibited_drops[0].detail
    assert set(fell.inhibited) == ACTIONS - {"stop"}

    assert result.summary["fires"] == 1
    assert result.summary["drops_by_reason"].get(REASON_INHIBITED) == 1
    assert result.summary["inhibited_actions"] == sorted(ACTIONS - {"stop"})


# --------------------------------------------------------------------------- #
# Summary shape                                                              #
# --------------------------------------------------------------------------- #


def test_summary_counts_ticks_fires_and_drops():
    config = load_shipped_rules()
    records = [
        {"ts": t * 0.02, "source": "state", "params": {"safety": {"limp": limp}}}
        for t, limp in enumerate([False, True, True, True])
    ]
    result = replay(config, records)
    assert result.summary["ticks"] == 4
    # limp fires "stop-when-limp" once, then cools down for the remaining ticks.
    assert result.summary["fires"] == 1
    assert result.summary["drops"] >= 1
