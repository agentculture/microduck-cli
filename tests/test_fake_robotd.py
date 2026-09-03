"""Tests for the in-process fake robotd (``tests/fake_robotd.py``).

These cover the fake's own contract — the wire shapes it was built to match, the
call log, and the three fault levers (refuse / wedge / delay) — so a later client
test that fails can be read as a client bug rather than a fake bug.

The payloads asserted here are the ones captured from the real ``robotd`` 0.10.0
built from the pinned ``sim-remote-io`` commit and run with ``--fake``. When the
pin moves, re-probe and update these first.
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
    DAEMON_VERSION,
    INVALID_PARAMS,
    JOINT_NAMES,
    METHOD_NOT_FOUND,
    POLICY_API_VERSION,
    POLICY_METHODS,
    SKILLS,
    FakeRobotd,
)

_TIMEOUT = 2.0
_HELLO = {"api_version": API_VERSION}


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

    def result(self, method: str, params: Any = None) -> Any:
        answer = self.call(method, params)
        assert "error" not in answer, answer["error"]
        return answer["result"]

    def error(self, method: str, params: Any = None) -> dict[str, Any]:
        answer = self.call(method, params)
        assert "result" not in answer, answer["result"]
        return answer["error"]

    def close(self) -> None:
        self.sock.close()


@pytest.fixture
def fake():
    with FakeRobotd() as instance:
        yield instance


@pytest.fixture
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


# -- handshake ---------------------------------------------------------------


def test_hello_returns_the_probed_daemon_shape(client: _Client) -> None:
    assert client.result("hello", _HELLO) == {
        "api_version": API_VERSION,
        "daemon_version": DAEMON_VERSION,
        "revision": None,
    }


def test_hello_without_api_version_is_a_missing_field_error(client: _Client) -> None:
    error = client.error("hello")
    assert error["code"] == INVALID_PARAMS
    assert error["message"] == "missing field `api_version`"


def test_any_client_api_version_is_accepted(client: _Client) -> None:
    """Skew is reported by the answer, never refused at the door."""
    for sent in (1, 99):
        assert client.result("hello", {"api_version": sent})["api_version"] == API_VERSION


# -- unknown methods and bad params ------------------------------------------


def test_unknown_method_is_method_not_found_quoting_the_method(client: _Client) -> None:
    error = client.error("robot.nope")
    assert error["code"] == METHOD_NOT_FOUND
    assert error["message"] == 'unknown method "robot.nope"'


@pytest.mark.parametrize(
    ("method", "params", "message"),
    [
        ("robot.enable", {"enabled": True}, "unknown field `enabled`, expected `on` or `toggle`"),
        ("robot.do", {"name": "roulade"}, "unknown field `name`, expected `skill`"),
        (
            "robot.look",
            {"point": [0.5, 0, 0.1]},
            "unknown field `point`, expected one of `x`, `y`, `z`, `neck_pitch`",
        ),
    ],
)
def test_serde_style_invalid_params(
    client: _Client, method: str, params: dict[str, Any], message: str
) -> None:
    error = client.error(method, params)
    assert error["code"] == INVALID_PARAMS
    assert error["message"] == message


def test_a_float_where_the_proto_wants_a_u32_is_a_type_error(client: _Client) -> None:
    """``robot.subscribe``'s ``hz`` is a serde ``u32``: 50.0 is a type error, not 50.

    Straight off the real daemon, and the reason it is asserted here: a client that
    sends a JSON float is refused on the box, while a lenient fake would let it pass.
    """
    error = client.error("robot.subscribe", {"hz": 50.0})
    assert error["code"] == INVALID_PARAMS
    assert error["message"] == "invalid type: floating point `50.0`, expected u32"
    assert client.result("robot.subscribe", {"hz": 50})["accepted"] is True


def test_an_unknown_skill_is_an_unknown_variant(client: _Client) -> None:
    error = client.error("robot.do", {"skill": "backflip"})
    assert error["code"] == INVALID_PARAMS
    assert error["message"].startswith("unknown variant `backflip`, expected one of `ground_pick`")


def test_a_malformed_line_is_a_parse_error(client: _Client) -> None:
    client.sock.sendall(b"{not json\n")
    assert client.read()["error"]["code"] == -32700


def test_an_unknown_notification_is_recorded_but_never_answered(
    fake: FakeRobotd, client: _Client
) -> None:
    client.send("robot.nope", {"a": 1}, notify=True)
    # Bad params on a notification are silent too — the probed daemon sent
    # nothing back for any of them.
    client.send("robot.move", {"bogus": 1}, notify=True)
    assert "result" in client.call("robot.health")
    assert fake.methods_called() == ["robot.nope", "robot.move", "robot.health"]


# -- the answered verbs, as the probed daemon answers them -------------------


def test_the_bare_fake_daemon_answers(client: _Client) -> None:
    assert client.result("robot.modelApi") == {"model_api": 1}
    assert client.result("robot.mode") == {"mode": "walk"}
    assert client.result("robot.safeToRestart") == {"safe": True}
    assert client.result("robot.remoteSessionActive") == {"active": False}
    assert client.result("robot.init") == {"accepted": True}
    assert client.result("robot.relax") == {"accepted": True}
    assert client.result("robot.stop") == {"accepted": True}


def test_enable_reports_the_state_it_ended_in(client: _Client) -> None:
    assert client.result("robot.enable", {"on": True}) == {
        "accepted": True,
        "reason": "enabled — driving",
    }
    assert client.result("robot.enable", {"on": False})["accepted"] is True
    # `toggle` flips whatever the robot currently believes.
    assert client.result("robot.enable", {"on": False, "toggle": True})["reason"].startswith(
        "enabled"
    )


def test_a_bare_fake_has_no_policy_behind_any_skill(client: _Client) -> None:
    assert client.result("robot.do", {"skill": "roulade"}) == {
        "accepted": False,
        "reason": "no policy configured for that skill",
    }
    assert client.result("robot.setMode", {"mode": "walk"}) == {
        "accepted": False,
        "reason": "no policy on this robot, so there is nothing to switch between",
    }


def test_health_on_a_bare_fake_has_no_battery_or_temperature(client: _Client) -> None:
    health = client.result("robot.health")
    assert health["healthy"] is True
    assert health["control_loop"] == {
        "target_hz": 50.0,
        "achieved_hz": None,
        "ticks": 1000,
        "missed": 0,
        "last_tick_age_ms": 4,
    }
    assert health["bus"] == {"consecutive_errors": 0, "startup_failures": 0}
    assert health["imu"] == {"ready": False, "stale_blocks": 0, "consecutive_stale_blocks": 0}
    assert "battery" not in health
    assert "motors" not in health
    assert "degraded" not in health


# -- the call log (obligation o2) --------------------------------------------


def test_call_log_preserves_order_and_separates_notifications_from_requests(
    fake: FakeRobotd, client: _Client
) -> None:
    client.call("hello", _HELLO)
    client.send("robot.move", {"vx": 0.1, "vy": 0.0, "vyaw": 0.0}, notify=True)
    client.send("robot.mouth", {"open": 0.5}, notify=True)
    client.call("robot.enable", {"on": True})
    client.send("robot.head", {"head_yaw": 0.2}, notify=True)
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
    assert log[0].params == _HELLO


def test_call_log_sequence_is_monotonic_across_connections(fake: FakeRobotd) -> None:
    first = _Client(fake.socket_path)
    second = _Client(fake.socket_path)
    try:
        first.call("hello", _HELLO)
        second.call("robot.health")
        first.call("robot.mode")
    finally:
        first.close()
        second.close()
    seqs = [rec.seq for rec in fake.call_log]
    assert seqs == sorted(seqs)
    assert len(set(seqs)) == len(seqs)


def test_clear_log_empties_it(fake: FakeRobotd, client: _Client) -> None:
    client.call("hello", _HELLO)
    assert fake.call_log
    fake.clear_log()
    assert fake.call_log == []


# -- refusals ----------------------------------------------------------------


def test_refuse_makes_a_method_answer_an_error_until_cleared(
    fake: FakeRobotd, client: _Client
) -> None:
    fake.refuse("robot.init", code=7, message="servo bus is down")
    error = client.error("robot.init")
    assert error == {"code": 7, "message": "servo bus is down"}

    fake.allow("robot.init")
    assert client.result("robot.init") == {"accepted": True}


def test_allow_with_no_argument_clears_every_refusal(fake: FakeRobotd, client: _Client) -> None:
    fake.refuse("robot.init")
    fake.refuse("robot.stop")
    fake.allow()
    assert client.result("robot.init")["accepted"] is True
    assert client.result("robot.stop")["accepted"] is True


def test_a_refusal_outranks_param_validation(fake: FakeRobotd, client: _Client) -> None:
    fake.refuse("robot.do", code=1, message="busy")
    assert client.error("robot.do", {"skill": "roulade"})["message"] == "busy"


# -- state shaping -----------------------------------------------------------


def test_set_state_adds_the_measured_sections_to_health(fake: FakeRobotd, client: _Client) -> None:
    fake.set_state(
        healthy=False,
        degraded=True,
        reason="control loop stalled",
        battery_frac=0.1,
        hottest_servo_c=61.5,
        achieved_hz=48.2,
    )
    health = client.result("robot.health")
    assert health["healthy"] is False
    assert health["degraded"] is True
    assert health["reason"] == "control loop stalled"
    assert health["battery"]["percent"] == pytest.approx(10.0)
    assert health["motors"] == {"hottest_c": 61.5}
    assert health["control_loop"]["achieved_hz"] == pytest.approx(48.2)


def test_a_fallen_robot_refuses_to_drive(fake: FakeRobotd, client: _Client) -> None:
    fake.set_state(fallen=True, skills=["roulade"], enabled=True)
    assert client.result("robot.enable", {"on": True}) == {
        "accepted": False,
        "reason": "the robot has fallen",
    }
    assert client.result("robot.do", {"skill": "roulade"})["reason"] == "the robot has fallen"


def test_configured_skills_are_accepted_once_the_robot_is_driving(
    fake: FakeRobotd, client: _Client
) -> None:
    fake.set_state(skills=["kick_left"])
    assert client.result("robot.do", {"skill": "kick_left"})["reason"] == "the robot is not driving"
    client.result("robot.enable", {"on": True})
    assert client.result("robot.do", {"skill": "kick_left"}) == {"accepted": True}
    # A skill in the enum but without a policy is still refused.
    assert (
        client.result("robot.do", {"skill": "roulade"})["reason"]
        == "no policy configured for that skill"
    )


def test_a_configured_policy_makes_set_mode_switchable(fake: FakeRobotd, client: _Client) -> None:
    fake.set_state(walk_policy="walk.onnx", unavailable=None)
    assert client.result("robot.setMode", {"mode": "roller"}) == {"accepted": True}
    assert client.result("robot.mode") == {"mode": "roller"}
    assert client.result("robot.setMode", {"mode": "hover"})["accepted"] is False


def test_set_state_rejects_an_unknown_field(fake: FakeRobotd) -> None:
    with pytest.raises(AttributeError, match="nonsense"):
        fake.set_state(nonsense=True)


# -- the policy methods are gated on the API version -------------------------


@pytest.mark.parametrize("method", sorted(POLICY_METHODS))
def test_policy_methods_are_absent_on_the_pinned_api_version(client: _Client, method: str) -> None:
    error = client.error(method, {"slot": "walk", "path": "x"})
    assert error["code"] == METHOD_NOT_FOUND
    assert error["message"] == f'unknown method "{method}"'


def test_policy_methods_appear_on_a_newer_daemon(fake: FakeRobotd, client: _Client) -> None:
    fake.set_state(api_version=POLICY_API_VERSION, skills=["kick_left"])
    assert client.result("hello", _HELLO)["api_version"] == POLICY_API_VERSION

    policies = client.result("robot.policies")
    assert policies["slots"]["walk"] is None
    assert [entry["name"] for entry in policies["skills"]] == ["kick_left"]

    # robot.skills reads the live [[policy.skill]] table (robot.setSkill /
    # robot.removeSkill), not the legacy `skills` field set_state seeds above —
    # empty until something is added.
    assert client.result("robot.skills") == {"skills": [], "built_in": list(SKILLS)}

    assert client.result("robot.loadPolicy", {"slot": "walk", "path": "w.onnx"}) == {
        "accepted": True
    }
    assert client.result("robot.policies")["slots"]["walk"] == "w.onnx"
    assert client.result("robot.loadPolicy", {"slot": "hover", "path": "x"})["accepted"] is False

    # robot.reloadPolicies re-reads every slot from disk; the fake models this as
    # a no-op accept.
    assert client.result("robot.reloadPolicies") == {"accepted": True}

    # robot.setSkill / robot.removeSkill write and read back the same shape
    # ("the same shape read and written" — SkillParams's doc comment).
    assert client.result("robot.setSkill", {"name": "polite-bow", "duration": 5.0}) == {
        "accepted": True
    }
    skills = client.result("robot.skills")["skills"]
    assert skills == [
        {"name": "polite-bow", "duration": 5.0, "overridden": True},
    ]

    assert client.result("robot.removeSkill", {"name": "polite-bow"}) == {
        "accepted": True,
        "removed": True,
    }
    assert client.result("robot.skills")["skills"] == []
    assert client.result("robot.removeSkill", {"name": "polite-bow"}) == {
        "accepted": True,
        "removed": False,
    }


# -- streams -----------------------------------------------------------------


def test_subscribe_answers_and_starts_the_state_stream(client: _Client) -> None:
    assert client.result("robot.subscribe", {"hz": 50}) == {
        "accepted": True,
        "unavailable": "no policy configured; holding the startup pose",
    }

    frame = client.read()
    assert frame["method"] == "robot.state"
    assert "id" not in frame
    params = frame["params"]
    assert sorted(params) == [
        "head",
        "joints",
        "loop",
        "move",
        "odom",
        "policy",
        "safety",
        "t",
        "targets",
    ]
    assert len(params["head"]) == 4
    assert len(params["joints"]) == len(JOINT_NAMES) == 15
    assert len(params["targets"]) == 15
    assert sorted(params["safety"]) == ["fallen", "gain", "gravity", "limp"]
    assert params["safety"]["gravity"] == [0.0, 0.0, -1.0]
    assert params["safety"]["gain"] == 200
    assert params["odom"] == {"position": [0.0, 0.0, 0.0], "yaw": 0.0}
    assert params["policy"] == "held"


def test_subscribe_names_the_configured_policies(fake: FakeRobotd, client: _Client) -> None:
    fake.set_state(walk_policy="walk.onnx", stand_policy="stand.onnx", unavailable=None)
    assert client.result("robot.subscribe") == {
        "accepted": True,
        "walk": "walk.onnx",
        "stand": "stand.onnx",
    }


def test_the_state_stream_reflects_set_state(fake: FakeRobotd, client: _Client) -> None:
    client.call("robot.subscribe", {"hz": 100})
    fake.set_state(fallen=True)
    deadline = time.monotonic() + _TIMEOUT
    while time.monotonic() < deadline:
        frame = client.read()
        if frame.get("method") == "robot.state" and frame["params"]["safety"]["fallen"]:
            assert frame["params"]["safety"]["gravity"] == [0.0, 0.0, 1.0]
            return
    pytest.fail("the state stream never reported the fallen robot")


def test_joint_names_override_simulates_a_table_mismatch(fake: FakeRobotd, client: _Client) -> None:
    """A daemon whose joint table disagrees with the client's, on the wire."""
    fake.set_state(joint_names=("only_one",))
    client.call("robot.subscribe", {"hz": 100})
    frame = client.read()
    assert frame["method"] == "robot.state"
    assert len(frame["params"]["joints"]) == 1 != len(JOINT_NAMES)


def test_pad_and_tof_streams_deliver_fed_frames(fake: FakeRobotd, client: _Client) -> None:
    assert client.result("pad.input")["accepted"] is True
    assert client.result("tof.stream")["sensor"] == "VL53L8CX"

    assert fake.feed_pad_report({"buttons": ["a"]}) == 1
    pad = client.read()
    assert pad["method"] == "pad.report"
    assert "id" not in pad
    assert pad["params"] == {"buttons": ["a"]}

    assert fake.feed_tof_frame({"seq": 1, "rows": 8, "cols": 8}) == 1
    tof = client.read()
    assert tof["method"] == "tof.frame"
    assert tof["params"]["seq"] == 1


def test_frames_are_not_pushed_to_an_unsubscribed_connection(
    fake: FakeRobotd, client: _Client
) -> None:
    client.call("hello", _HELLO)
    assert fake.feed_pad_report({"buttons": []}) == 0


def test_tof_stream_reports_a_missing_sensor(fake: FakeRobotd, client: _Client) -> None:
    fake.set_state(tof_sensor=None)
    result = client.result("tof.stream")
    assert result["accepted"] is True
    assert result["unavailable"]
    assert "sensor" not in result


# -- fault levers: wedge and delay -------------------------------------------


def test_wedge_stops_the_server_reading_and_unwedge_resumes(
    fake: FakeRobotd, client: _Client
) -> None:
    fake.wedge()
    assert fake.wedged is True
    request_id = client.send("hello", _HELLO)
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


def test_delay_applies_to_error_replies_too(fake: FakeRobotd, client: _Client) -> None:
    fake.delay(150)
    started = time.monotonic()
    client.error("robot.nope")
    assert time.monotonic() - started >= 0.15


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
