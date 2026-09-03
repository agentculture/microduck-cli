"""Behaviour model: channels, lifetimes, and the arbitration core (t4)."""

from __future__ import annotations

import ast
import dataclasses
import pathlib

import pytest

from microduck_cli.behavior import model as model_mod
from microduck_cli.behavior.model import (
    BLOCKING_CLASSES,
    CHANNELS,
    Behavior,
    BehaviorSpec,
    Lifetime,
    StopClass,
    admit,
    arbitrate,
)
from microduck_cli.behavior.sense import EMPTY_SENSE, Sense


def make(
    beh_id: str,
    channels,
    stop_class: StopClass = StopClass.STOPPABLE,
    *,
    fn=None,
    lifetime: Lifetime | None = None,
    wants_sense: bool = False,
    params: dict | None = None,
) -> Behavior:
    spec = BehaviorSpec(
        name=beh_id.rstrip("0123456789-") or beh_id,
        channels=frozenset(channels),
        stop_class=stop_class,
        lifetime=lifetime or Lifetime(looping=True),
    )
    return Behavior(
        id=beh_id,
        spec=spec,
        fn=fn or (lambda t, p, s: {ch: 1.0 for ch in spec.channels}),
        params=params or {},
        wants_sense=wants_sense,
    )


def owner_ids(owners):
    return {ch: (b.id if b is not None else None) for ch, b in owners.items()}


# --- channels and classes -------------------------------------------------


def test_channels_are_the_duck_intent_families():
    assert CHANNELS == ("twist", "head", "pose", "mouth", "sound", "skill")


def test_priorities_are_strictly_ordered():
    order = [StopClass.PASSIVE, StopClass.STOPPABLE, StopClass.STOPPING, StopClass.UNSTOPPABLE]
    priorities = [c.priority for c in order]
    assert priorities == sorted(set(priorities))


def test_blocking_classes_are_the_two_holders():
    assert BLOCKING_CLASSES == frozenset({StopClass.UNSTOPPABLE, StopClass.STOPPING})
    assert StopClass.STOPPABLE not in BLOCKING_CLASSES
    assert StopClass.PASSIVE not in BLOCKING_CLASSES


# --- lifetime -------------------------------------------------------------


@pytest.mark.parametrize(
    "lifetime,problem",
    [
        (Lifetime(), "a one-shot behavior needs a duration"),
        (Lifetime(duration=0.0), "duration must be > 0"),
        (Lifetime(duration=-1.0), "duration must be > 0"),
        (Lifetime(duration=float("inf")), "duration must be a finite number"),
        (Lifetime(duration="5"), "duration must be a number"),
    ],
)
def test_invalid_lifetimes_are_named(lifetime, problem):
    assert problem in lifetime.errors()


@pytest.mark.parametrize(
    "lifetime",
    [Lifetime(duration=1.5), Lifetime(looping=True), Lifetime(duration=3.0, looping=True)],
)
def test_valid_lifetimes_have_no_errors(lifetime):
    assert lifetime.errors() == []


def test_expiry_only_applies_to_a_finite_duration():
    assert Lifetime(duration=2.0).is_expired(2.0)
    assert not Lifetime(duration=2.0).is_expired(1.9)
    assert not Lifetime(looping=True).is_expired(1e9)


def test_spec_rejects_an_unknown_or_empty_channel_claim():
    bad = BehaviorSpec("quack", frozenset({"wings"}), StopClass.STOPPABLE, Lifetime(looping=True))
    assert any("unknown channel 'wings'" in e for e in bad.errors())
    empty = BehaviorSpec("quack", frozenset(), StopClass.STOPPABLE, Lifetime(looping=True))
    assert "a behavior must claim at least one channel" in empty.errors()
    assert make("ok", ["head"]).spec.errors() == []


# --- contribution ---------------------------------------------------------


def test_contribution_is_a_channel_dict_of_local_time():
    beh = make("wag", ["twist"], fn=lambda t, p, s: {"twist": t * p["gain"]}, params={"gain": 2.0})
    assert beh.contribute(3.0) == {"twist": 6.0}


def test_a_pure_behavior_never_sees_the_live_sense():
    seen: list[Sense] = []

    def fn(t, p, s):
        seen.append(s)
        return {}

    live = Sense(fallen=True)
    make("pure", ["head"], fn=fn).contribute(0.0, live)
    make("sensing", ["head"], fn=fn, wants_sense=True).contribute(0.0, live)
    assert seen == [EMPTY_SENSE, live]


def test_a_non_dict_contribution_degrades_to_an_abstention():
    assert make("bad", ["head"], fn=lambda t, p, s: None).contribute(0.0) == {}


# --- acceptance 2a: a higher StopClass owns a contested channel -----------


def test_higher_stop_class_owns_a_contested_channel():
    low = make("low", ["head", "sound"], StopClass.STOPPABLE)
    high = make("high", ["head"], StopClass.UNSTOPPABLE)
    assert owner_ids(arbitrate([low, high])) == {
        "twist": None,
        "head": "high",
        "pose": None,
        "mouth": None,
        "sound": "low",
        "skill": None,
    }
    # Order of admission does not change who wins a class contest.
    assert owner_ids(arbitrate([high, low]))["head"] == "high"


def test_a_same_class_tie_goes_to_the_most_recent():
    first = make("first", ["twist"], StopClass.STOPPABLE)
    second = make("second", ["twist"], StopClass.STOPPABLE)
    assert owner_ids(arbitrate([first, second]))["twist"] == "second"


def test_stopping_outranks_stoppable_but_not_unstoppable():
    stoppable = make("s", ["pose"], StopClass.STOPPABLE)
    stopping = make("x", ["pose"], StopClass.STOPPING)
    unstoppable = make("u", ["pose"], StopClass.UNSTOPPABLE)
    assert owner_ids(arbitrate([stoppable, stopping]))["pose"] == "x"
    assert owner_ids(arbitrate([stopping, unstoppable]))["pose"] == "u"


# --- acceptance 2b: PASSIVE only fills an unclaimed channel ---------------


def test_passive_only_fills_an_unclaimed_channel():
    passive = make("idle", ["head", "twist"], StopClass.PASSIVE)
    driver = make("look", ["head"], StopClass.STOPPABLE)
    owners = owner_ids(arbitrate([passive, driver]))
    assert owners["head"] == "look"
    assert owners["twist"] == "idle"
    # With nothing else claiming, the passive layer does own the channel.
    assert owner_ids(arbitrate([passive]))["head"] == "idle"


def test_an_unclaimed_channel_has_no_owner():
    assert owner_ids(arbitrate([])) == dict.fromkeys(CHANNELS)
    assert owner_ids(arbitrate([make("a", ["sound"])]))["mouth"] is None


def test_abstention_falls_a_channel_through_to_the_next_claimant():
    passive = make("idle", ["head"], StopClass.PASSIVE)
    driver = make("look", ["head"], StopClass.STOPPABLE)
    contribs = {"idle": {"head": 0.0}, "look": {"head": None}}
    assert owner_ids(arbitrate([passive, driver], contribs))["head"] == "idle"
    contribs["look"]["head"] = 1.0
    assert owner_ids(arbitrate([passive, driver], contribs))["head"] == "look"


def test_a_missing_contribution_is_an_abstention():
    driver = make("look", ["head"])
    assert owner_ids(arbitrate([driver], {}))["head"] is None


# --- acceptance 2c: UNSTOPPABLE/STOPPING block admission ------------------


@pytest.mark.parametrize("holder_class", sorted(BLOCKING_CLASSES, key=lambda c: c.value))
@pytest.mark.parametrize(
    "newcomer_class", [StopClass.STOPPABLE, StopClass.STOPPING, StopClass.UNSTOPPABLE]
)
def test_a_blocking_incumbent_refuses_a_newcomer_on_its_channel(holder_class, newcomer_class):
    holder = make("holder", ["head", "skill"], holder_class)
    newcomer = make("new", ["head", "twist"], newcomer_class)
    result = admit(newcomer, [holder])
    assert result.admitted is False
    assert result.reason == "blocked"
    assert result.blocked == ("head",)
    assert result.evicted == ()


def test_a_blocking_incumbent_on_another_channel_does_not_block():
    holder = make("holder", ["skill"], StopClass.UNSTOPPABLE)
    result = admit(make("new", ["head"], StopClass.STOPPABLE), [holder])
    assert result.admitted is True
    assert result.blocked == ()


def test_a_passive_newcomer_is_always_admitted_and_evicts_nothing():
    holder = make("holder", ["head"], StopClass.UNSTOPPABLE)
    result = admit(make("idle", ["head"], StopClass.PASSIVE), [holder])
    assert result.admitted is True
    assert result.evicted == ()
    assert result.blocked == ()


def test_stoppable_yields_to_a_stopping_newcomer():
    victim = make("drive", ["twist", "head"], StopClass.STOPPABLE)
    bystander = make("hum", ["sound"], StopClass.STOPPABLE)
    result = admit(make("halt", ["twist"], StopClass.STOPPING), [victim, bystander])
    assert result.admitted is True
    assert [b.id for b in result.evicted] == ["drive"]
    assert result.blocked == ()


def test_a_stoppable_newcomer_evicts_nothing_and_still_takes_the_channel():
    incumbent = make("old", ["head"], StopClass.STOPPABLE)
    result = admit(make("new", ["head"], StopClass.STOPPABLE), [incumbent])
    assert result.admitted is True
    assert result.evicted == ()
    assert result.blocked == ()


def test_admit_does_not_mutate_the_live_set():
    live = [make("a", ["head"], StopClass.STOPPABLE)]
    snapshot = list(live)
    admit(make("b", ["head"], StopClass.STOPPING), live)
    assert live == snapshot


def test_a_passive_newcomer_reports_no_block_even_when_outranked():
    driver = make("look", ["head"], StopClass.STOPPABLE)
    assert admit(make("idle", ["head"], StopClass.PASSIVE), [driver]).blocked == ()


# --- purity ---------------------------------------------------------------


def test_model_imports_only_stdlib_and_the_sense_leaf():
    tree = ast.parse(pathlib.Path(model_mod.__file__).read_text(encoding="utf-8"))
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)
    assert imported <= {
        "__future__",
        "enum",
        "math",
        "dataclasses",
        "typing",
        "microduck_cli.behavior.sense",
    }


def test_behavior_and_spec_are_frozen_value_objects():
    beh = make("a", ["head"])
    with pytest.raises(dataclasses.FrozenInstanceError):
        beh.params = {}  # type: ignore[misc]
    with pytest.raises(dataclasses.FrozenInstanceError):
        beh.spec.name = "b"  # type: ignore[misc]
