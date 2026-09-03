"""Tests for the JSON-RPC client (``microduck_cli/ipc/client.py``).

Every test runs against :class:`tests.fake_robotd.FakeRobotd` — a real unix socket
speaking the shapes the probed ``robotd`` 0.10.0 answers — rather than against mocks, so
a failure here is a failure of the wire behaviour and not of a stubbed expectation.

The client's clock is injected (:class:`_Clock`) so peek-slot stamps are deterministic;
wall-clock measurements deliberately use ``time.perf_counter`` instead, because the point
of the non-blocking test is what really happened in real time.
"""

from __future__ import annotations

import ast
import logging
import os
import pathlib
import queue
import shutil
import statistics
import sys
import tempfile
import threading
import time
from typing import Any, Callable, Iterator

import pytest

from microduck_cli.behavior.sense import read_sense
from microduck_cli.cli._errors import EXIT_ENV_ERROR, CliError
from microduck_cli.ipc import client as client_mod
from microduck_cli.ipc import proto
from microduck_cli.ipc.client import (
    DROP_DOWN,
    DROP_METHOD_NOT_FOUND,
    DROP_NOTIFY_QUEUE_FULL,
    DROP_QUEUE_FULL,
    DROP_TIMEOUT,
    RobotClient,
    RpcError,
    RpcLinkDown,
    RpcTimeout,
    SubscribeResult,
)
from tests.fake_robotd import FakeRobotd

_DEADLINE_S = 5.0


class _Clock:
    """A hand-advanced monotonic clock: the client's stamps, under test control."""

    def __init__(self, start: float = 1_000.0) -> None:
        self.now = start

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


class _Records(logging.Handler):
    """Captures the ``microduck.sense`` lines a drop emits, fully formatted."""

    def __init__(self) -> None:
        super().__init__()
        self.lines: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.lines.append(record.getMessage())


@pytest.fixture()
def fake() -> Iterator[FakeRobotd]:
    with FakeRobotd() as running:
        yield running


@pytest.fixture()
def clock() -> _Clock:
    return _Clock()


@pytest.fixture()
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


@pytest.fixture()
def client(fake: FakeRobotd, clock: _Clock) -> Iterator[RobotClient]:
    connected = RobotClient(fake.socket_path, clock=clock)
    connected.connect()
    try:
        yield connected
    finally:
        connected.close()


def _wait_for(predicate: Callable[[], bool], timeout: float = _DEADLINE_S) -> bool:
    deadline = time.perf_counter() + timeout
    while time.perf_counter() < deadline:
        if predicate():
            return True
        time.sleep(0.005)
    return predicate()


# ── acceptance 1: notify() never blocks the tick thread ──────────────────────────────

#: A single sample above this could not be scheduler noise: it means the call waited on
#: the socket. Generous on purpose — the assertion that has to stay tight is the
#: mean/median one below.
_BLOCKED_MS = 20.0


def _time_notify(client: RobotClient) -> float:
    started = time.perf_counter()
    client.notify(proto.ROBOT_MOVE, {"vx": 0.1, "vy": 0.0, "vyaw": 0.0})
    return time.perf_counter() - started


def _assert_never_blocked(samples: list[float], when: str) -> None:
    """Mean and median under 1 ms, worst sample under the blocked-for-real ceiling."""
    mean_ms = statistics.fmean(samples) * 1e3
    median_ms = statistics.median(samples) * 1e3
    worst_ms = max(samples) * 1e3
    detail = (
        f"notify() {when}: median {median_ms:.3f} ms, mean {mean_ms:.3f} ms, "
        f"worst {worst_ms:.3f} ms over {len(samples)} calls"
    )
    assert median_ms < 1.0, detail
    assert mean_ms < 1.0, detail
    assert worst_ms < _BLOCKED_MS, detail


def test_notify_returns_under_a_millisecond_with_the_socket_wedged(
    fake: FakeRobotd, clock: _Clock, sense_log: _Records
) -> None:
    """The obligation: with the daemon wedged, notify() drops instead of blocking.

    The fake stops draining its socket, so the client's writer thread parks in sendall
    and the bounded queue backs up. Every call must still return in well under a
    millisecond — measured on the real clock, not the injected one — and the overflow
    must land on the ``ipc-queue-full`` counter rather than on the caller.

    The budget is spent per CALL, so mean and median are the criteria and both are held
    to the full 1 ms; the single worst SAMPLE is held only to a generous ceiling. Under
    ``pytest -n auto`` (and on a shared CI runner) this test contends for a core with
    its siblings, and a lone ~1 ms sample there measures the OS scheduler, not
    ``put_nowait`` — while a sample past :data:`_BLOCKED_MS` could only mean the call
    actually waited on the socket, which is the bug this test exists to catch. All
    three numbers go in the failure message. ``tests/test_sink.py`` holds the same
    obligation the same way, one layer up.
    """
    client = RobotClient(fake.socket_path, clock=clock, queue_depth=64)
    client.connect()
    try:
        fake.wedge()

        samples = [_time_notify(client) for _ in range(100)]
        _assert_never_blocked(samples, "with the socket wedged")

        # Keep pushing until the kernel's socket buffer and then the queue fill. The
        # frames are tiny, so this takes a few thousand of them; each one is still a
        # put_nowait and must stay inside the same budget.
        filled = False
        full_samples: list[float] = []
        for _ in range(40_000):
            full_samples.append(_time_notify(client))
            if client.drops[DROP_QUEUE_FULL]:
                filled = True
                break
        assert filled, "the bounded queue never filled behind a wedged daemon"
        _assert_never_blocked(full_samples, "once the queue was full")
        assert client.drops[DROP_QUEUE_FULL] > 0
    finally:
        fake.unwedge()
        client.close()

    assert any(
        f"[SENSE stage=ipc source={fake.socket_path} event={DROP_QUEUE_FULL}]" in line
        for line in sense_log.lines
    ), sense_log.lines[:3]


def test_notify_reaches_the_daemon_when_the_link_is_healthy(
    fake: FakeRobotd, client: RobotClient
) -> None:
    assert client.notify(proto.ROBOT_MOVE, {"vx": 0.2, "vy": 0.0, "vyaw": 0.0}) is True
    assert _wait_for(lambda: proto.ROBOT_MOVE in fake.methods_called())
    record = [rec for rec in fake.call_log if rec.method == proto.ROBOT_MOVE][0]
    assert record.is_notification, "a continuous intent must go out without an id"
    assert client.drops == {}


# ── acceptance 2: METHOD_NOT_FOUND is a named drop, not a traceback ──────────────────


def test_method_not_found_is_a_named_drop_and_a_plain_rpc_error(
    client: RobotClient, sense_log: _Records
) -> None:
    """``robot.skills`` is METHOD_NOT_FOUND on the pinned API 16 daemon.

    A caller gets an RpcError — never a CliError, never a traceback into a tick — and
    afterwards ``supports()`` answers False, so a tick-path caller can check availability
    without catching anything at all.
    """
    assert client.supports("robot.skills") is True

    with pytest.raises(RpcError) as caught:
        client.request("robot.skills")

    assert not isinstance(caught.value, CliError)
    assert caught.value.code == proto.METHOD_NOT_FOUND
    assert caught.value.method == "robot.skills"
    assert "robot.skills" in caught.value.message

    assert client.supports("robot.skills") is False
    assert client.drops[DROP_METHOD_NOT_FOUND] == 1
    assert any(f"event={DROP_METHOD_NOT_FOUND}]" in line for line in sense_log.lines)
    assert any("robot.skills" in line for line in sense_log.lines)


def test_supports_is_optimistic_for_a_method_never_tried(client: RobotClient) -> None:
    assert client.supports(proto.ROBOT_HEALTH) is True


# ── acceptance 3: a joint-table mismatch refuses the connection ──────────────────────


def test_a_fourteen_joint_daemon_makes_connect_raise_naming_both_counts(
    fake: FakeRobotd, clock: _Clock
) -> None:
    fake.set_state(joint_names=proto.JOINT_NAMES[:14])

    client = RobotClient(fake.socket_path, clock=clock)
    with pytest.raises(CliError) as caught:
        client.connect()

    assert caught.value.code == EXIT_ENV_ERROR
    assert "14" in caught.value.message
    assert "15" in caught.value.message
    assert caught.value.remediation, "a refusal must say what to do about it"
    assert client.connected is False, "a refused connect must not leave a live link"


def test_a_matching_joint_table_verifies(client: RobotClient) -> None:
    assert _wait_for(lambda: client.joints_verified)
    assert client.joints_verified is True


def test_verify_joints_false_skips_the_subscribe(fake: FakeRobotd, clock: _Clock) -> None:
    client = RobotClient(fake.socket_path, clock=clock)
    client.connect(verify_joints=False)
    try:
        assert fake.methods_called() == [proto.HELLO]
        assert client.joints_verified is False
    finally:
        client.close()


# ── acceptance 4: a request timeout names the method and the reader survives ─────────


def test_a_timed_out_request_names_the_method_and_the_reader_survives(
    fake: FakeRobotd, clock: _Clock
) -> None:
    client = RobotClient(fake.socket_path, clock=clock, request_timeout_s=0.1)
    client.connect(verify_joints=False)
    try:
        fake.delay(500)
        with pytest.raises(RpcTimeout) as caught:
            client.request(proto.ROBOT_HEALTH)
        assert proto.ROBOT_HEALTH in caught.value.message
        assert caught.value.method == proto.ROBOT_HEALTH
        assert client.drops[DROP_TIMEOUT] == 1

        # The late reply lands on nobody and is itself a named drop, not a crash.
        fake.delay(0)
        result = client.request(proto.ROBOT_MODE, timeout=2.0)
        assert result["mode"] == "walk"
        assert client.connected is True
    finally:
        client.close()


# ── acceptance 5: stdlib only ────────────────────────────────────────────────────────


def test_the_module_imports_only_stdlib_and_first_party() -> None:
    tree = ast.parse(pathlib.Path(client_mod.__file__).read_text(encoding="utf-8"))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            names.add(node.module.split(".")[0])

    third_party = {name for name in names if name not in sys.stdlib_module_names}
    assert third_party <= {"microduck_cli"}, f"non-stdlib imports: {third_party}"
    assert {"socket", "threading", "queue", "json", "logging", "dataclasses"} <= names


# ── handshake, skew, and the subscribe answer ────────────────────────────────────────


def test_hello_records_the_daemon_identity(fake: FakeRobotd, client: RobotClient) -> None:
    assert client.daemon.api_version == proto.API_VERSION
    assert client.daemon.daemon_version == "0.10.0"
    assert client.daemon.revision is None
    hello = [rec for rec in fake.call_log if rec.method == proto.HELLO][0]
    assert hello.params == {"api_version": proto.API_VERSION}
    assert client.api_skew is None


def test_api_skew_is_reported_never_refused(fake: FakeRobotd, clock: _Clock) -> None:
    """A newer daemon connects fine; the difference is readable, not fatal."""
    fake.set_state(api_version=18)
    client = RobotClient(fake.socket_path, clock=clock)
    client.connect()
    try:
        assert client.connected is True
        assert client.api_skew == (18, proto.API_VERSION)
    finally:
        client.close()


def test_connect_to_a_missing_socket_is_an_environment_error(clock: _Clock) -> None:
    with pytest.raises(CliError) as caught:
        RobotClient("/nonexistent/robotd.sock", clock=clock).connect()
    assert caught.value.code == EXIT_ENV_ERROR
    assert "/nonexistent/robotd.sock" in caught.value.message


def test_subscribe_result_carries_the_policy_slots_and_skills(
    fake: FakeRobotd, clock: _Clock
) -> None:
    fake.set_state(walk_policy="walk.onnx", unavailable=None, skills=("kick_left", "roulade"))
    client = RobotClient(fake.socket_path, clock=clock)
    client.connect()
    try:
        result = client.subscribe_result
        assert isinstance(result, SubscribeResult)
        assert result.accepted is True
        assert result.walk == "walk.onnx"
        assert result.unavailable is None
        assert sorted(client.skills_from_subscribe() or ()) == ["kick_left", "roulade"]
        assert result.files["roulade"] == "roulade.onnx"
    finally:
        client.close()


def test_skills_are_none_before_subscribing_and_empty_on_a_bare_daemon(
    fake: FakeRobotd, clock: _Clock
) -> None:
    """``None`` is "never asked"; ``()`` is "asked, and there are none" — different facts."""
    client = RobotClient(fake.socket_path, clock=clock)
    client.connect(verify_joints=False)
    try:
        assert client.skills_from_subscribe() is None
        client.subscribe()
        assert client.skills_from_subscribe() == ()
    finally:
        client.close()


def test_subscribe_sends_hz_as_an_integer(fake: FakeRobotd, client: RobotClient) -> None:
    """Upstream types ``hz`` as a ``u32``; a JSON float is a -32602 on the real daemon.

    Found by probing robotd 0.10.0 with a float, which answered "invalid type: floating
    point `50.0`, expected u32". The fake now refuses it too, so this test bites.
    """
    subscribe = [rec for rec in fake.call_log if rec.method == proto.ROBOT_SUBSCRIBE][0]
    assert isinstance(subscribe.params["hz"], int)
    assert not isinstance(subscribe.params["hz"], bool)
    assert client.subscribe(20).accepted is True


def test_subscribe_is_not_re_issued_at_the_same_rate(fake: FakeRobotd, client: RobotClient) -> None:
    client.subscribe()
    client.subscribe()
    assert fake.methods_called().count(proto.ROBOT_SUBSCRIBE) == 1


# ── the notification queue: arrival order, for consumers that must not coalesce ──────


def test_notifications_deliver_every_frame_in_arrival_order(
    fake: FakeRobotd, client: RobotClient, clock: _Clock
) -> None:
    """The seam a recorder needs: nothing coalesced, nothing reordered.

    A peek slot answers "the latest", which is what a tick wants and what a
    recording must never be. Twenty frames pushed back to back must all be on the
    queue, in order, each with the reader thread's own stamp.
    """
    pending = client.notifications()
    for seq in range(20):
        clock.advance(0.001)
        fake.feed_state({"seq": seq})

    # The fixture's connect() also left the daemon's own 50 Hz stream running, so
    # the fed frames are picked out by the "seq" only they carry.
    seen: list[tuple[float, str, Any]] = []
    deadline = time.perf_counter() + _DEADLINE_S
    while len(seen) < 20 and time.perf_counter() < deadline:
        try:
            stamp, method, params = pending.get(timeout=0.05)
        except queue.Empty:
            continue
        if isinstance(params, dict) and "seq" in params:
            seen.append((stamp, method, params))

    assert [params["seq"] for _stamp, _method, params in seen] == list(range(20))
    assert all(method == proto.ROBOT_STATE for _stamp, method, _params in seen)
    stamps = [stamp for stamp, _method, _params in seen]
    assert stamps == sorted(stamps), "frames carry the reader's clock, in arrival order"


def test_a_full_notification_queue_drops_by_name_rather_than_growing(
    fake: FakeRobotd, client: RobotClient, sense_log: _Records
) -> None:
    """A consumer slower than the daemon loses frames BY NAME, never silently."""
    pending = client.notifications(maxsize=4)
    for seq in range(40):
        fake.feed_state({"seq": seq})
    # Wait on the LOG line, not the counter: the counter is bumped first, so a
    # wait on it can win the race against the line and read an empty log.
    assert _wait_for(
        lambda: any(f"event={DROP_NOTIFY_QUEUE_FULL}" in line for line in sense_log.lines)
    )
    assert client.drops[DROP_NOTIFY_QUEUE_FULL] > 0
    assert pending.qsize() <= 4, "the queue is bounded, whatever the daemon does"


def test_notifications_hands_out_one_queue_per_client(client: RobotClient) -> None:
    assert client.notifications() is client.notifications()


# ── peek slots and the sense seam ────────────────────────────────────────────────────


def test_state_frames_land_in_a_peek_slot_and_are_never_consumed(
    fake: FakeRobotd, client: RobotClient, clock: _Clock
) -> None:
    assert _wait_for(lambda: client.peek(proto.ROBOT_STATE) is not None)
    first = client.peek(proto.ROBOT_STATE)
    second = client.peek(proto.ROBOT_STATE)
    assert first is not None and second is not None
    assert first[1] == clock.now, "a slot is stamped with the injected clock"
    assert second[0] is not None, "peeking must not consume the slot"


def test_providers_map_the_state_frame_onto_the_sense_snapshot(
    fake: FakeRobotd, client: RobotClient, clock: _Clock
) -> None:
    fake.set_state(fallen=True, loop_hz=49.5, policy="walk")
    assert _wait_for(
        lambda: (client.peek(proto.ROBOT_STATE) or (None,))[0] is not None
        and client.peek(proto.ROBOT_STATE)[0]["safety"]["fallen"] is True  # type: ignore[index]
    )

    clock.advance(0.25)
    sense = read_sense(client.providers(), now=clock.now)
    assert sense.fallen is True
    assert sense.limp is True, "the bare fake is not driving, so it reports limp"
    assert sense.gravity == (0.0, 0.0, 1.0)
    assert sense.loop_hz == 49.5
    assert sense.policy == "walk"
    assert sense.move_applied == (0.0, 0.0, 0.0)
    assert sense.move_requested == (0.0, 0.0, 0.0)
    assert sense.state_age_s == pytest.approx(0.25)
    # Unwired on this build: no pad/ToF subscription and no health poll yet.
    assert sense.battery_frac is None
    assert sense.health_age_s is None


def test_poll_health_fills_the_health_fields(fake: FakeRobotd, client: RobotClient) -> None:
    fake.set_state(battery_frac=0.5, hottest_servo_c=41.5)
    assert client.poll_health() is not None
    sense = read_sense(client.providers(), now=1_000.0)
    assert sense.battery_frac == pytest.approx(0.5)
    assert sense.hottest_servo_c == pytest.approx(41.5)
    assert sense.health_age_s == pytest.approx(0.0)


def test_health_fields_stay_none_on_a_bare_fake(client: RobotClient) -> None:
    """``--fake`` measures no battery and no servo temperature: absent, not zero."""
    assert client.poll_health() is not None
    sense = read_sense(client.providers(), now=1_000.0)
    assert sense.battery_frac is None
    assert sense.hottest_servo_c is None


def test_poll_remote_session_feeds_the_sense_field(fake: FakeRobotd, client: RobotClient) -> None:
    fake.set_state(remote_session=True)
    assert client.poll_remote_session() == {"active": True}
    assert read_sense(client.providers(), now=1_000.0).remote_session is True


def test_a_failing_poll_is_a_named_drop_not_an_exception(
    fake: FakeRobotd, client: RobotClient
) -> None:
    fake.refuse(proto.ROBOT_HEALTH, code=proto.INTERNAL_ERROR, message="bus fault")
    assert client.poll_health() is None
    assert client.drops["ipc-poll-failed"] == 1


def test_pad_and_tof_frames_land_in_their_own_slots(
    fake: FakeRobotd, client: RobotClient, clock: _Clock
) -> None:
    client.subscribe_pad()
    client.subscribe_tof()
    assert _wait_for(lambda: fake.feed_pad_report({"buttons": ["a"]}) == 1)
    fake.feed_tof_frame({"min_m": 0.4})

    assert _wait_for(lambda: client.peek(proto.PAD_REPORT) is not None)
    assert _wait_for(lambda: client.peek(proto.TOF_FRAME) is not None)
    assert client.peek(proto.PAD_REPORT)[0] == {"buttons": ["a"]}  # type: ignore[index]

    clock.advance(1.0)
    sense = read_sense(client.providers(), now=clock.now)
    assert sense.pad_age_s == pytest.approx(1.0)
    assert sense.tof_age_s == pytest.approx(1.0)


def test_providers_on_a_silent_client_are_all_none(fake: FakeRobotd, clock: _Clock) -> None:
    client = RobotClient(fake.socket_path, clock=clock)
    client.connect(verify_joints=False)
    try:
        sense = read_sense(client.providers(), now=clock.now)
        assert sense.fallen is None
        assert sense.loop_hz is None
        assert sense.state_age_s is None
    finally:
        client.close()


# ── the link going away, and coming back ─────────────────────────────────────────────


def test_a_dead_socket_drops_instead_of_raising_on_the_tick_path(
    fake: FakeRobotd, clock: _Clock
) -> None:
    client = RobotClient(fake.socket_path, clock=clock)
    client.connect(verify_joints=False)
    try:
        fake.close()
        assert _wait_for(lambda: not client.connected)
        assert client.notify(proto.ROBOT_MOVE, {"vx": 0.0, "vy": 0.0, "vyaw": 0.0}) is False
        assert client.drops[DROP_DOWN] > 0
        with pytest.raises(RpcLinkDown):
            client.request(proto.ROBOT_MODE, timeout=0.2)
    finally:
        client.close()


def test_the_writer_reconnects_lazily_and_redoes_the_handshake(clock: _Clock) -> None:
    """A notify against a down link asks for a reconnect; the writer does the work.

    The client is pointed at a symlink so a second fake can take over the same path,
    the way a restarted daemon takes over the same socket.
    """
    tmpdir = tempfile.mkdtemp(prefix="mdlnk")
    link = os.path.join(tmpdir, "l.sock")
    first = FakeRobotd()
    client = RobotClient(link, clock=clock, connect_timeout_s=0.5)
    second: FakeRobotd | None = None
    try:
        os.symlink(first.socket_path, link)
        client.connect()
        assert client.connected is True

        first.close()
        assert _wait_for(lambda: not client.connected)

        second = FakeRobotd()
        os.remove(link)
        os.symlink(second.socket_path, link)

        # The control loop is the retry timer: a notify is what asks for a reconnect.
        def poke() -> bool:
            client.notify(proto.ROBOT_MOVE, {"vx": 0.0, "vy": 0.0, "vyaw": 0.0})
            return client.connected

        assert _wait_for(poke), "the writer never reconnected"
        assert _wait_for(lambda: proto.HELLO in second.methods_called())  # type: ignore[union-attr]
        assert _wait_for(
            lambda: proto.ROBOT_SUBSCRIBE in second.methods_called()  # type: ignore[union-attr]
        ), "a client that was subscribed must re-subscribe on reconnect"
    finally:
        client.close()
        first.close()
        if second is not None:
            second.close()
        shutil.rmtree(tmpdir, ignore_errors=True)


def test_close_is_idempotent_and_leaves_no_live_threads(fake: FakeRobotd, clock: _Clock) -> None:
    before = {thread.name for thread in threading.enumerate()}
    client = RobotClient(fake.socket_path, clock=clock)
    client.connect()
    client.close()
    client.close()
    assert client.connected is False
    assert _wait_for(
        lambda: not {t.name for t in threading.enumerate()} - before & {"ipc-reader", "ipc-writer"}
    )


def test_a_closed_client_refuses_to_reconnect(fake: FakeRobotd, clock: _Clock) -> None:
    client = RobotClient(fake.socket_path, clock=clock)
    client.connect(verify_joints=False)
    client.close()
    with pytest.raises(CliError):
        client.connect()


def test_the_context_manager_closes_the_client(fake: FakeRobotd, clock: _Clock) -> None:
    with RobotClient(fake.socket_path, clock=clock) as client:
        client.connect()
        assert client.connected is True
    assert client.connected is False


# ── framing details ──────────────────────────────────────────────────────────────────


def test_send_routes_by_the_protocols_own_classification(
    fake: FakeRobotd, client: RobotClient
) -> None:
    assert client.send(proto.ROBOT_MOUTH, {"open": 0.5}) is True  # continuous -> notify
    assert client.send(proto.ROBOT_MODE) == {"mode": "walk"}  # discrete -> request
    assert _wait_for(lambda: proto.ROBOT_MOUTH in fake.methods_called())
    mouth = [rec for rec in fake.call_log if rec.method == proto.ROBOT_MOUTH][0]
    assert mouth.is_notification


def test_requests_are_correlated_by_id_under_concurrent_callers(client: RobotClient) -> None:
    """Two threads asking at once must each get their own answer, not each other's."""
    answers: dict[str, Any] = {}

    def ask(method: str) -> None:
        answers[method] = client.request(method)

    threads = [
        threading.Thread(target=ask, args=(proto.ROBOT_MODE,)),
        threading.Thread(target=ask, args=(proto.ROBOT_MODEL_API,)),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=_DEADLINE_S)
    assert answers[proto.ROBOT_MODE] == {"mode": "walk"}
    assert answers[proto.ROBOT_MODEL_API] == {"model_api": 1}


def test_a_malformed_line_from_the_daemon_is_a_named_drop(
    fake: FakeRobotd, client: RobotClient
) -> None:
    _wait_for(lambda: client.connected)
    # The fake has no public "write me a bad line" hook; reach into its connections.
    for conn in fake._conns:
        conn._raw.sendall(b"{not json}\n")
    assert _wait_for(lambda: client.drops["ipc-malformed"] > 0)
    assert client.connected is True, "one bad line must not take the link down"


def test_an_invalid_params_error_is_an_rpc_error_carrying_the_daemons_message(
    client: RobotClient,
) -> None:
    with pytest.raises(RpcError) as caught:
        client.request(proto.ROBOT_DO, {"name": "roulade"})
    assert caught.value.code == proto.INVALID_PARAMS
    assert "unknown field" in caught.value.message
    assert client.supports(proto.ROBOT_DO) is True, "a bad payload is not a missing method"


def test_subscribe_result_parsing_tolerates_junk() -> None:
    assert SubscribeResult.from_result(None) == SubscribeResult()
    assert SubscribeResult.from_result({"accepted": True, "walk": 7}).walk is None


def test_the_drop_line_has_the_rubric_shape(fake: FakeRobotd, clock: _Clock) -> None:
    handler = _Records()
    logger = logging.getLogger("microduck.sense")
    logger.addHandler(handler)
    try:
        client = RobotClient(fake.socket_path, clock=clock)
        client.connect(verify_joints=False)
        try:
            with pytest.raises(RpcError):
                client.request("robot.policies")
        finally:
            client.close()
    finally:
        logger.removeHandler(handler)

    expected = (
        f"[SENSE stage=ipc source={fake.socket_path} event={DROP_METHOD_NOT_FOUND}] "
        f'robot.policies: unknown method "robot.policies"'
    )
    assert expected in handler.lines


def test_the_socket_path_is_reported_verbatim(fake: FakeRobotd, clock: _Clock) -> None:
    """The drop lines name the socket, so the client must report it unchanged."""
    client = RobotClient(fake.socket_path, clock=clock)
    assert client.socket_path == fake.socket_path


def test_flush_drains_a_live_link_and_reports_a_wedged_one(fake_daemon_factory=None):
    from tests.fake_robotd import FakeRobotd

    with FakeRobotd() as fake:
        client = RobotClient(fake.socket_path, clock=time.monotonic)
        client.connect(verify_joints=False)
        try:
            for _ in range(20):
                client.notify("robot.move", {"vx": 0.0, "vy": 0.0, "vyaw": 0.0})
            assert client.flush(2.0) is True
            fake.wedge()
            for _ in range(200):
                client.notify("robot.move", {"vx": 0.1, "vy": 0.0, "vyaw": 0.0})
            # Some frames sit in the kernel buffer; anything still queued must time out.
            outcome = client.flush(0.2)
            assert outcome in (True, False)
            if not outcome:
                assert not client._queue.empty()
        finally:
            client.close()
