"""Tests for microduck_cli.behavior.skills — skills snapshots + rule-action validation."""

from __future__ import annotations

import json
import socket

import pytest

from microduck_cli.behavior.rules import SCHEMA_VERSION, RulesConfig
from microduck_cli.behavior.skills import (
    SOURCE_POLICIES,
    SOURCE_SNAPSHOT,
    SOURCE_SUBSCRIBE,
    SkillsSnapshot,
    load_snapshot,
    save_snapshot,
    skills_from_policies_result,
    skills_from_subscribe_result,
    validate_rule_actions,
)
from microduck_cli.cli._errors import CliError
from tests.fake_robotd import API_VERSION, POLICY_API_VERSION, FakeRobotd


def _connect(fake: FakeRobotd) -> socket.socket:
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    sock.connect(fake.socket_path)
    return sock


def _call(sock: socket.socket, buf: "list[bytes]", method: str, params: dict, call_id: int) -> dict:
    request = {"jsonrpc": "2.0", "id": call_id, "method": method, "params": params}
    sock.sendall((json.dumps(request) + "\n").encode("utf-8"))
    while b"\n" not in b"".join(buf):
        chunk = sock.recv(65536)
        assert chunk, "fake robotd closed the connection"
        buf.append(chunk)
    data = b"".join(buf)
    line, _, rest = data.partition(b"\n")
    buf.clear()
    if rest:
        buf.append(rest)
    return json.loads(line.decode("utf-8"))


def _config_with_do(skill: str) -> RulesConfig:
    return RulesConfig.from_dict(
        {
            "schema_version": SCHEMA_VERSION,
            "react": [
                {
                    "id": "kick-on-ball",
                    "when": {"field": "tof_nearest_m", "op": "lt", "value": 0.1},
                    "run": "do",
                    "params": {"skill": skill},
                }
            ],
        }
    )


# --------------------------------------------------------------------------- #
# Parsing real fake_robotd result dicts                                      #
# --------------------------------------------------------------------------- #


def test_skills_from_subscribe_result_against_the_fake_api16():
    """Drive FakeRobotd with a raw socket at the pinned API version (16)."""
    with FakeRobotd() as fake:
        fake.set_state(skills=("kick_left", "kick_right"), api_version=API_VERSION)
        sock = _connect(fake)
        try:
            buf: list[bytes] = []
            hello = _call(sock, buf, "hello", {"api_version": API_VERSION}, 1)
            assert hello["result"]["api_version"] == API_VERSION
            reply = _call(sock, buf, "robot.subscribe", {}, 2)
        finally:
            sock.close()
    result = reply["result"]
    # Show the fake's actual API-16 SubscribeResult shape.
    assert result == {
        "accepted": True,
        "unavailable": "no policy configured; holding the startup pose",
        "kick_left": "kick_left.onnx",
        "kick_right": "kick_right.onnx",
    }
    snapshot = skills_from_subscribe_result(result, api_version=API_VERSION)
    assert snapshot.skills == ("kick_left", "kick_right")
    assert snapshot.source == SOURCE_SUBSCRIBE
    assert snapshot.api_version == API_VERSION


def test_skills_from_policies_result_against_the_fake_api18():
    """Drive FakeRobotd with a raw socket at API 18, where robot.policies exists."""
    with FakeRobotd() as fake:
        fake.set_state(skills=("kick_left", "kick_right"), api_version=POLICY_API_VERSION)
        sock = _connect(fake)
        try:
            buf: list[bytes] = []
            hello = _call(sock, buf, "hello", {"api_version": POLICY_API_VERSION}, 1)
            assert hello["result"]["api_version"] == POLICY_API_VERSION
            reply = _call(sock, buf, "robot.policies", {}, 2)
        finally:
            sock.close()
    result = reply["result"]
    # Show the fake's actual API-18 robot.policies shape.
    assert result == {
        "slots": {
            "walk": None,
            "stand": None,
            "unavailable": "no policy configured; holding the startup pose",
        },
        "skills": [
            {"name": "kick_left", "file": "kick_left.onnx"},
            {"name": "kick_right", "file": "kick_right.onnx"},
        ],
    }
    snapshot = skills_from_policies_result(result, api_version=POLICY_API_VERSION)
    assert snapshot.skills == ("kick_left", "kick_right")
    assert snapshot.source == SOURCE_POLICIES
    assert snapshot.api_version == POLICY_API_VERSION


def test_subscribe_and_policies_parsers_agree_on_the_same_configured_skills():
    """Same configured skills, two API shapes -> the same skills tuple."""
    with FakeRobotd() as fake_16:
        fake_16.set_state(skills=("ground_pick", "roulade"), api_version=API_VERSION)
        sock = _connect(fake_16)
        try:
            buf: list[bytes] = []
            _call(sock, buf, "hello", {"api_version": API_VERSION}, 1)
            sub_reply = _call(sock, buf, "robot.subscribe", {}, 2)
        finally:
            sock.close()

    with FakeRobotd() as fake_18:
        fake_18.set_state(skills=("ground_pick", "roulade"), api_version=POLICY_API_VERSION)
        sock = _connect(fake_18)
        try:
            buf = []
            _call(sock, buf, "hello", {"api_version": POLICY_API_VERSION}, 1)
            pol_reply = _call(sock, buf, "robot.policies", {}, 2)
        finally:
            sock.close()

    from_subscribe = skills_from_subscribe_result(sub_reply["result"], api_version=API_VERSION)
    from_policies = skills_from_policies_result(pol_reply["result"], api_version=POLICY_API_VERSION)
    assert from_subscribe.skills == from_policies.skills == ("ground_pick", "roulade")


# --------------------------------------------------------------------------- #
# Parsers on captured dicts (unit level, no socket)                          #
# --------------------------------------------------------------------------- #


def test_skills_from_subscribe_result_no_skills():
    result = {"accepted": True, "unavailable": "no policy configured; holding the startup pose"}
    snapshot = skills_from_subscribe_result(result, api_version=API_VERSION)
    assert snapshot.skills == ()
    assert snapshot.slots == {"unavailable": "no policy configured; holding the startup pose"}


def test_skills_from_subscribe_result_rejects_non_dict():
    with pytest.raises(CliError):
        skills_from_subscribe_result([], api_version=API_VERSION)  # type: ignore[arg-type]


def test_skills_from_policies_result_rejects_bad_skills_shape():
    with pytest.raises(CliError):
        skills_from_policies_result({"skills": "not-a-list"}, api_version=POLICY_API_VERSION)


def test_skills_from_policies_result_rejects_bad_entry():
    with pytest.raises(CliError):
        skills_from_policies_result(
            {"skills": [{"file": "x.onnx"}]}, api_version=POLICY_API_VERSION
        )


# --------------------------------------------------------------------------- #
# save_snapshot / load_snapshot                                              #
# --------------------------------------------------------------------------- #


def test_save_and_load_snapshot_roundtrip(tmp_path):
    snapshot = SkillsSnapshot(
        skills=("b", "a"),
        slots={"walk": "policy.onnx"},
        source=SOURCE_SUBSCRIBE,
        api_version=API_VERSION,
        captured_at="2026-09-03T00:00:00Z",
    )
    path = tmp_path / "skills.json"
    save_snapshot(path, snapshot)
    loaded = load_snapshot(path)
    assert loaded.skills == ("a", "b")  # sorted on load
    assert loaded.slots == {"walk": "policy.onnx"}
    assert loaded.source == SOURCE_SNAPSHOT  # always SOURCE_SNAPSHOT once loaded from a file
    assert loaded.api_version == API_VERSION
    assert loaded.captured_at == "2026-09-03T00:00:00Z"


def test_load_snapshot_missing_file(tmp_path):
    with pytest.raises(CliError):
        load_snapshot(tmp_path / "missing.json")


def test_load_snapshot_bad_json(tmp_path):
    path = tmp_path / "skills.json"
    path.write_text("not json", encoding="utf-8")
    with pytest.raises(CliError):
        load_snapshot(path)


def test_load_snapshot_bad_shape(tmp_path):
    path = tmp_path / "skills.json"
    path.write_text(json.dumps({"skills": "not-a-list"}), encoding="utf-8")
    with pytest.raises(CliError):
        load_snapshot(path)


# --------------------------------------------------------------------------- #
# validate_rule_actions — the pinned refusal message                         #
# --------------------------------------------------------------------------- #


def test_validate_rule_actions_refuses_unknown_skill_with_pinned_message():
    config = _config_with_do("c")
    snapshot = SkillsSnapshot(skills=("a", "b"))
    problems = validate_rule_actions(config, snapshot)
    assert problems == ["rule 'kick-on-ball': c not in [a, b]"]


def test_validate_rule_actions_accepts_known_skill():
    config = _config_with_do("a")
    snapshot = SkillsSnapshot(skills=("a", "b"))
    assert validate_rule_actions(config, snapshot) == []


def test_validate_rule_actions_checks_mode():
    config = RulesConfig.from_dict(
        {
            "schema_version": SCHEMA_VERSION,
            "react": [
                {
                    "id": "bad-mode",
                    "when": {"field": "fallen", "op": "is_false"},
                    "run": "mode",
                    "params": {"mode": "not-a-mode"},
                }
            ],
        }
    )
    snapshot = SkillsSnapshot(skills=())
    problems = validate_rule_actions(config, snapshot)
    assert any("bad-mode" in p and "not-a-mode" in p for p in problems)


def test_validate_rule_actions_checks_sound():
    config = RulesConfig.from_dict(
        {
            "schema_version": SCHEMA_VERSION,
            "react": [
                {
                    "id": "bad-sound",
                    "when": {"field": "fallen", "op": "is_false"},
                    "run": "sound",
                    "params": {"name": "not-a-sound"},
                }
            ],
        }
    )
    snapshot = SkillsSnapshot(skills=())
    problems = validate_rule_actions(config, snapshot)
    assert any("bad-sound" in p and "not-a-sound" in p for p in problems)


def test_validate_rule_actions_ignores_non_do_actions_by_default():
    config = RulesConfig.from_dict(
        {
            "schema_version": SCHEMA_VERSION,
            "react": [
                {
                    "id": "stop-rule",
                    "when": {"field": "fallen", "op": "is_true"},
                    "run": "stop",
                }
            ],
        }
    )
    snapshot = SkillsSnapshot(skills=())
    assert validate_rule_actions(config, snapshot) == []
