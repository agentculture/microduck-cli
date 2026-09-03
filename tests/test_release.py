"""Tests for the abnormal-exit release (``microduck_cli/behavior/release.py``).

Acceptance criterion 1, in full: inside ``owning(client)``, an interrupt raised
mid-behaviour must yield EXACTLY ``robot.stop``, ``robot.pose {active: false}``,
``robot.mouth {open: 0}`` and ``robot.sound {hold: false}`` — in that order, and
never ``robot.relax``. When the fake is told to refuse ``robot.stop`` the report
must NAME that failure while the other three still go out, and a clean exit must
send nothing at all.
"""

from __future__ import annotations

import logging
import os
import signal
import threading
import time
from typing import Callable, Iterator

import pytest

from microduck_cli.behavior.release import (
    DEFAULT_SOUND_TAG,
    DROP_RELEASE_FAILED,
    ReleaseReport,
    SignalExit,
    owning,
    release_on_exit,
)
from microduck_cli.ipc import proto
from microduck_cli.ipc.client import RobotClient
from tests.fake_robotd import FakeRobotd

_DEADLINE_S = 5.0

#: The four sends, in the order the release makes them.
EXPECTED_METHODS = [proto.ROBOT_STOP, proto.ROBOT_POSE, proto.ROBOT_MOUTH, proto.ROBOT_SOUND]


class _Records(logging.Handler):
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
    fake.clear_log()
    try:
        yield connected
    finally:
        connected.close()


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


def _released(fake: FakeRobotd) -> bool:
    return [rec.method for rec in fake.call_log] == EXPECTED_METHODS


# ── acceptance 1a: an interrupt mid-behaviour releases, in order, and only that ───────


def test_a_keyboard_interrupt_mid_behaviour_releases_exactly_four_sends(
    fake: FakeRobotd, client: RobotClient
) -> None:
    ctx = owning(client)
    client.notify(proto.ROBOT_MOVE, {"vx": 0.2, "vy": 0.0, "vyaw": 0.0})
    assert _wait_for(lambda: proto.ROBOT_MOVE in fake.methods_called())
    fake.clear_log()
    with pytest.raises(KeyboardInterrupt):
        with ctx as owner:
            raise KeyboardInterrupt

    assert _wait_for(lambda: _released(fake)), fake.methods_called()
    stop, pose, mouth, sound = fake.call_log
    assert stop.kind == "request", "robot.stop is the one send whose answer matters"
    assert pose.params == {"active": False}
    assert mouth.params == {"open": 0.0}
    assert sound.params == {"tag": DEFAULT_SOUND_TAG, "hold": False}
    assert all(rec.is_notification for rec in (pose, mouth, sound))

    assert owner.report is not None
    assert owner.report.complete
    assert owner.report.sent == tuple(EXPECTED_METHODS)


def test_release_never_sends_robot_relax(fake: FakeRobotd, client: RobotClient) -> None:
    """A duck on two legs FALLS when it goes limp — relax is the arm's answer, not this one."""
    ctx = owning(client)
    with pytest.raises(RuntimeError):
        with ctx:
            raise RuntimeError("a rule blew up")
    assert _wait_for(lambda: _released(fake))
    assert proto.ROBOT_RELAX not in fake.methods_called()
    assert proto.ROBOT_RELAX not in [step.method for step in (release_on_exit(client).steps)]


def test_a_clean_exit_sends_nothing_so_a_deliberate_hold_survives(
    fake: FakeRobotd, client: RobotClient
) -> None:
    with owning(client) as owner:
        client.notify(proto.ROBOT_POSE, {"z": 0.02, "active": True})
        assert _wait_for(lambda: proto.ROBOT_POSE in fake.methods_called())
        fake.clear_log()
    time.sleep(0.05)
    assert fake.methods_called() == []
    assert owner.report is None, "None IS the assertion that nothing was released"


# ── acceptance 1b: one refusal never skips the other three ───────────────────────────


def test_a_refused_stop_is_named_and_the_other_three_still_go_out(
    fake: FakeRobotd, client: RobotClient, sense_log: _Records
) -> None:
    fake.refuse(proto.ROBOT_STOP, message="the bus is down")
    with pytest.raises(KeyboardInterrupt):
        with owning(client) as owner:
            raise KeyboardInterrupt

    assert _wait_for(lambda: _released(fake)), fake.methods_called()
    report = owner.report
    assert report is not None
    assert not report.complete
    assert report.failed == (proto.ROBOT_STOP,)
    assert "the bus is down" in report.errors[proto.ROBOT_STOP]
    assert report.sent == (proto.ROBOT_POSE, proto.ROBOT_MOUTH, proto.ROBOT_SOUND)
    assert "INCOMPLETE" in report.describe()
    assert any(f"event={DROP_RELEASE_FAILED}" in line for line in sense_log.lines)


def test_an_accepted_false_answer_is_a_failure_not_a_success(
    fake: FakeRobotd, client: RobotClient
) -> None:
    """``accepted: false`` is the daemon saying no — reporting it as released would lie."""

    class _Refusing:
        def request(self, *_a, **_k):
            return {"accepted": False, "reason": "the robot has fallen"}

        def notify(self, *_a, **_k):
            return True

    report = release_on_exit(_Refusing())
    assert report.failed == (proto.ROBOT_STOP,)
    assert report.errors[proto.ROBOT_STOP] == "the robot has fallen"


def test_a_dead_link_fails_every_send_and_says_so(fake: FakeRobotd, client: RobotClient) -> None:
    client.close()
    report = release_on_exit(client, timeout=0.2)
    assert report.attempted == tuple(EXPECTED_METHODS)
    assert report.failed == tuple(EXPECTED_METHODS)
    assert not report.complete
    assert "deadman" in report.describe(), "the limit is stated, not implied"


def test_no_client_is_an_empty_report_not_an_error() -> None:
    report = release_on_exit(None)
    assert report == ReleaseReport()
    assert not report.complete
    assert report.as_dict()["steps"] == []


# ── signals: SIGINT and SIGTERM both unwind the block ────────────────────────────────


@pytest.mark.skipif(
    threading.current_thread() is not threading.main_thread(),
    reason="signal handlers can only be installed on the main thread",
)
def test_sigterm_inside_the_context_releases_and_propagates(
    fake: FakeRobotd, client: RobotClient
) -> None:
    """SIGTERM's default action kills the process outright and releases NOTHING.

    The handler installed by the context is the only reason this exit path exists
    at all — and it is restored on the way out, so the next test (and the caller's
    own handler) is unaffected.
    """
    before = signal.getsignal(signal.SIGTERM)
    ctx = owning(client)

    def _raise_sigterm() -> None:
        assert signal.getsignal(signal.SIGTERM) is not before
        os.kill(os.getpid(), signal.SIGTERM)
        time.sleep(0.5)  # pragma: no cover - the signal lands first

    with pytest.raises(SignalExit):
        with ctx as owner:
            _raise_sigterm()
    assert signal.getsignal(signal.SIGTERM) is before
    assert _wait_for(lambda: _released(fake))
    assert owner.report is not None
    assert owner.report.complete


@pytest.mark.skipif(
    threading.current_thread() is not threading.main_thread(),
    reason="signal handlers can only be installed on the main thread",
)
def test_sigint_inside_the_context_raises_keyboard_interrupt(
    fake: FakeRobotd, client: RobotClient
) -> None:
    before = signal.getsignal(signal.SIGINT)
    ctx = owning(client)

    def _raise_sigint() -> None:
        os.kill(os.getpid(), signal.SIGINT)
        time.sleep(0.5)  # pragma: no cover - the signal lands first

    with pytest.raises(KeyboardInterrupt):
        with ctx:
            _raise_sigint()
    assert signal.getsignal(signal.SIGINT) is before
    assert _wait_for(lambda: _released(fake))


def test_a_broken_on_release_hook_never_replaces_the_real_failure(
    fake: FakeRobotd, client: RobotClient
) -> None:
    def _boom(_report: ReleaseReport) -> None:
        raise ValueError("the diagnostic is broken")

    with pytest.raises(ZeroDivisionError):
        with owning(client, on_release=_boom) as owner:
            _ = 1 / 0
    assert owner.report is not None
    assert owner.report.complete


def test_the_report_is_json_serialisable(fake: FakeRobotd, client: RobotClient) -> None:
    report = release_on_exit(client, sound_tag="coo")
    payload = report.as_dict()
    assert payload["complete"] is True
    assert [step["method"] for step in payload["steps"]] == EXPECTED_METHODS
    assert payload["steps"][3]["params"] == {"tag": "coo", "hold": False}
    assert "Released the duck" in report.describe()
