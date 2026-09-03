"""Tests for microduck_cli.behavior.rule_engine — react/inhibit on one tick."""

from __future__ import annotations

import ast
import pathlib

import pytest

from microduck_cli.behavior import rule_engine as engine_mod
from microduck_cli.behavior.intents import ORIGIN_CLI, ORIGIN_RULE, default_registry
from microduck_cli.behavior.model import Behavior, BehaviorSpec, Lifetime, StopClass
from microduck_cli.behavior.rule_engine import (
    REASON_COOLDOWN,
    REASON_INHIBITED,
    REASON_REARMING,
    REASON_REFUSED,
    Drop,
    RuleEngine,
)
from microduck_cli.behavior.rules import SCHEMA_VERSION, RulesConfig
from microduck_cli.behavior.sense import Sense

TICK_HZ = 50.0
TICK_S = 1.0 / TICK_HZ


class FakeClock:
    """A monotonic fake clock advanced explicitly, one tick at a time."""

    def __init__(self, start: float = 0.0, step: float = TICK_S) -> None:
        self.t = start
        self.step = step
        self.reads = 0

    def __call__(self) -> float:
        self.reads += 1
        return self.t

    def advance(self, seconds: float | None = None) -> float:
        self.t += self.step if seconds is None else seconds
        return self.t


def _config(react=(), inhibit=(), **extra) -> RulesConfig:
    data: dict = {"schema_version": SCHEMA_VERSION}
    if react:
        data["react"] = list(react)
    if inhibit:
        data["inhibit"] = list(inhibit)
    data.update(extra)
    return RulesConfig.from_dict(data)


def _engine(config: RulesConfig, clock: FakeClock, registry=None) -> RuleEngine:
    return RuleEngine(config, registry or default_registry(), clock)


def _incumbent(name: str, channels, stop_class: StopClass) -> Behavior:
    spec = BehaviorSpec(
        name=name,
        channels=frozenset(channels),
        stop_class=stop_class,
        lifetime=Lifetime(looping=True),
    )
    return Behavior(id=f"{name}-0", spec=spec, fn=lambda t, p, s: {})


LOW_BATTERY = {
    "id": "low-battery-quack",
    "when": {"field": "battery_frac", "op": "lt", "value": 0.2},
    "run": "sound",
    "params": {"name": "alarm"},
    "cooldown_s": 5.0,
}


# --------------------------------------------------------------------------- #
# Firing                                                                      #
# --------------------------------------------------------------------------- #


def test_a_matching_rule_fires_through_the_registry_with_rule_origin():
    clock = FakeClock()
    engine = _engine(_config(react=[LOW_BATTERY]), clock)
    result = engine.evaluate(Sense(battery_frac=0.1))
    assert result.now == 0.0
    assert [f.rule_id for f in result.fires] == ["low-battery-quack"]
    fire = result.fires[0]
    assert fire.kind == "sound"
    assert fire.behavior.name == "sound"
    assert fire.behavior.params["name"] == "alarm"
    assert result.drops == ()
    assert [b.id for b in result.active] == [fire.behavior.id]


def test_a_non_matching_rule_is_silent():
    engine = _engine(_config(react=[LOW_BATTERY]), FakeClock())
    result = engine.evaluate(Sense(battery_frac=0.9))
    assert result.fires == () and result.drops == ()


def test_a_none_sense_field_never_matches():
    """An unwired or failed sensor must not be able to fire a rule."""
    engine = _engine(_config(react=[LOW_BATTERY]), FakeClock())
    assert engine.evaluate(Sense()).fires == ()
    truthy = _config(
        react=[
            {
                "id": "fallen-stop",
                "when": {"field": "fallen", "op": "is_true"},
                "run": "stop",
            }
        ]
    )
    assert _engine(truthy, FakeClock()).evaluate(Sense(fallen=None)).fires == ()
    falsy = _config(
        react=[
            {
                "id": "not-fallen",
                "when": {"field": "fallen", "op": "is_false"},
                "run": "stop",
            }
        ]
    )
    assert _engine(falsy, FakeClock()).evaluate(Sense(fallen=None)).fires == ()
    assert _engine(falsy, FakeClock()).evaluate(Sense(fallen=False)).fires != ()


def test_absent_for_measures_absence_from_the_first_tick():
    clock = FakeClock()
    config = _config(
        react=[
            {
                "id": "state-stale",
                "when": {"field": "loop_hz", "op": "absent_for", "value": 0.1},
                "run": "stop",
                "cooldown_s": 0.0,
            }
        ]
    )
    engine = _engine(config, clock)
    assert engine.evaluate(Sense()).fires == ()  # t=0: absent for 0 s so far
    for _ in range(4):
        clock.advance()
        engine.evaluate(Sense())
    clock.advance()
    assert engine.evaluate(Sense()).fires != ()  # t=0.10 s of continuous absence
    clock.advance()
    assert engine.evaluate(Sense(loop_hz=50.0)).fires == ()  # a reading resets it


# --------------------------------------------------------------------------- #
# Cooldown                                                                    #
# --------------------------------------------------------------------------- #


def test_a_cooldown_rule_fires_at_most_once_in_250_ticks_at_50hz():
    """250 ticks at 50 Hz is 5 s; a cooldown_s=5 rule may fire only once."""
    clock = FakeClock()
    engine = _engine(_config(react=[LOW_BATTERY]), clock)
    sense = Sense(battery_frac=0.1)  # holds continuously: every tick matches

    fires = 0
    cooldown_drops = 0
    for tick in range(250):
        result = engine.evaluate(sense)
        fires += len(result.fires)
        cooldown_drops += sum(1 for d in result.drops if d.reason == REASON_COOLDOWN)
        if tick < 249:
            clock.advance()

    assert fires == 1
    assert cooldown_drops == 249  # every suppressed tick is reported, never skipped
    assert clock.t == pytest.approx(249 * TICK_S)


def test_the_first_firing_is_never_cooldown_gated():
    clock = FakeClock(start=1000.0)
    engine = _engine(_config(react=[LOW_BATTERY]), clock)
    assert engine.evaluate(Sense(battery_frac=0.1)).fires != ()


def test_a_rule_refires_once_the_cooldown_lapses():
    clock = FakeClock()
    engine = _engine(_config(react=[LOW_BATTERY]), clock)
    sense = Sense(battery_frac=0.1)
    assert engine.evaluate(sense).fires != ()
    clock.advance(4.999)
    assert engine.evaluate(sense).fires == ()
    clock.advance(0.002)
    assert engine.evaluate(sense).fires != ()


def test_a_cooldown_drop_names_the_rule_and_the_wait():
    clock = FakeClock()
    engine = _engine(_config(react=[LOW_BATTERY]), clock)
    engine.evaluate(Sense(battery_frac=0.1))
    clock.advance()
    drop = engine.evaluate(Sense(battery_frac=0.1)).drops[0]
    assert isinstance(drop, Drop)
    assert drop.reason == REASON_COOLDOWN
    assert drop.rule_id == "low-battery-quack"
    assert "cooldown_s is 5.0" in drop.detail


# --------------------------------------------------------------------------- #
# Hysteresis                                                                  #
# --------------------------------------------------------------------------- #


def test_hysteresis_holds_a_rule_disarmed_until_the_value_crosses_back():
    clock = FakeClock()
    config = _config(
        react=[
            dict(LOW_BATTERY, cooldown_s=0.0, hysteresis=0.05),
        ]
    )
    engine = _engine(config, clock)
    assert engine.evaluate(Sense(battery_frac=0.19)).fires != ()

    clock.advance()
    # Back above the threshold, but inside the margin (0.2 .. 0.25): still disarmed.
    result = engine.evaluate(Sense(battery_frac=0.22))
    assert result.fires == () and result.drops == ()  # predicate no longer holds

    clock.advance()
    result = engine.evaluate(Sense(battery_frac=0.19))
    assert result.fires == ()
    assert [(d.reason, d.rule_id) for d in result.drops] == [(REASON_REARMING, "low-battery-quack")]

    clock.advance()
    engine.evaluate(Sense(battery_frac=0.30))  # clears value + hysteresis: re-armed
    clock.advance()
    assert engine.evaluate(Sense(battery_frac=0.19)).fires != ()


def test_hysteresis_on_a_gt_rule_rearms_below_the_lower_margin():
    clock = FakeClock()
    config = _config(
        react=[
            {
                "id": "hot",
                "when": {"field": "hottest_servo_c", "op": "gt", "value": 60.0},
                "run": "stop",
                "cooldown_s": 0.0,
                "hysteresis": 5.0,
            }
        ]
    )
    engine = _engine(config, clock)
    assert engine.evaluate(Sense(hottest_servo_c=61.0)).fires != ()
    clock.advance()
    engine.evaluate(Sense(hottest_servo_c=57.0))  # inside the margin: still disarmed
    clock.advance()
    assert engine.evaluate(Sense(hottest_servo_c=61.0)).drops[0].reason == REASON_REARMING
    clock.advance()
    engine.evaluate(Sense(hottest_servo_c=54.0))  # below 60 - 5: re-armed
    clock.advance()
    assert engine.evaluate(Sense(hottest_servo_c=61.0)).fires != ()


def test_zero_hysteresis_rearms_as_soon_as_the_predicate_reads_false():
    clock = FakeClock()
    engine = _engine(_config(react=[dict(LOW_BATTERY, cooldown_s=0.0)]), clock)
    assert engine.evaluate(Sense(battery_frac=0.1)).fires != ()
    clock.advance()
    assert engine.evaluate(Sense(battery_frac=0.1)).fires != ()  # cooldown 0, armed


# --------------------------------------------------------------------------- #
# Inhibition                                                                  #
# --------------------------------------------------------------------------- #


FALLEN_INHIBIT = {
    "id": "fallen-inhibit",
    "when": {"field": "fallen", "op": "is_true"},
    "disable": ["do", "sound", "idle"],
}


def test_an_inhibit_rule_suppresses_the_named_action_as_a_named_drop():
    engine = _engine(_config(react=[LOW_BATTERY], inhibit=[FALLEN_INHIBIT]), FakeClock())
    result = engine.evaluate(Sense(battery_frac=0.1, fallen=True))
    assert result.fires == ()
    assert len(result.drops) == 1
    drop = result.drops[0]
    assert drop.reason == REASON_INHIBITED
    assert drop.rule_id == "low-battery-quack"
    assert "fallen-inhibit" in drop.detail
    assert "'sound'" in drop.detail
    assert result.inhibited == {
        "do": "fallen-inhibit",
        "sound": "fallen-inhibit",
        "idle": "fallen-inhibit",
    }


def test_an_inhibit_rule_stops_inhibiting_when_its_predicate_lapses():
    engine = _engine(_config(react=[LOW_BATTERY], inhibit=[FALLEN_INHIBIT]), FakeClock())
    result = engine.evaluate(Sense(battery_frac=0.1, fallen=False))
    assert result.inhibited == {}
    assert result.fires != ()


def test_an_inhibit_rule_does_not_touch_actions_it_does_not_name():
    config = _config(
        react=[{"id": "r", "when": {"field": "fallen", "op": "is_true"}, "run": "stop"}],
        inhibit=[FALLEN_INHIBIT],
    )
    result = _engine(config, FakeClock()).evaluate(Sense(fallen=True))
    assert [f.kind for f in result.fires] == ["stop"]


# --------------------------------------------------------------------------- #
# Refusal — the one-gate obligation seen from the rule side                   #
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "run,params,extra",
    [
        ("move", {"vx": 0.0, "vy": 0.0, "wz": 9.0}, {}),
        ("do", {"skill": "standup"}, {"duration_s": 60.0}),
    ],
)
def test_a_rule_fired_refusal_is_byte_identical_to_an_injected_one(run, params, extra):
    registry = default_registry()
    rule = dict(
        {
            "id": "over-limit",
            "when": {"field": "battery_frac", "op": "lt", "value": 0.2},
            "run": run,
            "params": params,
        },
        **extra,
    )
    engine = _engine(_config(react=[rule]), FakeClock(), registry)
    result = engine.evaluate(Sense(battery_frac=0.1))

    payload = dict(params, **extra)
    injected = registry.inject(run, payload, now=123.0, origin=ORIGIN_CLI)

    assert result.fires == ()
    assert len(result.drops) == 1
    assert result.drops[0].reason == REASON_REFUSED
    assert result.drops[0].rule_id == "over-limit"
    assert result.drops[0].detail == injected.reason
    assert not injected.admitted


def test_a_blocked_admission_is_a_named_refusal_drop():
    engine = _engine(_config(react=[dict(LOW_BATTERY, run="move", params={})]), FakeClock())
    skill = _incumbent("do", {"skill", "sound", "twist"}, StopClass.UNSTOPPABLE)
    result = engine.evaluate(Sense(battery_frac=0.1), active=[skill])
    assert result.fires == ()
    assert result.drops[0].reason == REASON_REFUSED
    assert result.drops[0].detail.startswith("blocked: ")
    assert "twist" in result.drops[0].detail


def test_a_refused_rule_may_fire_on_a_later_tick_without_a_cooldown_penalty():
    """A refusal is not a firing: the cooldown clock must not start on it."""
    clock = FakeClock()
    engine = _engine(_config(react=[dict(LOW_BATTERY, run="move", params={})]), clock)
    skill = _incumbent("do", {"twist"}, StopClass.UNSTOPPABLE)
    assert engine.evaluate(Sense(battery_frac=0.1), active=[skill]).fires == ()
    clock.advance()
    assert engine.evaluate(Sense(battery_frac=0.1)).fires != ()


# --------------------------------------------------------------------------- #
# duration_s                                                                  #
# --------------------------------------------------------------------------- #


def test_a_rules_duration_caps_the_admitted_lifetime():
    rule = dict(LOW_BATTERY, duration_s=2.5)
    result = _engine(_config(react=[rule]), FakeClock()).evaluate(Sense(battery_frac=0.1))
    assert result.fires[0].behavior.lifetime == Lifetime(duration=2.5, looping=False)


def test_a_looping_action_admitted_by_a_rule_is_bounded_by_its_duration():
    rule = {
        "id": "idle-base",
        "when": {"field": "battery_frac", "op": "lt", "value": 0.2},
        "run": "idle",
        "duration_s": 3.0,
    }
    result = _engine(_config(react=[rule]), FakeClock()).evaluate(Sense(battery_frac=0.1))
    lifetime = result.fires[0].behavior.lifetime
    assert lifetime.looping and lifetime.duration == 3.0


def test_a_payload_duration_wins_over_the_rules_own_bound():
    rule = dict(LOW_BATTERY, duration_s=9.0, params={"name": "alarm", "duration_s": 1.0})
    result = _engine(_config(react=[rule]), FakeClock()).evaluate(Sense(battery_frac=0.1))
    assert result.fires[0].behavior.lifetime.duration == 1.0


# --------------------------------------------------------------------------- #
# Live-set bookkeeping                                                        #
# --------------------------------------------------------------------------- #


def test_the_result_carries_the_live_set_after_evictions():
    config = _config(
        react=[
            {
                "id": "fallen-stop",
                "when": {"field": "fallen", "op": "is_true"},
                "run": "stop",
            }
        ]
    )
    incumbent = _incumbent("move", {"twist"}, StopClass.STOPPABLE)
    result = _engine(config, FakeClock()).evaluate(Sense(fallen=True), active=[incumbent])
    assert [b.id for b in result.evicted] == [incumbent.id]
    assert [b.name for b in result.active] == ["stop"]


def test_a_second_rule_contends_against_the_first_rules_admission_this_tick():
    """Two rules claiming one channel resolve within the same tick, not across it."""
    config = _config(
        react=[
            {
                "id": "a-skill",
                "when": {"field": "fallen", "op": "is_true"},
                "run": "do",
                "params": {"skill": "standup"},
            },
            {
                "id": "b-skill",
                "when": {"field": "fallen", "op": "is_true"},
                "run": "do",
                "params": {"skill": "sit"},
            },
        ]
    )
    result = _engine(config, FakeClock()).evaluate(Sense(fallen=True))
    assert [f.rule_id for f in result.fires] == ["a-skill"]
    assert result.drops[0].rule_id == "b-skill"
    assert result.drops[0].reason == REASON_REFUSED


# --------------------------------------------------------------------------- #
# Purity                                                                      #
# --------------------------------------------------------------------------- #


def test_the_engine_reads_only_the_injected_clock():
    clock = FakeClock()
    engine = _engine(_config(react=[LOW_BATTERY]), clock)
    engine.evaluate(Sense(battery_frac=0.1))
    engine.evaluate(Sense(battery_frac=0.1))
    assert clock.reads == 2  # one read per tick; no wall clock anywhere


def test_the_rule_engine_submits_with_the_rule_origin_and_id():
    registry = default_registry()
    seen: list = []
    real = registry.admit

    def spy(intent, now=0.0, active=()):
        seen.append(intent)
        return real(intent, now, active)

    registry.admit = spy  # type: ignore[method-assign]
    _engine(_config(react=[LOW_BATTERY]), FakeClock(), registry).evaluate(Sense(battery_frac=0.1))
    assert [(i.origin, i.rule_id, i.kind) for i in seen] == [
        (ORIGIN_RULE, "low-battery-quack", "sound")
    ]


def test_module_is_a_leaf_with_no_cli_transport_or_logging_setup():
    source = pathlib.Path(engine_mod.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            modules.add(node.module or "")
    assert not modules & {"socket", "subprocess", "threading", "asyncio", "logging"}
    assert not any(m.startswith("microduck_cli.cli") for m in modules)
    assert not any(m.startswith("microduck_cli.ipc") for m in modules)
    assert "basicConfig" not in source


# --------------------------------------------------------------------------- #
# Comparators                                                                 #
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "op,threshold,reading,matches",
    [
        ("lt", 0.2, 0.1, True),
        ("lt", 0.2, 0.3, False),
        ("le", 0.2, 0.2, True),
        ("gt", 0.2, 0.3, True),
        ("ge", 0.2, 0.2, True),
        ("ge", 0.2, 0.1, False),
        ("eq", 0.2, 0.2, True),
        ("ne", 0.2, 0.2, False),
        ("ne", 0.2, 0.5, True),
    ],
)
def test_every_comparator_reads_the_snapshot(op, threshold, reading, matches):
    config = _config(
        react=[
            {
                "id": "cmp",
                "when": {"field": "battery_frac", "op": op, "value": threshold},
                "run": "stop",
            }
        ]
    )
    result = _engine(config, FakeClock()).evaluate(Sense(battery_frac=reading))
    assert bool(result.fires) is matches


def test_a_type_mismatch_is_simply_no_match_not_an_exception():
    config = _config(
        react=[
            {
                "id": "policy-name",
                "when": {"field": "policy", "op": "gt", "value": 3},
                "run": "stop",
            }
        ]
    )
    assert _engine(config, FakeClock()).evaluate(Sense(policy="held")).fires == ()


def test_hysteresis_rearms_when_the_field_stops_reading():
    clock = FakeClock()
    engine = _engine(_config(react=[dict(LOW_BATTERY, cooldown_s=0.0, hysteresis=0.05)]), clock)
    assert engine.evaluate(Sense(battery_frac=0.19)).fires != ()
    clock.advance()
    engine.evaluate(Sense())  # no reading at all: nothing holds the rule disarmed
    clock.advance()
    assert engine.evaluate(Sense(battery_frac=0.19)).fires != ()


def test_the_engine_exposes_the_config_it_was_built_with():
    config = _config(react=[LOW_BATTERY])
    assert _engine(config, FakeClock()).config is config
