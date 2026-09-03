"""Tests for microduck_cli.env.doctor (env doctor report, t15)."""

from __future__ import annotations

import json

from microduck_cli.env.doctor import (
    EnvProbe,
    default_probe,
    diagnose,
    render_text,
)
from microduck_cli.env.hosts import HostInfo
from tests.test_no_secrets_in_output import assert_no_secrets

# --- fixtures ----------------------------------------------------------

EMPTY_PROBE = EnvProbe()

_HEALTHY_HOST = HostInfo(
    host_class="x86_64",
    display_name="x86_64 host",
    torch_source_applies=False,
)

COMPLETE_PROBE = EnvProbe(
    microduck_clone="/opt/microduck",
    microduck_clone_commit="0cd676d6fbb6e90a762c84aa63abe7a02dbc9495",
    microduck_pinned_commit="0cd676d6fbb6e90a762c84aa63abe7a02dbc9495",
    rl_clone="/opt/microduck_rl",
    rl_clone_commit="29e887ecfbf5d37144759e5a9f8a176dfb83d547",
    rl_pinned_commit="29e887ecfbf5d37144759e5a9f8a176dfb83d547",
    cargo_version="cargo 1.89.0 (c9dbb45f8 2025-06-06)",
    built_binaries={"robotd": True, "robotctl": True, "tofd": True, "sounds": True},
    rl_venv_present=True,
    rl_onnxruntime_path="/opt/microduck_rl/.venv/lib/onnxruntime/libonnxruntime.so.1",
    state_dir="/tmp/duck-sim",
    body_port_free=True,
    host=_HEALTHY_HOST,
    secrets={"HF_TOKEN": True, "WANDB_API_KEY": True, "DUCK_PIN": True},
    hf_auth_user="ori",
)


# --- report shape --------------------------------------------------------


def test_report_is_rubric_shaped():
    report = diagnose(EMPTY_PROBE)
    assert set(report.keys()) == {"healthy", "checks"}
    assert isinstance(report["healthy"], bool)
    assert isinstance(report["checks"], list)
    for check in report["checks"]:
        assert set(check.keys()) == {"id", "passed", "severity", "message", "remediation"}
        assert isinstance(check["id"], str)
        assert isinstance(check["passed"], bool)
        assert check["severity"] in {"error", "warning", "info"}
        assert isinstance(check["message"], str)
        assert isinstance(check["remediation"], str)


def test_report_is_json_serializable():
    report = diagnose(COMPLETE_PROBE)
    json.dumps(report)  # must not raise


# --- empty / nothing-installed fixture -----------------------------------


def test_empty_probe_is_unhealthy():
    report = diagnose(EMPTY_PROBE)
    assert report["healthy"] is False


def test_empty_probe_lists_every_missing_item_with_a_pollen_robotics_url():
    report = diagnose(EMPTY_PROBE)
    required_failed = [c for c in report["checks"] if c["severity"] == "error" and not c["passed"]]
    # Every required check should be present and failing on a fixture with
    # nothing installed.
    required_ids = {c["id"] for c in required_failed}
    assert required_ids == {
        "microduck_clone_present",
        "microduck_pinned_commit",
        "cargo_version",
        "daemons_built",
        "rl_clone_present",
        "rl_pinned_commit",
        "rl_venv_with_onnxruntime",
        "body_port_free",
        "state_dir_length",
    } or required_ids >= {
        "microduck_clone_present",
        "microduck_pinned_commit",
        "cargo_version",
        "daemons_built",
        "rl_clone_present",
        "rl_pinned_commit",
        "rl_venv_with_onnxruntime",
        "body_port_free",
    }
    for check in required_failed:
        assert check["remediation"], f"{check['id']} has no remediation"
        assert "https://github.com/pollen-robotics/" in check["remediation"], check["id"]


# --- complete fixture ------------------------------------------------------


def test_complete_probe_is_healthy():
    report = diagnose(COMPLETE_PROBE)
    assert report["healthy"] is True
    for check in report["checks"]:
        if check["severity"] == "error":
            assert check["passed"] is True, check


def test_complete_probe_all_ids_present_exactly_once():
    report = diagnose(COMPLETE_PROBE)
    ids = [c["id"] for c in report["checks"]]
    assert len(ids) == len(set(ids))
    assert ids == [
        "microduck_clone_present",
        "microduck_pinned_commit",
        "cargo_version",
        "daemons_built",
        "rl_clone_present",
        "rl_pinned_commit",
        "rl_venv_with_onnxruntime",
        "state_dir_length",
        "body_port_free",
        "host_class",
        "hf_auth",
        "wandb_key",
        "duck_pin",
    ]


# --- individual check behaviour -------------------------------------------


def test_microduck_pinned_commit_error_when_clone_absent():
    report = diagnose(EMPTY_PROBE)
    check = next(c for c in report["checks"] if c["id"] == "microduck_pinned_commit")
    assert check["passed"] is False
    assert check["severity"] == "error"


def test_microduck_pinned_commit_warning_on_mismatch():
    probe = EnvProbe(
        microduck_clone="/opt/microduck",
        microduck_clone_commit="deadbeef",
        microduck_pinned_commit="0cd676d6fbb6e90a762c84aa63abe7a02dbc9495",
    )
    report = diagnose(probe)
    check = next(c for c in report["checks"] if c["id"] == "microduck_pinned_commit")
    assert check["passed"] is False
    assert check["severity"] == "warning"
    # A warning never blocks health by itself (other required checks still do).
    assert "0cd676d6" in check["message"]


def test_cargo_version_below_minimum_fails():
    probe = EnvProbe(cargo_version="cargo 1.70.0 (abc 2023-01-01)")
    check = next(c for c in diagnose(probe)["checks"] if c["id"] == "cargo_version")
    assert check["passed"] is False
    assert check["severity"] == "error"


def test_cargo_version_at_minimum_passes():
    probe = EnvProbe(cargo_version="cargo 1.89.0 (c9dbb45f8 2025-06-06)")
    check = next(c for c in diagnose(probe)["checks"] if c["id"] == "cargo_version")
    assert check["passed"] is True


def test_daemons_built_lists_missing_binaries():
    probe = EnvProbe(built_binaries={"robotd": True, "robotctl": False})
    check = next(c for c in diagnose(probe)["checks"] if c["id"] == "daemons_built")
    assert check["passed"] is False
    assert "robotctl" in check["message"]
    assert "tofd" in check["message"]
    assert "sounds" in check["message"]
    assert "robotd" not in check["message"].split(":", 1)[1].split(",")[0]


def test_rl_venv_without_onnxruntime_fails():
    probe = EnvProbe(rl_venv_present=True, rl_onnxruntime_path=None)
    check = next(c for c in diagnose(probe)["checks"] if c["id"] == "rl_venv_with_onnxruntime")
    assert check["passed"] is False


def test_state_dir_length_flags_a_long_path():
    probe = EnvProbe(state_dir="/" + ("x" * 120))
    check = next(c for c in diagnose(probe)["checks"] if c["id"] == "state_dir_length")
    assert check["passed"] is False
    assert "DUCK_SIM_STATE" in check["remediation"] or "shorter" in check["remediation"]


def test_state_dir_length_passes_a_short_path():
    probe = EnvProbe(state_dir="/tmp/duck-sim")
    check = next(c for c in diagnose(probe)["checks"] if c["id"] == "state_dir_length")
    assert check["passed"] is True


def test_body_port_free_none_is_treated_as_failure():
    probe = EnvProbe(body_port_free=None)
    check = next(c for c in diagnose(probe)["checks"] if c["id"] == "body_port_free")
    assert check["passed"] is False
    assert check["severity"] == "error"


def test_body_port_in_use_fails():
    probe = EnvProbe(body_port_free=False)
    check = next(c for c in diagnose(probe)["checks"] if c["id"] == "body_port_free")
    assert check["passed"] is False


def test_host_class_is_info_and_never_blocks_health():
    host = HostInfo(
        host_class="jetson-thor",
        display_name="NVIDIA Jetson AGX Thor",
        torch_source_applies=False,
        remediation="torch source unverified here",
    )
    probe = EnvProbe(
        microduck_clone="/opt/microduck",
        microduck_clone_commit="0cd676d6fbb6e90a762c84aa63abe7a02dbc9495",
        microduck_pinned_commit="0cd676d6fbb6e90a762c84aa63abe7a02dbc9495",
        rl_clone="/opt/microduck_rl",
        rl_clone_commit="29e887ecfbf5d37144759e5a9f8a176dfb83d547",
        rl_pinned_commit="29e887ecfbf5d37144759e5a9f8a176dfb83d547",
        cargo_version="cargo 1.89.0 (c9dbb45f8 2025-06-06)",
        built_binaries={"robotd": True, "robotctl": True, "tofd": True, "sounds": True},
        rl_venv_present=True,
        rl_onnxruntime_path="/opt/microduck_rl/.venv/libonnxruntime.so.1",
        state_dir="/tmp/duck-sim",
        body_port_free=True,
        host=host,
    )
    report = diagnose(probe)
    assert report["healthy"] is True
    check = next(c for c in report["checks"] if c["id"] == "host_class")
    assert check["severity"] == "info"
    assert "torch source unverified here" in check["remediation"]


def test_hf_auth_wandb_duck_pin_are_set_unset_only():
    probe = EnvProbe(
        secrets={"HF_TOKEN": True, "WANDB_API_KEY": False, "DUCK_PIN": True},
        hf_auth_user="someone",
    )
    report = diagnose(probe)
    hf_check = next(c for c in report["checks"] if c["id"] == "hf_auth")
    wandb_check = next(c for c in report["checks"] if c["id"] == "wandb_key")
    pin_check = next(c for c in report["checks"] if c["id"] == "duck_pin")

    assert hf_check["severity"] == "info"
    assert "someone" in hf_check["message"]
    assert wandb_check["severity"] == "info"
    assert "unset" in wandb_check["message"]
    assert pin_check["severity"] == "info"
    assert "set" in pin_check["message"]


# --- secrets never leak ----------------------------------------------------


def test_output_never_contains_secret_values_text_and_json():
    sentinels = {
        "HF_TOKEN": "hf-sentinel-should-not-leak",
        "WANDB_API_KEY": "wandb-sentinel-should-not-leak",
        "DUCK_PIN": "1234-sentinel-should-not-leak",
    }
    # The EnvProbe type itself only ever carries booleans for secrets — it
    # is structurally incapable of holding these sentinel strings. We prove
    # that by driving default_probe() with an environment carrying the
    # sentinel values, and asserting the resulting probe (and everything
    # rendered from it) never contains them.
    import os as _os

    env_backup = {name: _os.environ.get(name) for name in sentinels}
    try:
        for name, value in sentinels.items():
            _os.environ[name] = value
        probe = default_probe()
    finally:
        for name, original in env_backup.items():
            if original is None:
                _os.environ.pop(name, None)
            else:
                _os.environ[name] = original

    # secrets is boolean-only.
    for name in sentinels:
        assert isinstance(probe.secrets.get(name), bool)

    report = diagnose(probe)
    text_output = render_text(report)
    json_output = json.dumps(report)

    assert_no_secrets(text_output, sentinels=sentinels)
    assert_no_secrets(json_output, sentinels=sentinels)


def test_complete_probe_render_text_and_json_never_contain_secrets():
    sentinels = {
        "HF_TOKEN": "hf-sentinel-abc",
        "WANDB_API_KEY": "wandb-sentinel-def",
        "DUCK_PIN": "pin-sentinel-ghi",
    }
    report = diagnose(COMPLETE_PROBE)
    text_output = render_text(report)
    json_output = json.dumps(report)
    assert_no_secrets(text_output, sentinels=sentinels)
    assert_no_secrets(json_output, sentinels=sentinels)


# --- render_text style -----------------------------------------------------


def test_render_text_matches_commands_doctor_style():
    report = diagnose(EMPTY_PROBE)
    text = render_text(report)
    lines = text.splitlines()
    assert lines[0].startswith("microduck-cli env doctor: unhealthy")
    ok_or_fail = [line for line in lines if line.startswith("[ok]") or line.startswith("[FAIL]")]
    assert ok_or_fail
    hints = [line for line in lines if line.strip().startswith("hint:")]
    assert hints
    for hint in hints:
        assert hint.startswith("  hint:")


def test_render_text_healthy_on_complete_probe():
    report = diagnose(COMPLETE_PROBE)
    text = render_text(report)
    assert text.startswith("microduck-cli env doctor: healthy")


# --- default_probe never raises --------------------------------------------


def test_default_probe_never_raises_and_returns_env_probe():
    probe = default_probe()
    assert isinstance(probe, EnvProbe)
    # Must be diagnosable without raising regardless of what's installed here.
    report = diagnose(probe)
    assert isinstance(report["healthy"], bool)
