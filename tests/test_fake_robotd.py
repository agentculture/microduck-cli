"""Tests for the in-process fake robotd (``tests/fake_robotd.py``).

These cover the fake's own contract — the wire shape, the call log, and the three
fault levers (refuse / wedge / delay) — so a later client test that fails can be
read as a client bug rather than a fake bug.
"""

from __future__ import annotations

import json
import os
import socket
import time
from typing import Any

import pytest

from tests.fake_robotd import (
    API_VERSION,
    JOINT_NAMES,
    METHOD_NOT_FOUND,
    ROBOT_MODEL,
    FakeRobotd,
)

_TIMEOUT = 2.0


class _Client:
    """A minimal NDJSON JSON-RPC client, just enough to exercise the fake."""

    def __init__(self, path: str, timeout: float = _TIMEOUT) -> None:
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.settimeout(timeout)
        self.sock.connect(path)
        self._buf = b""
        self._next_id = 0

    def send(self, method: str, params: Any = None, *, notify: bool = False) -> int | None:
        message: dict[str, Any] = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            message["params"] = params
        if notify:
            request_id = None
        else:
            self._next_id += 1
            request_id = self._next_id
            message["id"] = request_id
        self.sock.sendall((json.dumps(message) + "\n").encode())
        return request_id

    def read(self, timeout: float | None = None) -> dict[str, Any]:
        if timeout is not None:
            self.sock.settimeout(timeout)
        while b"\n" not in self._buf:
            chunk = self.sock.recv(65536)
            if not chunk:
                raise EOFError("fake closed the connection")
            self._buf += chunk
        line, self._buf = self._buf.split(b"\n", 1)
        return json.loads(line)

    def call(self, method: str, params: Any = None, timeout: float | None = None) -> dict[str, Any]:
        request_id = self.send(method, params)
        while True:
            message = self.read(timeout)
            if message.get("id") == request_id:
                return message

    def close(self) -> None:
        self.sock.close()


@pytest.fixture()
def fake():
    with FakeRobotd() as instance:
        yield instance


@pytest.fixture()
def client(fake: FakeRobotd):
    conn = _Client(fake.socket_path)
    try:
        yield conn
    finally:
        conn.close()


# -- socket path -------------------------------------------------------------


def test_socket_path_is_short_and_unique() -> None:
    """Safe under ``pytest -n auto``: private dir, well under the AF_UNIX limit."""
    with FakeRobotd() as one, FakeRobotd() as two:
        assert one.socket_path != two.socket_path
        for fake in (one, two):
            assert len(fake.socket_path.encode()) < 100
            assert os.path.exists(fake.socket_path)


def test_close_removes_the_socket_directory() -> None:
    fake = FakeRobotd()
    path = fake.socket_path
    fake.close()
    assert not os.path.exists(path)
    fake.close()  # idempotent


# -- handshake and unknown methods -------------------------------------------


def test_hello_returns_the_api_version_model_and_joints(client: _Client) -> None:
    answer = client.call("hello", {"api_version": API_VERSION})
    result = answer["result"]
    assert result["api_version"] == API_VERSION
    assert result["model"] == ROBOT_MODEL
    assert result["joint_names"] == list(JOINT_NAMES)
    assert len(result["joint_names"]) == 15


def test_unknown_method_is_method_not_found_naming_the_method(client: _Client) -> None:
    answer = client.call("robot.doesNotExist")
    assert "result" not in answer
    assert answer["error"]["code"] == METHOD_NOT_FOUND
    assert "robot.doesNotExist" in answer["error"]["message"]


def test_an_unknown_notification_is_recorded_but_never_answered(
    fake: FakeRobotd, client: _Client
) -> None:
    client.send("robot.doesNotExist", {"a": 1}, notify=True)
    client.send("hello", {"api_version": API_VERSION}, notify=True)
    answer = client.call("robot.health")
    assert "result" in answer
    assert fake.methods_called() == ["robot.doesNotExist", "hello", "robot.health"]


def test_a_malformed_line_is_a_parse_error(client: _Client) -> None:
    client.sock.sendall(b"{not json\n")
    answer = client.read()
    assert answer["error"]["code"] == -32700


# -- the call log (obligation o2) --------------------------------------------


def test_call_log_preserves_order_and_separates_notifications_from_requests(
    fake: FakeRobotd, client: _Client
) -> None:
    client.call("hello", {"api_version": API_VERSION})
    client.send("robot.move", {"vx": 0.1, "vy": 0.0, "vyaw": 0.0}, notify=True)
    client.send("robot.mouth", {"open": 0.5}, notify=True)
    client.call("robot.enable", {"on": True})
    client.send("robot.head", {"pitch": 0.0}, notify=True)
    client.call("robot.stop")

    log = fake.call_log
    assert [rec.method for rec in log] == [
        "hello",
        "robot.move",
        "robot.mouth",
        "robot.enable",
        "robot.head",
        "robot.stop",
    ]
    assert [rec.kind for rec in log] == [
        "request",
        "notification",
        "notification",
        "request",
        "notification",
        "request",
    ]
    assert [rec.seq for rec in log] == [1, 2, 3, 4, 5, 6]
    for rec in log:
        assert rec.is_notification == (rec.kind == "notification")
        assert (rec.id is None) == rec.is_notification
    assert log[1].params == {"vx": 0.1, "vy": 0.0, "vyaw": 0.0}


def test_call_log_sequence_is_monotonic_across_connections(fake: FakeRobotd) -> None:
    first = _Client(fake.socket_path)
    second = _Client(fake.socket_path)
    try:
        first.call("hello")
        second.call("robot.health")
        first.call("robot.mode")
    finally:
        first.close()
        second.close()
    seqs = [rec.seq for rec in fake.call_log]
    assert seqs == sorted(seqs)
    assert len(set(seqs)) == len(seqs)


def test_clear_log_empties_it(fake: FakeRobotd, client: _Client) -> None:
    client.call("hello")
    assert fake.call_log
    fake.clear_log()
    assert fake.call_log == []


# -- refusals ----------------------------------------------------------------


def test_refuse_makes_a_method_answer_an_error_until_cleared(
    fake: FakeRobotd, client: _Client
) -> None:
    fake.refuse("robot.init", code=7, message="servo bus is down")
    answer = client.call("robot.init")
    assert answer["error"]["code"] == 7
    assert answer["error"]["message"] == "servo bus is down"

    fake.allow("robot.init")
    assert client.call("robot.init")["result"]["accepted"] is True


def test_allow_with_no_argument_clears_every_refusal(fake: FakeRobotd, client: _Client) -> None:
    fake.refuse("robot.init")
    fake.refuse("robot.stop")
    fake.allow()
    assert "result" in client.call("robot.init")
    assert "result" in client.call("robot.stop")


# -- state shaping -----------------------------------------------------------


def test_set_state_shapes_health_and_refusals(fake: FakeRobotd, client: _Client) -> None:
    fake.set_state(fallen=True, healthy=False, battery_frac=0.1, reason="control loop stalled")
    health = client.call("robot.health")["result"]
    assert health["healthy"] is False
    assert health["reason"] == "control loop stalled"
    assert health["battery"]["percent"] == pytest.approx(10.0)

    enable = client.call("robot.enable", {"on": True})["result"]
    assert enable["accepted"] is False
    assert "fallen" in enable["reason"]


def test_set_state_shapes_the_skill_list(fake: FakeRobotd, client: _Client) -> None:
    fake.set_state(skills=["kick_left"])
    policies = client.call("robot.policies")["result"]
    assert [entry["name"] for entry in policies["skills"]] == ["kick_left"]
    assert client.call("robot.do", {"skill": "roulade"})["result"]["accepted"] is False
    assert client.call("robot.do", {"skill": "kick_left"})["result"]["accepted"] is True


def test_joint_names_override_simulates_a_table_mismatch(fake: FakeRobotd, client: _Client) -> None:
    fake.set_state(joint_names=("only_one",))
    assert client.call("hello")["result"]["joint_names"] == ["only_one"]


def test_set_state_rejects_an_unknown_field(fake: FakeRobotd) -> None:
    with pytest.raises(AttributeError, match="nonsense"):
        fake.set_state(nonsense=True)


# -- streams -----------------------------------------------------------------


def test_subscribe_starts_a_state_notification_stream(fake: FakeRobotd, client: _Client) -> None:
    answer = client.call("robot.subscribe", {"hz": 50})
    assert answer["result"]["accepted"] is True

    frame = client.read()
    assert frame["method"] == "robot.state"
    assert "id" not in frame
    params = frame["params"]
    assert params["safety"]["fallen"] is False
    assert len(params["joints"]) == len(JOINT_NAMES)


def test_the_state_stream_reflects_set_state(fake: FakeRobotd, client: _Client) -> None:
    client.call("robot.subscribe", {"hz": 100})
    fake.set_state(fallen=True)
    deadline = time.monotonic() + _TIMEOUT
    while time.monotonic() < deadline:
        frame = client.read()
        if frame.get("method") == "robot.state" and frame["params"]["safety"]["fallen"]:
            return
    pytest.fail("the state stream never reported the fallen robot")


def test_pad_and_tof_streams_deliver_fed_frames(fake: FakeRobotd, client: _Client) -> None:
    assert client.call("pad.input")["result"]["accepted"] is True
    assert client.call("tof.stream")["result"]["sensor"] == "VL53L8CX"

    assert fake.feed_pad_report({"buttons": ["a"]}) == 1
    pad = client.read()
    assert pad["method"] == "pad.report"
    assert pad["params"] == {"buttons": ["a"]}

    assert fake.feed_tof_frame({"seq": 1, "rows": 8, "cols": 8}) == 1
    tof = client.read()
    assert tof["method"] == "tof.frame"
    assert tof["params"]["seq"] == 1


def test_frames_are_not_pushed_to_an_unsubscribed_connection(
    fake: FakeRobotd, client: _Client
) -> None:
    client.call("hello")
    assert fake.feed_pad_report({"buttons": []}) == 0


def test_tof_stream_reports_a_missing_sensor(fake: FakeRobotd, client: _Client) -> None:
    fake.set_state(tof_sensor=None)
    result = client.call("tof.stream")["result"]
    assert result["accepted"] is True
    assert result["unavailable"]
    assert "sensor" not in result


# -- fault levers: wedge and delay -------------------------------------------


def test_wedge_stops_the_server_reading_and_unwedge_resumes(
    fake: FakeRobotd, client: _Client
) -> None:
    fake.wedge()
    assert fake.wedged is True
    request_id = client.send("hello")
    with pytest.raises(socket.timeout):
        client.read(timeout=0.3)
    assert fake.call_log == []  # nothing was even read off the socket

    fake.unwedge()
    answer = client.read(timeout=_TIMEOUT)
    assert answer["id"] == request_id
    assert answer["result"]["api_version"] == API_VERSION
    assert fake.methods_called() == ["hello"]


def test_delay_delays_every_reply(fake: FakeRobotd, client: _Client) -> None:
    fake.delay(0)
    started = time.monotonic()
    client.call("robot.health")
    undelayed = time.monotonic() - started

    fake.delay(200)
    started = time.monotonic()
    client.call("robot.health")
    delayed = time.monotonic() - started

    assert delayed >= 0.2
    assert delayed > undelayed

    fake.delay(0)
    started = time.monotonic()
    client.call("robot.health")
    assert time.monotonic() - started < 0.2


# -- lifecycle ---------------------------------------------------------------


def test_close_is_safe_while_a_stream_is_running(fake: FakeRobotd) -> None:
    conn = _Client(fake.socket_path)
    try:
        conn.call("robot.subscribe", {"hz": 200})
        conn.call("pad.input")
        started = time.monotonic()
        fake.close()
        assert time.monotonic() - started < 5.0
    finally:
        conn.close()


def test_autostart_false_defers_binding() -> None:
    fake = FakeRobotd(autostart=False)
    try:
        assert not os.path.exists(fake.socket_path)
        fake.start()
        assert os.path.exists(fake.socket_path)
        fake.start()  # idempotent
    finally:
        fake.close()
