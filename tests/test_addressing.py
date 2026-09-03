"""Tests for microduck_cli.duck.addressing.resolve().

Every test asserts no network and no subprocess call happen: socket.socket
and subprocess.Popen/run are monkeypatched to raise if touched at all.
"""

from __future__ import annotations

import socket
import subprocess

import pytest

from microduck_cli.cli._errors import EXIT_ENV_ERROR, EXIT_USER_ERROR, CliError
from microduck_cli.duck.addressing import DuckAddress, resolve


@pytest.fixture(autouse=True)
def _forbid_io(monkeypatch: pytest.MonkeyPatch) -> None:
    def _boom(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("addressing.resolve() must not touch network/subprocess")

    monkeypatch.setattr(socket, "socket", _boom)
    monkeypatch.setattr(subprocess, "Popen", _boom)
    monkeypatch.setattr(subprocess, "run", _boom)


def _listdir(entries: list[str]):
    def _fn(_path: str) -> list[str]:
        return list(entries)

    return _fn


def _missing_dir(_path: str) -> list[str]:
    raise FileNotFoundError(_path)


# --- acceptance 1: explicit --duck resolves expected paths ----------------


def test_resolve_by_duck_name() -> None:
    addr = resolve(
        name="duck-b",
        env={"DUCK_SIM_STATE": "/state"},
        listdir=_listdir(["duck-a.sock", "duck-b.sock", "duck-b-tof.sock"]),
    )
    assert addr == DuckAddress(
        name="duck-b",
        socket_path="/state/duck-b.sock",
        tof_socket_path="/state/duck-b-tof.sock",
        state_dir="/state",
        source="--duck",
    )


def test_resolve_default_state_dir_from_home() -> None:
    addr = resolve(
        name="duck-a",
        env={"HOME": "/home/u"},
        listdir=_listdir(["duck-a.sock", "duck-a-tof.sock"]),
    )
    assert addr.state_dir == "/home/u/.cache/duck-sim"
    assert addr.socket_path == "/home/u/.cache/duck-sim/duck-a.sock"
    assert addr.tof_socket_path == "/home/u/.cache/duck-sim/duck-a-tof.sock"


def test_resolve_via_env_duck_sim_duck() -> None:
    addr = resolve(
        env={"DUCK_SIM_STATE": "/state", "DUCK_SIM_DUCK": "duck-c"},
        listdir=_listdir(["duck-c.sock", "duck-c-tof.sock"]),
    )
    assert addr.name == "duck-c"
    assert addr.source == "DUCK_SIM_DUCK"


def test_explicit_duck_overrides_env_duck() -> None:
    addr = resolve(
        name="duck-a",
        env={"DUCK_SIM_STATE": "/state", "DUCK_SIM_DUCK": "duck-c"},
        listdir=_listdir(["duck-a.sock", "duck-a-tof.sock", "duck-c.sock", "duck-c-tof.sock"]),
    )
    assert addr.name == "duck-a"
    assert addr.source == "--duck"


def test_resolve_single_sock_in_state_dir() -> None:
    addr = resolve(
        env={"DUCK_SIM_STATE": "/state"},
        listdir=_listdir(["duck-only.sock", "duck-only-tof.sock"]),
    )
    assert addr.name == "duck-only"
    assert addr.source == "state-dir-single"


def test_resolve_multiple_socks_picks_first_alphabetical_and_says_so() -> None:
    addr = resolve(
        env={"DUCK_SIM_STATE": "/state"},
        listdir=_listdir(["duck-z.sock", "duck-z-tof.sock", "duck-a.sock", "duck-a-tof.sock"]),
    )
    assert addr.name == "duck-a"
    assert "alphabetical" in addr.source
    assert "2" in addr.source


def test_explicit_socket_takes_precedence_over_duck_and_env() -> None:
    addr = resolve(
        name="duck-a",
        socket="/custom/path/mysock.sock",
        env={"DUCK_SIM_STATE": "/state", "DUCK_SIM_DUCK": "duck-c"},
        listdir=_listdir(["duck-a.sock", "duck-a-tof.sock"]),
    )
    assert addr.socket_path == "/custom/path/mysock.sock"
    assert addr.source == "--socket"
    assert addr.name == "mysock"


# --- acceptance 2: over-length socket path -> CliError exit 2 -------------


def test_socket_path_over_limit_raises_env_error() -> None:
    long_name = "d" * 90
    listdir = _listdir([f"{long_name}.sock", f"{long_name}-tof.sock"])
    with pytest.raises(CliError) as excinfo:
        resolve(
            name=long_name,
            env={"DUCK_SIM_STATE": "/state"},
            listdir=listdir,
        )
    err = excinfo.value
    assert err.code == EXIT_ENV_ERROR
    assert "108" in err.message
    assert "DUCK_SIM_STATE" in err.remediation


def test_explicit_socket_over_limit_also_checked() -> None:
    long_path = "/state/" + ("x" * 100) + ".sock"
    listdir = _listdir([])
    with pytest.raises(CliError) as excinfo:
        resolve(
            socket=long_path,
            env={"DUCK_SIM_STATE": "/state"},
            listdir=listdir,
        )
    assert excinfo.value.code == EXIT_ENV_ERROR


# --- acceptance 3: unknown name lists sockets present ----------------------


def test_unknown_duck_name_lists_present_sockets() -> None:
    listdir = _listdir(["duck-a.sock", "duck-a-tof.sock"])
    with pytest.raises(CliError) as excinfo:
        resolve(
            name="duck-nope",
            env={"DUCK_SIM_STATE": "/state"},
            listdir=listdir,
        )
    err = excinfo.value
    assert err.code == EXIT_USER_ERROR
    assert "duck-a.sock" in err.message


def test_unknown_duck_name_empty_state_dir() -> None:
    listdir = _listdir([])
    with pytest.raises(CliError) as excinfo:
        resolve(
            name="duck-nope",
            env={"DUCK_SIM_STATE": "/state"},
            listdir=listdir,
        )
    assert "empty or missing" in excinfo.value.message


def test_missing_state_dir_reports_missing() -> None:
    with pytest.raises(CliError) as excinfo:
        resolve(
            name="duck-nope",
            env={"DUCK_SIM_STATE": "/state"},
            listdir=_missing_dir,
        )
    assert "empty or missing" in excinfo.value.message


def test_no_ducks_present_at_all_without_name() -> None:
    listdir = _listdir([])
    with pytest.raises(CliError) as excinfo:
        resolve(env={"DUCK_SIM_STATE": "/state"}, listdir=listdir)
    assert excinfo.value.code == EXIT_USER_ERROR
