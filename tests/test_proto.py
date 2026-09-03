"""Table test for microduck_cli.ipc.proto against tests/fixtures/duck_ipc_proto.json.

The fixture is the transcription target: both it and proto.py were copied from the pinned
duck-ipc-proto commit recorded in docs/upstream-pins.md (see proto.py's module docstring for
the exact ref/blob sha and the deviations from the t1 task brief). This test fails if the two
ever disagree.
"""

from __future__ import annotations

import ast
import json
import sys
from pathlib import Path

import pytest

from microduck_cli.ipc import proto

FIXTURE_PATH = Path(__file__).parent / "fixtures" / "duck_ipc_proto.json"


@pytest.fixture(scope="module")
def fixture() -> dict:
    with FIXTURE_PATH.open(encoding="utf-8") as handle:
        return json.load(handle)


# ── scalar / sequence constants ──────────────────────────────────────────────────────────


def test_jsonrpc_version(fixture: dict) -> None:
    assert proto.JSONRPC_VERSION == fixture["jsonrpc_version"]


def test_api_version(fixture: dict) -> None:
    assert proto.API_VERSION == fixture["api_version"]


def test_policy_obs_len(fixture: dict) -> None:
    assert proto.POLICY_OBS_LEN == fixture["policy_obs_len"] == 61


def test_policy_action_len(fixture: dict) -> None:
    assert proto.POLICY_ACTION_LEN == fixture["policy_action_len"] == 14


def test_joint_names(fixture: dict) -> None:
    assert list(proto.JOINT_NAMES) == fixture["joint_names"]
    assert len(proto.JOINT_NAMES) == 15


def test_joint_names_exact_order() -> None:
    assert proto.JOINT_NAMES == (
        "left_hip_yaw",
        "left_hip_roll",
        "left_hip_pitch",
        "left_knee",
        "left_ankle",
        "neck_pitch",
        "head_pitch",
        "head_yaw",
        "head_roll",
        "mouth",
        "right_hip_yaw",
        "right_hip_roll",
        "right_hip_pitch",
        "right_knee",
        "right_ankle",
    )


# ── default socket paths ─────────────────────────────────────────────────────────────────


def test_sockets(fixture: dict) -> None:
    sockets = fixture["sockets"]
    assert proto.DEFAULT_SOCKET == sockets["updater"]
    assert proto.SOCKET_UPDATER == sockets["updater"]
    assert proto.SOCKET_ROBOT == sockets["robot"]
    assert proto.SOCKET_CONFIG == sockets["config"]
    assert proto.SOCKET_PAD == sockets["pad"]
    assert proto.SOCKET_TOF == sockets["tof"]


# ── method-name constants ────────────────────────────────────────────────────────────────


def test_method_table_matches_fixture(fixture: dict) -> None:
    assert dict(proto.METHODS) == fixture["methods"]


@pytest.mark.parametrize("name", list(json.loads(FIXTURE_PATH.read_text())["methods"]))
def test_each_method_constant_matches_fixture(name: str, fixture: dict) -> None:
    assert getattr(proto, name) == fixture["methods"][name]


def test_hello_constant() -> None:
    assert proto.HELLO == "hello"


# ── error codes ───────────────────────────────────────────────────────────────────────────


def test_error_code_table_matches_fixture(fixture: dict) -> None:
    assert dict(proto.ERROR_CODES) == fixture["error_codes"]


@pytest.mark.parametrize("name", list(json.loads(FIXTURE_PATH.read_text())["error_codes"]))
def test_each_error_code_matches_fixture(name: str, fixture: dict) -> None:
    assert getattr(proto, name) == fixture["error_codes"][name]


def test_jsonrpc_error_codes_are_spec_reserved() -> None:
    assert proto.PARSE_ERROR == -32700
    assert proto.INVALID_REQUEST == -32600
    assert proto.METHOD_NOT_FOUND == -32601
    assert proto.INVALID_PARAMS == -32602
    assert proto.INTERNAL_ERROR == -32603


# ── is_notification classification ───────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "method",
    [
        proto.ROBOT_MOVE,
        proto.ROBOT_HEAD,
        proto.ROBOT_POSE,
        proto.ROBOT_MOUTH,
        proto.ROBOT_SOUND,
    ],
)
def test_is_notification_true_for_continuous_intents(method: str) -> None:
    assert proto.is_notification(method) is True


@pytest.mark.parametrize(
    "method",
    [
        proto.ROBOT_DO,
        proto.ROBOT_LOOK,
        proto.ROBOT_STOP,
        proto.ROBOT_ENABLE,
        proto.ROBOT_INIT,
        proto.ROBOT_RELAX,
        proto.ROBOT_SET_MODE,
        "robot.loadPolicy",  # not a real method at this pin -- see proto.py docstring
    ],
)
def test_is_notification_false_for_discrete_requests(method: str) -> None:
    assert proto.is_notification(method) is False


@pytest.mark.parametrize(
    "method",
    [
        proto.HELLO,
        proto.UPDATE_APPLY,
        proto.NET_STATUS,
        proto.SYSTEM_INFO,
        proto.PAD_INPUT,
        proto.TOF_STREAM,
        proto.CHORALE_SUBSCRIBE,
    ],
)
def test_is_notification_false_for_every_non_robot_method(method: str) -> None:
    assert proto.is_notification(method) is False


def test_notification_methods_matches_fixture(fixture: dict) -> None:
    assert sorted(proto.NOTIFICATION_METHODS) == sorted(fixture["notification_methods"])


# ── stdlib-only import contract ──────────────────────────────────────────────────────────


def test_module_imports_nothing_outside_the_stdlib() -> None:
    source = Path(proto.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source, filename=proto.__file__)

    top_level_names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                top_level_names.add(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            if node.level == 0 and node.module is not None:
                top_level_names.add(node.module.split(".")[0])
            # a relative import (node.level > 0) stays inside microduck_cli -- allowed

    assert top_level_names, "expected at least one import (e.g. types) to make this test meaningful"

    non_stdlib = {name for name in top_level_names if name not in sys.stdlib_module_names}
    assert not non_stdlib, f"microduck_cli.ipc.proto imports non-stdlib modules: {non_stdlib}"
