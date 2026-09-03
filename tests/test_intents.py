"""Tests for microduck_cli.behavior.intents — the ONE admission registry."""

from __future__ import annotations

import ast
import pathlib

import pytest

from microduck_cli.behavior import intents as intents_mod
from microduck_cli.behavior.intents import (
    MAX_DURATION_S,
    MAX_TWIST,
    MOUTH_MAX,
    NECK_PITCH_MAX_RAD,
    NECK_PITCH_MIN_RAD,
    ORIGIN_AGENT,
    ORIGIN_CLI,
    ORIGIN_RULE,
    REASON_ADMITTED,
    REASON_BLOCKED,
    REASON_INVALID,
    REASON_UNKNOWN_KIND,
    Admission,
    Intent,
    KindRegistry,
    default_registry,
)
from microduck_cli.behavior.model import Behavior, BehaviorSpec, Lifetime, StopClass
from microduck_cli.behavior.sense import ACTIONS, EMPTY_SENSE
from microduck_cli.cli._errors import CliError

BEHAVIOR_DIR = pathlib.Path(intents_mod.__file__).parent


def _incumbent(name: str, channels, stop_class: StopClass) -> Behavior:
    spec = BehaviorSpec(
        name=name,
        channels=frozenset(channels),
        stop_class=stop_class,
        lifetime=Lifetime(looping=True),
    )
    return Behavior(id=f"{name}-0", spec=spec, fn=lambda t, p, s: {})


# --------------------------------------------------------------------------- #
# The kind vocabulary                                                         #
# --------------------------------------------------------------------------- #


def test_default_registry_covers_exactly_the_action_vocabulary():
    assert set(default_registry().kinds()) == set(ACTIONS)


def test_register_replaces_and_returns_self():
    registry = KindRegistry()
    returned = registry.register("do", lambda payload: {}, lambda params, bid: None)
    assert returned is registry
    assert registry.kinds() == ["do"]
    assert registry.knows("do")


# --------------------------------------------------------------------------- #
# validate(): the one entry point, fail-closed                                #
# --------------------------------------------------------------------------- #


def test_validate_accepts_a_well_formed_payload_per_kind():
    registry = default_registry()
    assert registry.validate("do", {"skill": "standup"})["skill"] == "standup"
    assert registry.validate("move", {"vx": 0.1, "wz": -1.0})["vy"] == 0.0
    assert registry.validate("look", {"x": 0.5, "neck_pitch": 0.1})["y"] == 0.0
    assert registry.validate("sound", {"name": "chirp"})["name"] == "chirp"
    assert registry.validate("mode", {"mode": "roller"})["mode"] == "roller"
    assert registry.validate("stop", {})["duration_s"] > 0
    assert registry.validate("idle", {})["duration_s"] is None


def test_validate_refuses_an_unknown_kind_naming_the_known_ones():
    with pytest.raises(CliError) as excinfo:
        default_registry().validate("teleport", {})
    assert "teleport" in excinfo.value.message
    assert "move" in excinfo.value.remediation


@pytest.mark.parametrize(
    "kind,payload,needle",
    [
        ("move", {"vx": 0.0, "wobble": 1.0}, "unknown field"),
        ("do", {"skill": "standup", "fn": "lambda: 1"}, "unknown field"),
        ("move", {"wz": 9.0}, "out of range"),
        ("move", {"vx": MAX_TWIST[0] + 0.01}, "out of range"),
        ("move", {"vx": "fast"}, "must be a number"),
        ("move", {"vx": True}, "must be a number"),
        ("move", {"vx": float("inf")}, "must be a number"),
        ("do", {"skill": "standup", "duration_s": 60}, "out of range"),
        ("do", {"skill": "standup", "duration_s": 0}, "must be > 0"),
        ("do", {"skill": ""}, "non-empty string"),
        ("do", {}, "non-empty string"),
        ("look", {"neck_pitch": NECK_PITCH_MAX_RAD + 0.5}, "out of range"),
        ("look", {"neck_pitch": NECK_PITCH_MIN_RAD - 0.5}, "out of range"),
        ("look", {"x": 99.0}, "out of range"),
        ("sound", {"name": "trumpet"}, "unknown"),
        ("sound", {"name": "chirp", "hold": True}, "only meaningful"),
        ("sound", {"name": "wheee", "hold": "yes"}, "must be a boolean"),
        ("sound", {"name": "chirp", "mouth": MOUTH_MAX + 0.5}, "out of range"),
        ("mode", {"mode": "hover"}, "unknown"),
        ("stop", {"vx": 0.1}, "unknown field"),
        ("idle", {"forever": True}, "unknown field"),
    ],
)
def test_validate_is_fail_closed(kind, payload, needle):
    """Every bad shape is REFUSED with a named message — never clamped."""
    with pytest.raises(CliError) as excinfo:
        default_registry().validate(kind, payload)
    assert needle in excinfo.value.message
    assert excinfo.value.message.startswith(f"{kind}:")


def test_validate_never_clamps_an_out_of_range_value():
    registry = default_registry()
    with pytest.raises(CliError):
        registry.validate("move", {"wz": MAX_TWIST[2] * 2})
    # ... and the legal neighbour still passes untouched.
    assert registry.validate("move", {"wz": MAX_TWIST[2]})["wz"] == MAX_TWIST[2]


def test_validate_refuses_a_non_object_payload():
    with pytest.raises(CliError) as excinfo:
        default_registry().validate("move", [1, 2, 3])
    assert "must be an object" in excinfo.value.message


def test_validate_accepts_a_missing_payload_as_empty():
    assert default_registry().validate("stop", None)["duration_s"] > 0


def test_max_duration_is_the_one_ceiling_for_every_kind():
    registry = default_registry()
    for kind, payload in (
        ("do", {"skill": "s"}),
        ("look", {}),
        ("move", {}),
        ("sound", {"name": "chirp"}),
        ("stop", {}),
        ("mode", {"mode": "walk"}),
        ("idle", {}),
    ):
        over = dict(payload, duration_s=MAX_DURATION_S + 0.1)
        with pytest.raises(CliError) as excinfo:
            registry.validate(kind, over)
        assert str(MAX_DURATION_S) in excinfo.value.message
        assert registry.validate(kind, dict(payload, duration_s=MAX_DURATION_S))


# --------------------------------------------------------------------------- #
# admit(): building, contending, refusing                                     #
# --------------------------------------------------------------------------- #


def test_admit_builds_a_behavior_with_the_right_claim_and_lifetime():
    registry = default_registry()
    admission = registry.admit(Intent("move", {"vx": 0.2, "duration_s": 3.0}), now=1.0)
    assert admission.admitted and admission.code == REASON_ADMITTED
    assert admission.reason == REASON_ADMITTED
    assert admission.at == 1.0
    behavior = admission.behavior
    assert behavior is not None
    assert behavior.channels == frozenset({"twist"})
    assert behavior.stop_class is StopClass.STOPPABLE
    assert behavior.lifetime == Lifetime(duration=3.0, looping=False)
    assert behavior.contribute(0.0, EMPTY_SENSE) == {"twist": (0.2, 0.0, 0.0)}


def test_do_is_unstoppable_and_idle_is_a_passive_loop():
    registry = default_registry()
    do = registry.admit(Intent("do", {"skill": "standup"})).behavior
    assert do is not None and do.stop_class is StopClass.UNSTOPPABLE
    assert do.channels == frozenset({"skill"})
    idle = registry.admit(Intent("idle", {})).behavior
    assert idle is not None and idle.stop_class is StopClass.PASSIVE
    assert idle.lifetime.looping and idle.lifetime.duration is None
    bounded = registry.admit(Intent("idle", {"duration_s": 4.0})).behavior
    assert bounded is not None and bounded.lifetime == Lifetime(duration=4.0, looping=True)


def test_stop_and_mode_are_stopping_and_evict_a_stoppable_incumbent():
    registry = default_registry()
    incumbent = _incumbent("move", {"twist"}, StopClass.STOPPABLE)
    admission = registry.admit(Intent("stop", {}), now=0.0, active=[incumbent])
    assert admission.admitted
    assert admission.behavior is not None
    assert admission.behavior.stop_class is StopClass.STOPPING
    assert [b.id for b in admission.evicted] == [incumbent.id]

    mode = registry.admit(Intent("mode", {"mode": "roller"}), now=0.0, active=[incumbent])
    assert mode.admitted and [b.id for b in mode.evicted] == [incumbent.id]


def test_admit_refuses_behind_a_blocking_incumbent_with_a_named_reason():
    registry = default_registry()
    skill = _incumbent("do", {"skill", "twist"}, StopClass.UNSTOPPABLE)
    admission = registry.admit(Intent("move", {"vx": 0.1}), now=0.0, active=[skill])
    assert not admission.admitted
    assert admission.code == REASON_BLOCKED
    assert admission.reason.startswith(f"{REASON_BLOCKED}: ")
    assert "twist" in admission.reason
    assert admission.behavior is None
    assert admission.blocked == ("twist",)


def test_admit_returns_a_refusal_rather_than_raising():
    admission = default_registry().admit(Intent("move", {"wz": 9.0}))
    assert isinstance(admission, Admission)
    assert not admission.admitted
    assert admission.code == REASON_INVALID
    assert admission.reason.startswith(f"{REASON_INVALID}: ")


def test_admit_names_an_unknown_kind():
    admission = default_registry().admit(Intent("teleport", {}))
    assert admission.code == REASON_UNKNOWN_KIND
    assert admission.reason.startswith(f"{REASON_UNKNOWN_KIND}: ")
    assert "teleport" in admission.reason


# --------------------------------------------------------------------------- #
# inject(): the CLI/agent front door onto the same admit()                    #
# --------------------------------------------------------------------------- #


def test_inject_calls_the_same_admit(monkeypatch):
    registry = default_registry()
    seen: list[tuple] = []
    real = registry.admit

    def spy(intent, now=0.0, active=()):
        seen.append((intent.kind, intent.origin, intent.rule_id, now))
        return real(intent, now, active)

    monkeypatch.setattr(registry, "admit", spy)
    admission = registry.inject("move", {"vx": 0.1}, now=2.0, origin=ORIGIN_AGENT)
    assert admission.admitted
    assert seen == [("move", ORIGIN_AGENT, None, 2.0)]


def test_inject_defaults_to_the_cli_origin_and_stamps_the_clock():
    registry = default_registry()
    admission = registry.inject("stop", now=7.5)
    assert admission.admitted and admission.at == 7.5
    assert Intent("stop", {}).origin == ORIGIN_CLI


def test_inject_refuses_an_unknown_origin():
    with pytest.raises(CliError) as excinfo:
        default_registry().inject("stop", {}, origin="daemon")
    assert "origin" in excinfo.value.message
    assert ORIGIN_RULE in excinfo.value.remediation


# --------------------------------------------------------------------------- #
# The obligation: one gate, one refusal text                                  #
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "kind,payload",
    [
        ("move", {"vx": 0.0, "vy": 0.0, "wz": 9.0}),
        ("do", {"skill": "standup", "duration_s": 60.0}),
    ],
)
def test_the_same_over_limit_payload_refuses_identically_from_any_origin(kind, payload):
    """Origin is a record, never an input to judgement."""
    registry = default_registry()
    from_rule = registry.admit(
        Intent(kind, dict(payload), origin=ORIGIN_RULE, rule_id="r1", submitted_at=0.0),
        now=0.0,
    )
    injected = registry.inject(kind, dict(payload), now=99.0, origin=ORIGIN_CLI)
    agent = registry.inject(kind, dict(payload), origin=ORIGIN_AGENT)
    assert not (from_rule.admitted or injected.admitted or agent.admitted)
    assert from_rule.reason == injected.reason == agent.reason
    assert from_rule.code == injected.code == REASON_INVALID


# --------------------------------------------------------------------------- #
# Static structure: exactly one registry, exactly one validate()              #
# --------------------------------------------------------------------------- #


def test_exactly_one_kind_registry_and_one_validate_under_behavior():
    """The single-gate obligation, asserted structurally rather than by faith."""
    registries: list[str] = []
    validators: list[str] = []
    for path in sorted(BEHAVIOR_DIR.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef) and node.name == "KindRegistry":
                registries.append(f"{path.name}:{node.name}")
            if (
                isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                and node.name == "validate"
            ):
                validators.append(f"{path.name}:{node.name}")
    assert registries == ["intents.py:KindRegistry"], registries
    assert validators == ["intents.py:validate"], validators


def test_module_is_a_leaf_with_no_cli_or_transport_imports():
    tree = ast.parse(pathlib.Path(intents_mod.__file__).read_text(encoding="utf-8"))
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            modules.add(node.module or "")
    assert not modules & {"socket", "subprocess", "threading", "asyncio"}
    assert not any(m.startswith("microduck_cli.ipc") for m in modules)
    for module in modules:
        if module.startswith("microduck_cli.cli"):
            assert module == "microduck_cli.cli._errors", module


# --------------------------------------------------------------------------- #
# Contributions — validated params carried through under their channel        #
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "kind,payload,expected",
    [
        ("do", {"skill": "standup"}, {"skill": "standup"}),
        (
            "look",
            {"x": 0.4, "y": -0.1, "z": 0.2, "neck_pitch": 0.3},
            {"head": {"x": 0.4, "y": -0.1, "z": 0.2, "neck_pitch": 0.3}},
        ),
        ("move", {"vx": 0.1, "vy": -0.1, "wz": 0.5}, {"twist": (0.1, -0.1, 0.5)}),
        ("sound", {"name": "chirp"}, {"sound": {"name": "chirp", "hold": None}}),
        (
            "sound",
            {"name": "wheee", "hold": True, "mouth": 0.5},
            {"sound": {"name": "wheee", "hold": True}, "mouth": 0.5},
        ),
        ("stop", {}, {"twist": (0.0, 0.0, 0.0)}),
        ("mode", {"mode": "walk"}, {"twist": (0.0, 0.0, 0.0)}),
        ("idle", {}, {"pose": {}}),
    ],
)
def test_each_kind_contributes_its_validated_params_on_its_channel(kind, payload, expected):
    behavior = default_registry().admit(Intent(kind, payload)).behavior
    assert behavior is not None
    contribution = behavior.contribute(0.0, EMPTY_SENSE)
    assert contribution == expected
    assert set(contribution) <= set(behavior.channels)


def test_a_mode_intent_keeps_the_mode_on_the_behaviors_params():
    behavior = default_registry().admit(Intent("mode", {"mode": "roller"})).behavior
    assert behavior is not None and behavior.params["mode"] == "roller"


def test_validate_refuses_a_payload_with_non_string_keys():
    with pytest.raises(CliError) as excinfo:
        default_registry().validate("move", {1: 0.1})
    assert "keys must be strings" in excinfo.value.message
