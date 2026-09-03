"""Tests for the human-driving gate (``microduck_cli/behavior/human_gate.py``).

Acceptance criterion 2: with a simulated pad stream (``fake.feed_pad_report``
every tick) the daemon must see ZERO motion calls over 200 engine ticks — no
``robot.move``, ``robot.head``, ``robot.look``, ``robot.pose``, ``robot.do``,
``robot.setMode`` and (decision q4, option B) no ``robot.stop`` either — while a
human-driving drop count accumulates and ``robot.sound`` / ``robot.mouth`` keep
flowing.
"""

from __future__ import annotations

import logging
import time
from typing import Callable, Iterator

import pytest

from microduck_cli.behavior.engine import Engine
from microduck_cli.behavior.human_gate import (
    DROP_HUMAN_DRIVING,
    EVENT_END,
    EVENT_START,
    REASON_PAD_ACTIVE,
    REASON_PAD_RECENT,
    REASON_REMOTE_SESSION,
    WITHHELD_CHANNELS,
    GatedSink,
    HumanGate,
    withheld_channels,
)
from microduck_cli.behavior.model import Behavior, BehaviorSpec, Lifetime, StopClass
from microduck_cli.behavior.sense import EMPTY_SENSE, Sense, read_sense
from microduck_cli.behavior.sink import RobotSink
from microduck_cli.ipc import proto
from microduck_cli.ipc.client import RobotClient
from tests.fake_robotd import FakeRobotd

_DEADLINE_S = 5.0

#: Every method a driving human must never see the engine send.
MOTION_METHODS = frozenset(
    {
        proto.ROBOT_MOVE,
        proto.ROBOT_HEAD,
        proto.ROBOT_LOOK,
        proto.ROBOT_POSE,
        proto.ROBOT_DO,
        proto.ROBOT_SET_MODE,
        proto.ROBOT_STOP,
    }
)


class _Records(logging.Handler):
    def __init__(self) -> None:
        super().__init__()
        self.lines: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.lines.append(record.getMessage())


class _Recording:
    """A sink that records what actually reached it."""

    def __init__(self) -> None:
        self.writes: list[dict] = []

    def write(self, pose: dict) -> None:
        self.writes.append(dict(pose))


@pytest.fixture
def fake() -> Iterator[FakeRobotd]:
    with FakeRobotd() as running:
        yield running


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


def _full_pose() -> dict:
    return {
        "twist": (0.2, 0.0, 0.0),
        "head": {"neck_pitch": 0.05},
        "pose": {"z": 0.0, "active": True},
        "skill": "kick_left",
        "mode": "walk",
        "stop": True,
        "mouth": 0.3,
        "sound": {"name": "coo", "hold": False},
    }


def _everything_behavior() -> Behavior:
    spec = BehaviorSpec(
        name="everything",
        channels=frozenset({"twist", "head", "pose", "mouth", "sound", "skill"}),
        stop_class=StopClass.STOPPABLE,
        lifetime=Lifetime(looping=True),
    )
    return Behavior(
        id="everything-0",
        spec=spec,
        fn=lambda _t, _p, _s: {
            "twist": (0.2, 0.0, 0.0),
            "head": {"neck_pitch": 0.05},
            "pose": {"z": 0.0, "active": True},
            "skill": "kick_left",
            "mouth": 0.3,
            "sound": {"name": "coo", "hold": False},
        },
    )


# ── judgement ────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "sense, driving, reason",
    [
        (EMPTY_SENSE, False, None),
        (Sense(pad_active=True), True, REASON_PAD_ACTIVE),
        (Sense(pad_age_s=0.1), True, REASON_PAD_RECENT),
        (Sense(pad_age_s=2.0), False, None),
        (Sense(remote_session=True), True, REASON_REMOTE_SESSION),
        (Sense(pad_age_s=0.1, remote_session=False), True, REASON_PAD_RECENT),
        (Sense(pad_active=False, pad_age_s=0.1), False, None),
        (Sense(pad_active=False, remote_session=True), True, REASON_REMOTE_SESSION),
    ],
)
def test_judge_reads_the_three_pieces_of_evidence(sense, driving, reason) -> None:
    verdict = HumanGate().judge(sense)
    assert verdict.driving is driving
    assert verdict.reason == reason


def test_a_positive_pad_active_false_beats_a_stale_timestamp() -> None:
    """``pad_active=False`` is a READING ("nobody is touching it"), not a missing one."""
    assert HumanGate().judge(Sense(pad_active=False, pad_age_s=0.0)).driving is False
    assert HumanGate().judge(Sense(pad_age_s=0.0)).driving is True


def test_the_pad_window_is_configurable() -> None:
    assert HumanGate(pad_recent_s=5.0).judge(Sense(pad_age_s=2.0)).driving is True
    assert HumanGate(pad_recent_s=0.05).judge(Sense(pad_age_s=2.0)).driving is False


# ── withholding ──────────────────────────────────────────────────────────────────────


def test_the_gate_withholds_motion_and_passes_expression() -> None:
    gate = HumanGate()
    gate.update(Sense(pad_active=True))
    kept = gate.filter_pose(_full_pose())
    assert set(kept) == {"mouth", "sound"}
    assert withheld_channels(_full_pose()) == tuple(sorted(WITHHELD_CHANNELS))


def test_stop_is_withheld_too_because_the_engine_never_overrides_a_person() -> None:
    gate = HumanGate()
    gate.update(Sense(remote_session=True))
    assert gate.filter_pose({"stop": True}) == {}


def test_an_open_gate_passes_the_pose_through_untouched() -> None:
    gate = HumanGate()
    gate.update(EMPTY_SENSE)
    pose = _full_pose()
    assert gate.filter_pose(pose) == pose
    assert gate.withheld == {}


def test_a_fully_withheld_tick_writes_nothing_at_all() -> None:
    inner = _Recording()
    gated = GatedSink(inner, HumanGate(), sense=lambda: Sense(pad_active=True))
    gated.write({"twist": (0.2, 0.0, 0.0)})
    assert inner.writes == [], "sending zeros would be the engine driving too"


def test_the_wrapper_delegates_unknown_attributes_to_the_inner_sink() -> None:
    inner = _Recording()
    inner.marker = "inner"  # type: ignore[attr-defined]
    assert GatedSink(inner).marker == "inner"


def test_a_broken_sense_peek_opens_the_gate_rather_than_killing_the_write() -> None:
    inner = _Recording()

    def _boom():
        raise RuntimeError("the provider is wedged")

    gated = GatedSink(inner, HumanGate(), sense=_boom)
    gated.write({"twist": (0.1, 0.0, 0.0)})
    assert inner.writes == [{"twist": (0.1, 0.0, 0.0)}]


# ── logging: one line per EDGE, a counter per tick ───────────────────────────────────


def test_only_the_transitions_are_logged_never_every_tick(sense_log: _Records) -> None:
    gate = HumanGate()
    for _ in range(100):
        gate.update(Sense(pad_active=True))
        gate.filter_pose({"twist": (0.1, 0.0, 0.0)})
    gate.update(EMPTY_SENSE)

    starts = [line for line in sense_log.lines if f"event={EVENT_START}" in line]
    ends = [line for line in sense_log.lines if f"event={EVENT_END}" in line]
    assert len(starts) == 1, "a line per gated tick would bury every other drop"
    assert len(ends) == 1
    assert gate.withheld[DROP_HUMAN_DRIVING] == 100
    assert gate.withheld["twist"] == 100
    assert gate.transitions == 1
    assert gate.snapshot()["driving"] is False


# ── acceptance 2: 200 engine ticks with a live pad stream ────────────────────────────


def test_a_live_pad_stream_silences_every_motion_channel_for_200_ticks(
    fake: FakeRobotd, sense_log: _Records
) -> None:
    """Acceptance 2, end to end: engine -> gate -> sink -> a real socket.

    ``pad.report`` is pushed on every tick, exactly as ``padd`` does while a thumb
    is on a stick. The behaviour underneath is asking for the whole pose — twist,
    head, posture, a skill — and none of it may reach the daemon; the chirp and
    the beak must.
    """
    # A deep write queue: this test runs 200 ticks as fast as the CPU allows, which
    # is ~100x real time, and a queue sized for 50 Hz would overflow on the burst —
    # a fact about the test harness, not about the gate.
    client = RobotClient(fake.socket_path, clock=time.monotonic, queue_depth=2048)
    client.connect()
    sink = RobotSink(client)
    gate = HumanGate()
    try:
        client.subscribe_pad()
        assert fake.feed_pad_report({"buttons": [], "axes": [0.4, 0.0]}) == 1
        assert _wait_for(lambda: client.peek(proto.PAD_REPORT) is not None)

        providers = client.providers()
        gated = GatedSink(sink, gate, sense=lambda: read_sense(providers, time.monotonic()))
        engine = Engine(gated, providers, sleep=lambda _s: None, hz=50.0)
        engine.admit(_everything_behavior())

        def _pad(_ctx) -> None:
            fake.feed_pad_report({"buttons": [], "axes": [0.4, 0.0]})

        fake.clear_log()
        assert engine.run(tick_seam=_pad, max_ticks=200) == 200

        assert _wait_for(lambda: len(_calls(fake, proto.ROBOT_SOUND)) == 200)
        assert MOTION_METHODS.isdisjoint(set(fake.methods_called()))
        assert len(_calls(fake, proto.ROBOT_MOUTH)) == 200
        assert gate.withheld[DROP_HUMAN_DRIVING] == 200
        assert gate.withheld["twist"] == 200
        assert gate.withheld["skill"] == 200
        assert any(f"event={EVENT_START}" in line for line in sense_log.lines)
        assert sink.drops == {}
    finally:
        sink.close()
        client.close()


def test_the_engine_takes_back_over_once_the_pad_goes_quiet(fake: FakeRobotd) -> None:
    """The mirror of the acceptance: the gate is a timeout, not a latch."""
    client = RobotClient(fake.socket_path, clock=time.monotonic)
    client.connect()
    sink = RobotSink(client)
    gate = HumanGate(pad_recent_s=0.05)
    try:
        client.subscribe_pad()
        fake.feed_pad_report({"buttons": [], "axes": [0.4, 0.0]})
        assert _wait_for(lambda: client.peek(proto.PAD_REPORT) is not None)

        providers = client.providers()
        gated = GatedSink(sink, gate, sense=lambda: read_sense(providers, time.monotonic()))
        engine = Engine(gated, providers, sleep=lambda _s: None, hz=50.0)
        engine.admit(_everything_behavior())

        fake.clear_log()
        engine.run(max_ticks=5)
        assert MOTION_METHODS.isdisjoint(set(fake.methods_called()))

        time.sleep(0.1)  # the pad falls silent
        engine.run(max_ticks=5)
        assert _wait_for(lambda: _calls(fake, proto.ROBOT_MOVE))
        assert gate.transitions == 1
    finally:
        sink.close()
        client.close()


def test_the_driver_seam_observes_the_gate_without_enforcing_it() -> None:
    """The TickBus driver is for RECORDING; enforcement needs the sink wrapper."""
    gate = HumanGate()
    driver = gate.driver()

    class _Ctx:
        sense = Sense(pad_active=True)

    driver(_Ctx())
    assert gate.active is True
    assert getattr(driver, "name") == "human-gate"


def _calls(fake: FakeRobotd, method: str) -> list:
    return [rec for rec in fake.call_log if rec.method == method]
