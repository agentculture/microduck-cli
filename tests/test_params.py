"""Tests for microduck_cli.env.params (robotd params generation, t13)."""

from __future__ import annotations

import tomllib
from pathlib import Path

import pytest

from microduck_cli.cli._errors import EXIT_ENV_ERROR, CliError
from microduck_cli.env.params import (
    BUS_PORT_PLACEHOLDER,
    POLICY_SLOTS,
    ParamsReport,
    render_params,
    write_params,
)

CLONE = "/opt/src/microduck"
STATE = "/run/user/1000/ds"

ALL_POLICY_FILES = {f"{CLONE}/policies/{filename}" for _slot, filename in POLICY_SLOTS}


def _exists(present: set[str]):
    return lambda path: path in present


def _parse(text: str) -> dict:
    return tomllib.loads(text)


# --- acceptance 3: round-trips, and names only files that exist -------------


def test_full_clone_round_trips_and_names_every_policy() -> None:
    text, report = render_params(
        CLONE, duck="duck-a", state_dir=STATE, exists=_exists(ALL_POLICY_FILES)
    )

    data = _parse(text)  # round-trip through tomllib

    assert data["policy"]["enabled"] is True
    for slot, filename in POLICY_SLOTS:
        assert data["policy"][slot] == f"{CLONE}/policies/{filename}"
    assert report.missing == []
    assert report.policy_enabled is True
    assert report.policies == {
        slot: f"{CLONE}/policies/{filename}" for slot, filename in POLICY_SLOTS
    }


def test_partial_clone_names_only_the_files_that_exist() -> None:
    present = {f"{CLONE}/policies/alpha_walking.onnx", f"{CLONE}/policies/roulade.onnx"}
    text, report = render_params(CLONE, duck="duck-a", state_dir=STATE, exists=_exists(present))

    data = _parse(text)
    assert set(data["policy"]) == {"enabled", "walk", "roulade"}
    assert data["policy"]["walk"] == f"{CLONE}/policies/alpha_walking.onnx"
    assert data["policy"]["roulade"] == f"{CLONE}/policies/roulade.onnx"
    assert report.missing == ["stand", "sitstand", "ground_pick", "kick_left", "kick_right"]
    # Every named path exists; nothing is named that does not.
    for value in report.policies.values():
        assert value in present


def test_no_policies_disables_the_policy_and_says_so() -> None:
    text, report = render_params(CLONE, duck="duck-a", state_dir=STATE, exists=_exists(set()))

    data = _parse(text)
    assert data["policy"] == {"enabled": False}
    assert report.policy_enabled is False
    assert any("no-policy" in note for note in report.notes)
    assert "--no-policy" in text or "no-policy" in text


def test_bus_port_is_a_placeholder_not_a_servo_bus() -> None:
    text, _report = render_params(CLONE, duck="duck-a", state_dir=STATE, exists=_exists(set()))
    data = _parse(text)
    assert data["bus"]["port"] == BUS_PORT_PLACEHOLDER
    assert not data["bus"]["port"].startswith("/dev/tty")


def test_every_other_section_is_commented_out() -> None:
    text, _report = render_params(
        CLONE, duck="duck-b", state_dir=STATE, exists=_exists(ALL_POLICY_FILES)
    )
    data = _parse(text)
    # A params file is a list of decisions: only bus and policy are decided.
    assert set(data) == {"bus", "policy"}
    # …but the alternatives are visible, with this duck's own values.
    assert f'# socket = "{STATE}/duck-b-tof.sock"' in text
    assert "# [audio]" in text
    assert "# [chorale]" in text


def test_paths_are_absolute_even_from_a_relative_clone(tmp_path: Path) -> None:
    _text, report = render_params(
        "relative/clone", duck="duck-a", state_dir="relative/state", exists=_exists(set())
    )
    assert Path(report.policy_dir).is_absolute()
    assert Path(report.params_path).is_absolute()


# --- writing ---------------------------------------------------------------


def test_write_params_writes_one_file_named_after_the_duck(tmp_path: Path) -> None:
    clone = tmp_path / "microduck"
    (clone / "policies").mkdir(parents=True)
    (clone / "policies" / "alpha_walking.onnx").write_bytes(b"")
    state = tmp_path / "state"

    path, report = write_params(clone, duck="duck-a", state_dir=state)

    assert Path(path) == state / "duck-a.toml"
    # Never robotd.toml: that name is the daemon's own live config.
    assert sorted(p.name for p in state.iterdir()) == ["duck-a.toml"]
    data = tomllib.loads(Path(path).read_text(encoding="utf-8"))
    assert data["policy"]["walk"] == str(clone / "policies" / "alpha_walking.onnx")
    assert report.duck == "duck-a"


def test_write_params_reports_an_unwritable_state_dir(tmp_path: Path) -> None:
    blocker = tmp_path / "state"
    blocker.write_text("not a directory", encoding="utf-8")

    with pytest.raises(CliError) as excinfo:
        write_params(tmp_path / "microduck", duck="duck-a", state_dir=blocker)

    assert excinfo.value.code == EXIT_ENV_ERROR
    assert "duck-sim" in excinfo.value.remediation


def test_report_to_dict_is_json_shaped() -> None:
    _text, report = render_params(CLONE, duck="duck-a", state_dir=STATE, exists=_exists(set()))
    payload = report.to_dict()
    assert isinstance(report, ParamsReport)
    assert payload["duck"] == "duck-a"
    assert payload["policy_enabled"] is False
    assert isinstance(payload["notes"], list)
