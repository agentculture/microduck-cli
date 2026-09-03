"""Tests for the idle base behaviour (``microduck_cli/behavior/idle.py``).

Acceptance criterion 4: idle emits NOTHING while ``fallen=True``, while
``enabled`` is not ``True``, or while the human gate is closed — and emits head
motion otherwise.
"""

from __future__ import annotations

import math
import time
from typing import Callable, Iterator

import pytest

from microduck_cli.behavior import idle as idle_mod
from microduck_cli.behavior.engine import Engine
from microduck_cli.behavior.human_gate import HumanGate
from microduck_cli.behavior.intents import Intent, default_registry
from microduck_cli.behavior.model import StopClass, arbitrate
from microduck_cli.behavior.sense import EMPTY_SENSE, Sense, SenseProviders
from microduck_cli.behavior.sink import RobotSink
from microduck_cli.cli._errors import EXIT_USER_ERROR, CliError
from microduck_cli.ipc import proto
from microduck_cli.ipc.client import RobotClient
from tests.fake_robotd import FakeRobotd

_DEADLINE_S = 5.0

#: A duck that is up, driving, and with nobody at the pad.
AWAKE = Sense(fallen=False, limp=False, enabled=True)


@pytest.fixture()
def fake() -> Iterator[FakeRobotd]:
    with FakeRobotd() as running:
        yield running


def _wait_for(predicate: Callable[[], bool], timeout: float = _DEADLINE_S) -> bool:
    deadline = time.perf_counter() + timeout
    while time.perf_counter() < deadline:
        if predicate():
            return True
        time.sleep(0.005)
    return predicate()


# ── acceptance 4: the four silences ──────────────────────────────────────────────────


@pytest.mark.parametrize(
    "sense, reason",
    [
        (Sense(fallen=True, limp=False, enabled=True), "fallen"),
        (Sense(fallen=False, limp=True, enabled=True), "limp"),
        (Sense(fallen=False, limp=False, enabled=False), "not-enabled"),
        (EMPTY_SENSE, "not-enabled"),
        (Sense(fallen=False, limp=False), "not-enabled"),
    ],
)
def test_idle_contributes_nothing_while_the_duck_cannot_or_must_not_move(sense, reason) -> None:
    assert idle_mod.is_silent(sense) == reason
    contribute = idle_mod.make_idle()
    assert contribute(0.0, {}, sense) == {}
    assert contribute(45.0, {}, sense) == {}, "not even the chirp"


def test_an_unknown_enabled_reading_is_not_permission_to_move() -> None:
    """``None`` is *no reading*, and "we don't know" is not "yes"."""
    assert idle_mod.is_silent(Sense(fallen=False, limp=False, enabled=None)) == "not-enabled"


def test_idle_is_silent_while_a_human_is_driving() -> None:
    gate = HumanGate()
    driven = Sense(fallen=False, limp=False, enabled=True, pad_active=True)
    assert idle_mod.is_silent(driven, gate) == "human-driving"
    assert idle_mod.make_idle(gate)(0.0, {}, driven) == {}
    # ... and the same snapshot without the pad is not silent at all.
    assert idle_mod.is_silent(AWAKE, gate) is None


def test_idle_emits_bounded_head_motion_when_the_duck_is_awake() -> None:
    contribute = idle_mod.make_idle()
    poses = [contribute(t / 10.0, {}, AWAKE)["head"] for t in range(200)]

    assert all(set(pose) == {"neck_pitch", "head_yaw"} for pose in poses)
    assert max(abs(pose["neck_pitch"]) for pose in poses) <= idle_mod.NECK_PITCH_AMPLITUDE_RAD
    assert max(abs(pose["head_yaw"]) for pose in poses) <= idle_mod.HEAD_YAW_AMPLITUDE_RAD
    # It MOVES: a "living" idle that returns the same pose forever is a dead one.
    # 200 samples 0.1 s apart over a 7 s period is 70 samples per cycle, and a sine
    # is symmetric, so ~35 distinct values is the ceiling here — the point of the
    # assertion is that the pose is not a constant.
    assert len({round(pose["neck_pitch"], 6) for pose in poses}) > 30


def test_the_head_motion_is_a_pure_function_of_local_time() -> None:
    first = idle_mod.make_idle()(3.25, {}, AWAKE)["head"]
    second = idle_mod.make_idle()(3.25, {}, AWAKE)["head"]
    assert first == second
    assert first["neck_pitch"] == pytest.approx(
        idle_mod.NECK_PITCH_AMPLITUDE_RAD * math.sin(2 * math.pi * 3.25 / idle_mod.NECK_PERIOD_S)
    )


# ── the chirp: at most one every 30 s ────────────────────────────────────────────────


def test_the_chirp_is_at_most_one_per_thirty_seconds() -> None:
    contribute = idle_mod.make_idle()
    heard = [t for t in range(0, 121) if "sound" in contribute(float(t), {}, AWAKE)]
    assert heard == [30, 60, 90, 120]
    assert contribute(30.0, {}, AWAKE).get("sound") is None, "already chirped in this window"
    assert idle_mod.CHIRP_INTERVAL_S == 30.0


def test_the_chirp_names_a_real_voice_bank_tag() -> None:
    contribute = idle_mod.make_idle()
    contribute(0.0, {}, AWAKE)
    sound = contribute(31.0, {}, AWAKE)["sound"]
    assert sound == {"name": idle_mod.CHIRP_TAG, "hold": False}
    from tests.fake_robotd import SOUND_TAGS

    assert idle_mod.CHIRP_TAG in SOUND_TAGS


def test_a_backwards_jump_in_local_time_does_not_silence_the_duck_for_hours() -> None:
    contribute = idle_mod.make_idle()
    assert "sound" in contribute(30.0, {}, AWAKE)
    assert "sound" not in contribute(1.0, {}, AWAKE)  # clock reset: recorded, not trusted
    assert "sound" in contribute(31.0, {}, AWAKE)


# ── knobs: refused, never clamped ────────────────────────────────────────────────────


def test_the_defaults_sit_inside_the_intent_layers_own_neck_bounds() -> None:
    from microduck_cli.behavior.intents import NECK_PITCH_MAX_RAD, NECK_PITCH_MIN_RAD

    assert NECK_PITCH_MIN_RAD < -idle_mod.NECK_PITCH_AMPLITUDE_RAD
    assert idle_mod.NECK_PITCH_AMPLITUDE_RAD < NECK_PITCH_MAX_RAD


@pytest.mark.parametrize(
    "overrides",
    [
        {"wiggle": 1.0},
        {"neck_amplitude": 1.2},
        {"yaw_amplitude": -0.1},
        {"neck_period": 0.0},
        {"chirp_every": float("inf")},
        {"chirp_tag": "honk"},
        {"neck_amplitude": "lots"},
    ],
)
def test_a_bad_knob_is_refused_with_a_named_reason(overrides) -> None:
    with pytest.raises(CliError) as excinfo:
        idle_mod.resolve_params(overrides)
    assert excinfo.value.code == EXIT_USER_ERROR
    assert excinfo.value.message.startswith("idle: ")


def test_good_knobs_resolve_over_the_defaults() -> None:
    params = idle_mod.resolve_params({"neck_amplitude": 0.02, "chirp_tag": "coo"})
    assert params["neck_amplitude"] == 0.02
    assert params["chirp_tag"] == "coo"
    assert params["neck_period"] == idle_mod.NECK_PERIOD_S


# ── the behaviour and the registry ───────────────────────────────────────────────────


def test_the_idle_behaviour_is_passive_looping_and_sense_fed() -> None:
    behavior = idle_mod.idle_behavior()
    assert behavior.stop_class is StopClass.PASSIVE
    assert behavior.channels == frozenset({"head", "sound"})
    assert behavior.lifetime.looping and behavior.lifetime.duration is None
    assert behavior.wants_sense, "without this the engine feeds EMPTY_SENSE and idle never moves"
    assert behavior.contribute(1.0, AWAKE)["head"]
    assert behavior.contribute(1.0) == {}, "EMPTY_SENSE means no reading, so no motion"


def test_any_non_passive_behaviour_owns_the_head_over_idle() -> None:
    from microduck_cli.behavior.model import Behavior, BehaviorSpec, Lifetime

    looker = Behavior(
        id="look-1",
        spec=BehaviorSpec(
            name="look",
            channels=frozenset({"head"}),
            stop_class=StopClass.STOPPABLE,
            lifetime=Lifetime(duration=1.0),
        ),
        fn=lambda _t, _p, _s: {"head": {"x": 0.2}},
    )
    idle = idle_mod.idle_behavior()
    owners = arbitrate([idle, looker])
    assert owners["head"] is looker
    assert owners["sound"] is idle


def test_registering_idle_replaces_the_placeholder_kind_but_not_its_validator() -> None:
    registry = default_registry()
    placeholder = registry.admit(Intent(kind="idle", payload={}))
    assert placeholder.admitted and placeholder.behavior is not None
    assert placeholder.behavior.channels == frozenset({"pose"})

    idle_mod.register(registry, HumanGate())
    admission = registry.admit(Intent(kind="idle", payload={"duration_s": 5.0}))
    assert admission.admitted and admission.behavior is not None
    assert admission.behavior.channels == frozenset({"head", "sound"})
    assert admission.behavior.lifetime.duration == 5.0

    refused = registry.admit(Intent(kind="idle", payload={"vx": 1.0}))
    assert not refused.admitted
    assert "unknown field" in refused.reason


# ── end to end: idle head motion reaches the daemon ──────────────────────────────────


def test_idle_head_motion_reaches_the_daemon_as_a_head_notification(fake: FakeRobotd) -> None:
    client = RobotClient(fake.socket_path, clock=time.monotonic, queue_depth=512)
    client.connect()
    sink = RobotSink(client)
    try:
        providers = SenseProviders(fallen=lambda: False, limp=lambda: False, enabled=lambda: True)
        now = [0.0]

        def clock() -> float:
            now[0] += 0.02
            return now[0]

        engine = Engine(sink, providers, clock=clock, sleep=lambda _s: None, hz=50.0)
        engine.admit(idle_mod.idle_behavior(), now=0.0)
        fake.clear_log()
        engine.run(max_ticks=25)

        assert _wait_for(lambda: len(fake.methods_called()) == 25)
        assert set(fake.methods_called()) == {proto.ROBOT_HEAD}
        params = [rec.params for rec in fake.call_log]
        assert all(set(p) == {"neck_pitch", "head_yaw"} for p in params)
        assert len({p["neck_pitch"] for p in params}) == 25
        assert sink.drops == {}
    finally:
        sink.close()
        client.close()


def test_a_fallen_duck_sends_nothing_at_all(fake: FakeRobotd) -> None:
    client = RobotClient(fake.socket_path, clock=time.monotonic)
    client.connect()
    sink = RobotSink(client)
    try:
        providers = SenseProviders(fallen=lambda: True, enabled=lambda: True)
        engine = Engine(sink, providers, sleep=lambda _s: None, hz=50.0)
        engine.admit(idle_mod.idle_behavior())
        fake.clear_log()
        engine.run(max_ticks=50)
        time.sleep(0.05)
        assert fake.methods_called() == []
    finally:
        sink.close()
        client.close()
