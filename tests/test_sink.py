"""Tests for the target sink (``microduck_cli/behavior/sink.py``).

Everything here runs against :class:`tests.fake_robotd.FakeRobotd` over a real
unix socket, so a pass means the bytes the daemon would receive are right — not
that a mock was called.

Acceptance criteria exercised here:

3. a rule's step command reaches the fake UNCHANGED — no EMA, no rate limit, no
   clamp on this side of the socket (h2: robotd's ``cmd_alpha`` is the plant);
5. ``write()`` never blocks: with the fake wedged, 100 ticks cost well under a
   millisecond apiece and the drops accumulate under named reasons.
"""

from __future__ import annotations

import logging
import statistics
import time
from typing import Callable, Iterator

import pytest

from microduck_cli.behavior.engine import Engine
from microduck_cli.behavior.intents import Intent, default_registry
from microduck_cli.behavior.model import Behavior, BehaviorSpec, Lifetime, StopClass
from microduck_cli.behavior.rule_engine import RuleEngine
from microduck_cli.behavior.rules import RulesConfig
from microduck_cli.behavior.sense import EMPTY_SENSE, Sense
from microduck_cli.behavior.sink import (
    DROP_NOTIFY_FAILED,
    DROP_REQUEST_QUEUE_FULL,
    DROP_REQUEST_REFUSED,
    DROP_UNENCODABLE,
    RobotSink,
    encode_head,
    encode_mode,
    encode_mouth,
    encode_pose,
    encode_skill,
    encode_sound,
    encode_stop,
    encode_twist,
)
from microduck_cli.ipc import proto
from microduck_cli.ipc.client import DROP_QUEUE_FULL, RobotClient
from tests.fake_robotd import FakeRobotd

_DEADLINE_S = 5.0


class _Records(logging.Handler):
    """Captures the ``microduck.sense`` lines a drop emits, fully formatted."""

    def __init__(self) -> None:
        super().__init__()
        self.lines: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.lines.append(record.getMessage())


@pytest.fixture
def fake() -> Iterator[FakeRobotd]:
    with FakeRobotd() as running:
        yield running


@pytest.fixture
def client(fake: FakeRobotd) -> Iterator[RobotClient]:
    connected = RobotClient(fake.socket_path, clock=time.monotonic)
    connected.connect()
    try:
        yield connected
    finally:
        connected.close()


@pytest.fixture
def sink(client: RobotClient) -> Iterator[RobotSink]:
    made = RobotSink(client)
    try:
        yield made
    finally:
        made.close()


@pytest.fixture
def sense_log() -> Iterator[_Records]:
    handler = _Records()
    logger = logging.getLogger("microduck.sense")
    logger.addHandler(handler)
    previous = logger.level
    logger.setLevel(logging.DEBUG)
    try:
        yield handler
    finally:
        logger.removeHandler(handler)
        logger.setLevel(previous)


def _wait_for(predicate: Callable[[], bool], timeout: float = _DEADLINE_S) -> bool:
    deadline = time.perf_counter() + timeout
    while time.perf_counter() < deadline:
        if predicate():
            return True
        time.sleep(0.005)
    return predicate()


def _calls(fake: FakeRobotd, method: str) -> list:
    return [rec for rec in fake.call_log if rec.method == method]


# ── encoding: the one place a channel becomes wire params ────────────────────────────


def test_twist_encodes_to_move_with_the_vyaw_rename() -> None:
    encoded = encode_twist((0.3, -0.1, 1.25))
    assert encoded.method == proto.ROBOT_MOVE
    assert encoded.params == {"vx": 0.3, "vy": -0.1, "vyaw": 1.25}


def test_twist_accepts_a_mapping_naming_wz_or_vyaw() -> None:
    assert encode_twist({"vx": 0.1, "vyaw": 0.5}).params == {"vx": 0.1, "vyaw": 0.5}
    assert encode_twist({"vyaw": 0.5}).params == {"vyaw": 0.5}


def test_an_unencodable_twist_is_refused_not_coerced() -> None:
    assert encode_twist((0.1, 0.2)).error
    assert encode_twist("fast").error
    assert encode_twist((0.1, 0.2, "quick")).error


def test_head_splits_look_targets_from_joint_angles() -> None:
    look = encode_head({"x": 0.2, "y": 0.0, "z": 0.1, "neck_pitch": -0.2})
    assert look.method == proto.ROBOT_LOOK
    assert look.params == {"x": 0.2, "y": 0.0, "z": 0.1, "neck_pitch": -0.2}

    joints = encode_head({"neck_pitch": 0.05, "head_yaw": -0.1})
    assert joints.method == proto.ROBOT_HEAD
    assert joints.params == {"neck_pitch": 0.05, "head_yaw": -0.1}
    assert not proto.is_notification(proto.ROBOT_LOOK)
    assert proto.is_notification(proto.ROBOT_HEAD)


def test_pose_mouth_sound_skill_stop_and_mode_encodings() -> None:
    assert encode_pose({"z": 0.01, "active": True}).params == {"z": 0.01, "active": True}
    assert encode_mouth(0.4).params == {"open": 0.4}
    assert encode_mouth({"open": 1.0}).params == {"open": 1.0}
    assert encode_sound({"name": "chirp", "hold": False}).params == {"tag": "chirp", "hold": False}
    assert encode_sound({"name": "wheee"}).params == {"tag": "wheee"}
    assert encode_skill("kick_left").params == {"skill": "kick_left"}
    assert encode_stop(True).method == proto.ROBOT_STOP
    assert encode_mode("roller").params == {"mode": "roller"}


def test_only_a_pose_key_that_is_present_is_sent(sink: RobotSink, fake: FakeRobotd) -> None:
    """A channel nobody owns sends NOTHING — the daemon's last value stays in charge."""
    fake.clear_log()
    sink.write({"mouth": 0.5})
    assert _wait_for(lambda: _calls(fake, proto.ROBOT_MOUTH))
    assert fake.methods_called() == [proto.ROBOT_MOUTH]


def test_an_unencodable_channel_is_a_named_drop(
    sink: RobotSink, fake: FakeRobotd, sense_log: _Records
) -> None:
    fake.clear_log()
    sink.write({"twist": "sideways"})
    assert sink.drops[DROP_UNENCODABLE] == 1
    assert any(f"event={DROP_UNENCODABLE}" in line for line in sense_log.lines)
    assert fake.methods_called() == []


# ── acceptance 3: a rule's step command reaches the fake unchanged ───────────────────


def _rules_config() -> RulesConfig:
    return RulesConfig.from_dict(
        {
            "schema_version": 1,
            "react": [
                {
                    "id": "creep-when-close",
                    "when": {"field": "tof_nearest_m", "op": "lt", "value": 0.4},
                    "run": "move",
                    "params": {"vx": 0.3, "vyaw": -1.5},
                    "duration_s": 2.0,
                }
            ],
        }
    )


def test_a_rules_move_reaches_the_daemon_byte_for_byte(
    fake: FakeRobotd, client: RobotClient, sink: RobotSink
) -> None:
    """Acceptance 3. No EMA, no rate limit, no clamp between the rule and the socket.

    The rule asks for ``vx = 0.3`` and ``vyaw = -1.5`` — both exactly at the intent
    layer's admitted ceiling, which is where a helpful client-side clamp would be
    most tempted to shave a value. The daemon must receive those numbers, and the
    untouched ``vy = 0.0`` the validator filled in, with nothing in between.
    """
    now = [1000.0]
    rules = RuleEngine(_rules_config(), default_registry(), lambda: now[0])
    engine = Engine(sink, clock=lambda: now[0], sleep=lambda _s: None, hz=50.0)

    sense = Sense(tof_nearest_m=0.2)
    result = rules.evaluate(sense)
    assert [fire.rule_id for fire in result.fires] == ["creep-when-close"]
    engine.admit(result.fires[0].behavior, now=now[0])

    fake.clear_log()
    engine.run(max_ticks=1)

    assert _wait_for(lambda: _calls(fake, proto.ROBOT_MOVE))
    record = _calls(fake, proto.ROBOT_MOVE)[0]
    assert record.is_notification, "a continuous intent must go out without an id"
    assert record.params == {"vx": 0.3, "vy": 0.0, "vyaw": -1.5}


def test_fifty_ticks_of_the_same_twist_send_fifty_identical_frames(
    fake: FakeRobotd, sink: RobotSink
) -> None:
    """Continuous channels are never de-duplicated: a stream that stops is a deadman."""
    fake.clear_log()
    for _ in range(50):
        sink.write({"twist": (0.05, 0.0, 0.0)})
    assert _wait_for(lambda: len(_calls(fake, proto.ROBOT_MOVE)) == 50)
    assert {tuple(sorted(rec.params.items())) for rec in _calls(fake, proto.ROBOT_MOVE)} == {
        (("vx", 0.05), ("vy", 0.0), ("vyaw", 0.0))
    }


# ── discrete channels: requests, edge-triggered, refusals named ──────────────────────


def test_a_skill_goes_out_as_a_request_and_only_on_change(
    fake: FakeRobotd, sink: RobotSink
) -> None:
    fake.clear_log()
    for _ in range(20):
        sink.write({"skill": "kick_left"})
    assert _wait_for(lambda: _calls(fake, proto.ROBOT_DO))
    time.sleep(0.05)
    calls = _calls(fake, proto.ROBOT_DO)
    assert len(calls) == 1, "a per-tick robot.do would fire the same skill 50 times a second"
    assert calls[0].params == {"skill": "kick_left"}
    assert not calls[0].is_notification, "a discrete intent must carry an id"

    sink.write({"skill": "kick_right"})
    assert _wait_for(lambda: len(_calls(fake, proto.ROBOT_DO)) == 2)

    sink.forget_discrete("skill")
    sink.write({"skill": "kick_right"})
    assert _wait_for(lambda: len(_calls(fake, proto.ROBOT_DO)) == 3)


def test_a_repeated_skill_after_the_owning_behavior_expires_is_sent_again(
    fake: FakeRobotd, sink: RobotSink
) -> None:
    """Edge-triggering must not suppress a skill forever.

    Before the fix, ``_last_discrete`` kept ``"skill"`` pinned to ``"roulade"``
    across ticks, so a SECOND behaviour admitted after the first one expired and
    asked for the very same skill would never be sent — the edge-trigger saw no
    change and dropped it. A write whose pose no longer carries the channel (the
    owning behaviour expired/was evicted) must forget that memory so the next
    admission of the same skill is a genuinely new request.
    """
    fake.clear_log()
    sink.write({"skill": "roulade"})
    assert _wait_for(lambda: _calls(fake, proto.ROBOT_DO))
    time.sleep(0.05)
    assert len(_calls(fake, proto.ROBOT_DO)) == 1

    # The behaviour that owned "skill" expired: subsequent ticks compose a pose
    # with no "skill" key at all.
    sink.write({})
    sink.write({"twist": (0.0, 0.0, 0.0)})

    # A later behaviour admits the SAME skill again.
    sink.write({"skill": "roulade"})
    assert _wait_for(lambda: len(_calls(fake, proto.ROBOT_DO)) == 2)
    calls = _calls(fake, proto.ROBOT_DO)
    assert [c.params for c in calls] == [{"skill": "roulade"}, {"skill": "roulade"}]


def test_a_refused_request_is_a_named_drop_with_the_daemons_reason(
    fake: FakeRobotd, sink: RobotSink, sense_log: _Records
) -> None:
    """A bare ``robotd --fake`` has no policy, so every skill comes back refused."""
    sink.write({"skill": "ground_pick"})
    assert _wait_for(lambda: sink.drops[DROP_REQUEST_REFUSED] == 1)
    assert "no policy configured for that skill" in sink.refusals[proto.ROBOT_DO]
    assert any(f"event={DROP_REQUEST_REFUSED}" in line for line in sense_log.lines)


def test_stop_and_mode_ride_the_same_seam_as_requests(fake: FakeRobotd, sink: RobotSink) -> None:
    fake.clear_log()
    sink.write({"stop": True, "mode": "roller"})
    assert _wait_for(lambda: _calls(fake, proto.ROBOT_STOP) and _calls(fake, proto.ROBOT_SET_MODE))
    assert _calls(fake, proto.ROBOT_SET_MODE)[0].params == {"mode": "roller"}
    assert all(not rec.is_notification for rec in fake.call_log)


def test_a_mode_intent_firing_sends_exactly_one_set_mode(fake: FakeRobotd, sink: RobotSink) -> None:
    """A mode intent's contribution must actually reach the daemon as setMode.

    Before the fix, ``_contribute_mode`` only ever produced a zero twist, so
    the sink's mode encoder was unreachable and ``robot.setMode`` was never
    sent no matter how a mode rule/intent fired. Admitting the intent and
    writing its own contribution through the sink must yield exactly one
    ``robot.setMode`` call carrying the requested mode.
    """
    admission = default_registry().admit(Intent("mode", {"mode": "roller"}))
    assert admission.admitted
    assert admission.behavior is not None
    pose = admission.behavior.contribute(0.0, EMPTY_SENSE)
    assert pose["mode"] == "roller"

    fake.clear_log()
    sink.write(pose)
    assert _wait_for(lambda: _calls(fake, proto.ROBOT_SET_MODE))
    time.sleep(0.05)
    calls = _calls(fake, proto.ROBOT_SET_MODE)
    assert len(calls) == 1
    assert calls[0].params == {"mode": "roller"}


def test_a_reply_without_an_accepted_field_is_not_a_refusal(
    fake: FakeRobotd, sink: RobotSink
) -> None:
    """``robot.look`` answers with the head pose it adopted and no verdict at all."""
    sink.write({"head": {"x": 0.1, "y": 0.0, "z": 0.05, "neck_pitch": 0.0}})
    assert _wait_for(lambda: _calls(fake, proto.ROBOT_LOOK))
    time.sleep(0.05)
    assert sink.drops[DROP_REQUEST_REFUSED] == 0


# ── acceptance 5: write() never blocks ───────────────────────────────────────────────


def test_write_stays_far_under_a_tick_with_the_fake_wedged(
    fake: FakeRobotd, sense_log: _Records
) -> None:
    """Acceptance 5. A wedged daemon costs frames, never ticks.

    The client's bounded queue is filled first (the kernel's socket buffer has to
    fill before the queue can), so the 100 measured ticks are all running against
    a genuinely full pipe: they must cost well under a millisecond each and land on
    a named drop.

    The budget is spent per TICK, so mean and median are the criteria and both are
    held to the full 1 ms. A hard cap on the single worst SAMPLE is deliberately
    not asserted: under ``pytest -n auto`` this test shares a core with 30
    siblings, and the outliers that produces (9 ms observed on a run whose median
    was 12 us) measure the OS scheduler, not the sink — the worst sample is
    reported in the failure message instead. The 1 ms bound is also what CI's
    ``--cov`` run needs: coverage tracing costs about 0.2 ms per call here against
    ~0.02 ms untraced.
    """
    client = RobotClient(fake.socket_path, clock=time.monotonic, queue_depth=32)
    client.connect()
    sink = RobotSink(client)
    try:
        fake.wedge()
        for _ in range(60_000):
            if not client.notify(proto.ROBOT_MOVE, {"vx": 0.0, "vy": 0.0, "vyaw": 0.0}):
                break
        assert client.drops[DROP_QUEUE_FULL] > 0, "the client's write queue never filled"

        samples: list[float] = []
        for tick in range(100):
            started = time.perf_counter()
            sink.write({"twist": (0.1, 0.0, 0.0), "mouth": 0.2 + tick * 0.001})
            samples.append(time.perf_counter() - started)
        total_s = sum(samples)
        worst_s = max(samples)
        mean_ms = total_s * 1e3 / len(samples)
        median_ms = statistics.median(samples) * 1e3
        assert mean_ms < 1.0, (
            f"sink.write() averaged {mean_ms:.3f} ms with the daemon wedged "
            f"(worst {worst_s * 1e3:.3f} ms)"
        )
        assert median_ms < 1.0, f"sink.write() median was {median_ms:.3f} ms"
        # 200 sends (twist + mouth per tick). How many of them the full queue
        # rejects is not fixed — the writer thread frees slots as the scheduler
        # lets it — so the invariant asserted here is the one that matters: every
        # single send either landed or was NAMED, and none vanished quietly.
        accounted = (
            sink.drops[DROP_NOTIFY_FAILED]
            + sink.sent[proto.ROBOT_MOVE]
            + sink.sent[proto.ROBOT_MOUTH]
        )
        assert accounted == 200
        assert sink.drops[DROP_NOTIFY_FAILED] > 0, "a wedged daemon must cost frames"
    finally:
        fake.unwedge()
        sink.close()
        client.close()

    assert any(f"event={DROP_NOTIFY_FAILED}" in line for line in sense_log.lines)


def test_a_slow_discrete_reply_never_reaches_the_tick(
    fake: FakeRobotd, client: RobotClient
) -> None:
    """A request waits on the worker thread; the loop is not even aware of it."""
    sink = RobotSink(client, request_timeout_s=0.2, queue_depth=4, repeat_discrete=True)
    try:
        fake.delay(300)
        samples: list[float] = []
        for _ in range(100):
            started = time.perf_counter()
            sink.write({"skill": "kick_left"})
            samples.append(time.perf_counter() - started)
        mean_ms = sum(samples) * 1e3 / len(samples)
        assert mean_ms < 1.0, (
            f"sink.write() averaged {mean_ms:.3f} ms behind a slow daemon "
            f"(worst {max(samples) * 1e3:.3f} ms)"
        )
        assert statistics.median(samples) * 1e3 < 1.0
        assert sink.drops[DROP_REQUEST_QUEUE_FULL] > 0, "a backlog must drop, never queue forever"
    finally:
        fake.delay(0)
        sink.close()


# ── the engine seam ──────────────────────────────────────────────────────────────────


def _all_channel_behavior() -> Behavior:
    spec = BehaviorSpec(
        name="everything",
        channels=frozenset({"twist", "head", "pose", "mouth", "sound"}),
        stop_class=StopClass.STOPPABLE,
        lifetime=Lifetime(looping=True),
    )
    return Behavior(
        id="everything-0",
        spec=spec,
        fn=lambda _t, _p, _s: {
            "twist": (0.1, 0.0, 0.0),
            "head": {"neck_pitch": 0.02},
            "pose": {"z": 0.0, "active": True},
            "mouth": 0.25,
            "sound": {"name": "coo", "hold": False},
        },
    )


def test_one_engine_tick_writes_every_owned_channel_once(fake: FakeRobotd, sink: RobotSink) -> None:
    now = [10.0]
    engine = Engine(sink, clock=lambda: now[0], sleep=lambda _s: None)
    engine.admit(_all_channel_behavior(), now=now[0])
    fake.clear_log()
    engine.run(max_ticks=1)

    expected = {
        proto.ROBOT_MOVE,
        proto.ROBOT_HEAD,
        proto.ROBOT_POSE,
        proto.ROBOT_MOUTH,
        proto.ROBOT_SOUND,
    }
    assert _wait_for(lambda: expected.issubset(set(fake.methods_called())))
    assert sorted(fake.methods_called()) == sorted(expected)
    assert all(rec.is_notification for rec in fake.call_log)
    assert sink.drops == {}
    assert _all_channel_behavior().contribute(0.0, EMPTY_SENSE)["mouth"] == 0.25
