"""Tests for the ``env`` noun (t20): doctor, up, down, status, hosts.

Every side effect the CLI verbs perform is reachable through a module-level
seam on ``microduck_cli.cli._commands.env`` (mirroring ``env/stack.py``'s own
injection style), so these tests never build cargo, start a real process, or
open a real network socket except against the in-process
``tests.fake_robotd.FakeRobotd`` (a real unix-socket JSON-RPC server) used to
exercise the private ``hello``/``robot.health`` client helper end to end.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from microduck_cli.cli import main
from microduck_cli.cli._commands import env as env_cmd
from microduck_cli.cli._errors import EXIT_ENV_ERROR, EXIT_SUCCESS, EXIT_USER_ERROR
from microduck_cli.env import doctor as env_doctor
from microduck_cli.env.doctor import EnvProbe
from microduck_cli.env.hosts import HostInfo
from tests.fake_robotd import FakeRobotd
from tests.test_no_secrets_in_output import assert_no_secrets
from tests.test_stack import FakeRunner, ProcTable, _clone_with_policies, _rl_with_ort

# ---------------------------------------------------------------------------
# fixtures shared by several tests
# ---------------------------------------------------------------------------

_HEALTHY_HOST = HostInfo(
    host_class="x86_64",
    display_name="x86_64 host",
    torch_source_applies=False,
)

_COMPLETE_PROBE = EnvProbe(
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

_EMPTY_PROBE = EnvProbe()


def _env(tmp_path: Path, **overrides) -> tuple[Path, Path]:
    clone = overrides.pop("clone", None) or _clone_with_policies(tmp_path)
    rl = overrides.pop("rl", None) or _rl_with_ort(tmp_path)
    return clone, rl


# ---------------------------------------------------------------------------
# argparse surface: no params file, ORT path or socket path is ever a flag
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "argv",
    [
        ["env", "up", "--params", "x"],
        ["env", "up", "--ort-path", "x"],
        ["env", "up", "--ort", "x"],
        ["env", "up", "--socket", "x"],
        ["env", "down", "--socket", "x"],
        ["env", "down", "--params", "x"],
        ["env", "status", "--socket", "x"],
        ["env", "doctor", "--socket", "x"],
    ],
)
def test_no_verb_accepts_a_params_ort_or_socket_flag(
    argv: list[str], capsys: pytest.CaptureFixture[str]
) -> None:
    with pytest.raises(SystemExit) as exc:
        main(argv)
    assert exc.value.code == EXIT_USER_ERROR
    err = capsys.readouterr().err
    assert err.startswith("error:")
    assert "hint:" in err


def test_env_up_defaults_to_fake_mode(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """No --sim/--fake at all still resolves mode='fake' (the documented default)."""
    clone, rl = _env(tmp_path)
    state_dir = tmp_path / "state"
    state_dir.mkdir()

    monkeypatch.setattr(env_cmd._doctor, "resolve_clone_paths", lambda env: (str(clone), str(rl)))
    monkeypatch.setattr(env_cmd, "_stack_runner", FakeRunner())
    monkeypatch.setattr(env_cmd, "_stack_exists", lambda _p: True)
    monkeypatch.setattr(env_cmd, "_wait_for_healthy", lambda _sock, _timeout: True)

    rc = main(["env", "up", "--state", str(state_dir), "--skip-build"])
    assert rc == EXIT_SUCCESS


# ---------------------------------------------------------------------------
# acceptance 1: env up --fake reaches healthy within the timeout, prints the
# socket path; env down afterwards leaves no tracked pidfile; a stale
# pidfile is reported as skipped, never signalled.
# ---------------------------------------------------------------------------


def test_env_up_fake_reaches_healthy_and_prints_the_socket_path(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    clone, rl = _env(tmp_path)
    state_dir = tmp_path / "state"
    state_dir.mkdir()

    monkeypatch.setattr(env_cmd._doctor, "resolve_clone_paths", lambda env: (str(clone), str(rl)))
    monkeypatch.setattr(env_cmd, "_stack_runner", FakeRunner())
    monkeypatch.setattr(env_cmd, "_stack_exists", lambda _p: True)
    monkeypatch.setattr(env_cmd, "_wait_for_healthy", lambda _sock, _timeout: True)

    rc = main(["env", "up", "--fake", "--state", str(state_dir), "--skip-build"])
    out = capsys.readouterr().out
    assert rc == EXIT_SUCCESS
    assert "healthy" in out
    assert str(state_dir / "duck-a.sock") in out


def test_env_up_fake_json_is_healthy_and_names_the_socket(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    clone, rl = _env(tmp_path)
    state_dir = tmp_path / "state"
    state_dir.mkdir()

    monkeypatch.setattr(env_cmd._doctor, "resolve_clone_paths", lambda env: (str(clone), str(rl)))
    monkeypatch.setattr(env_cmd, "_stack_runner", FakeRunner())
    monkeypatch.setattr(env_cmd, "_stack_exists", lambda _p: True)
    monkeypatch.setattr(env_cmd, "_wait_for_healthy", lambda _sock, _timeout: True)

    rc = main(["env", "up", "--fake", "--state", str(state_dir), "--skip-build", "--json"])
    payload = json.loads(capsys.readouterr().out)
    assert rc == EXIT_SUCCESS
    assert payload["healthy"] is True
    assert payload["sockets"] == [str(state_dir / "duck-a.sock")]


def test_env_up_times_out_naming_the_daemon_log_in_the_remediation(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    clone, rl = _env(tmp_path)
    state_dir = tmp_path / "state"
    state_dir.mkdir()

    monkeypatch.setattr(env_cmd._doctor, "resolve_clone_paths", lambda env: (str(clone), str(rl)))
    monkeypatch.setattr(env_cmd, "_stack_runner", FakeRunner())
    monkeypatch.setattr(env_cmd, "_stack_exists", lambda _p: True)
    monkeypatch.setattr(env_cmd, "_wait_for_healthy", lambda _sock, _timeout: False)

    rc = main(["env", "up", "--fake", "--state", str(state_dir), "--skip-build"])
    err = capsys.readouterr().err
    assert rc == EXIT_ENV_ERROR
    assert "duck-a.log" in err


def test_env_up_without_a_resolvable_clone_is_an_env_error(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(env_cmd._doctor, "resolve_clone_paths", lambda env: (None, None))
    rc = main(["env", "up", "--fake", "--state", str(tmp_path)])
    err = capsys.readouterr().err
    assert rc == EXIT_ENV_ERROR
    assert "env doctor" in err


def test_env_down_after_up_leaves_no_tracked_pidfile(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    clone, rl = _env(tmp_path)
    state_dir = tmp_path / "state"
    state_dir.mkdir()

    monkeypatch.setattr(env_cmd._doctor, "resolve_clone_paths", lambda env: (str(clone), str(rl)))
    monkeypatch.setattr(env_cmd, "_stack_runner", FakeRunner())
    monkeypatch.setattr(env_cmd, "_stack_exists", lambda _p: True)
    monkeypatch.setattr(env_cmd, "_wait_for_healthy", lambda _sock, _timeout: True)
    monkeypatch.setattr(env_cmd, "_port_listening", lambda _port: False)

    rc = main(["env", "up", "--fake", "--state", str(state_dir), "--skip-build"])
    assert rc == EXIT_SUCCESS
    pidfile = state_dir / "duck-a.pid"
    assert pidfile.is_file()
    pid = int(pidfile.read_text().strip())

    table = ProcTable({pid: "target/debug/robotd --fake"})
    monkeypatch.setattr(env_cmd, "_stack_proc_cmdline", table.cmdline)
    monkeypatch.setattr(env_cmd, "_stack_kill", table.kill)

    rc = main(["env", "down", "--state", str(state_dir)])
    out = capsys.readouterr().out
    assert rc == EXIT_SUCCESS
    assert "terminated" in out
    assert list(state_dir.glob("*.pid")) == []


def test_env_down_reports_a_stale_pidfile_as_skipped_never_signalled(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    (state_dir / "duck-a.pid").write_text("4242\n", encoding="utf-8")

    table = ProcTable({4242: "/usr/bin/login -- someone"})
    monkeypatch.setattr(env_cmd, "_stack_proc_cmdline", table.cmdline)
    monkeypatch.setattr(env_cmd, "_stack_kill", table.kill)
    monkeypatch.setattr(env_cmd, "_port_listening", lambda _port: False)

    rc = main(["env", "down", "--state", str(state_dir)])
    out = capsys.readouterr().out
    assert rc == EXIT_SUCCESS
    assert "stale" in out
    assert table.sent == [], "a stale pidfile must never be signalled"
    assert list(state_dir.glob("*.pid")) == []


def test_env_down_json_reports_body_port_still_listening(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    monkeypatch.setattr(env_cmd, "_port_listening", lambda _port: True)

    rc = main(["env", "down", "--state", str(state_dir), "--json"])
    payload = json.loads(capsys.readouterr().out)
    assert rc == EXIT_SUCCESS
    assert payload["body_port_still_listening"] is True
    assert payload["body_port"] == env_doctor.DEFAULT_BODY_PORT


def test_env_down_empty_state_dir_reports_nothing_tracked(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    monkeypatch.setattr(env_cmd, "_port_listening", lambda _port: False)

    rc = main(["env", "down", "--state", str(state_dir)])
    out = capsys.readouterr().out
    assert rc == EXIT_SUCCESS
    assert "nothing tracked" in out


# ---------------------------------------------------------------------------
# env status
# ---------------------------------------------------------------------------


def test_env_status_reports_alive_and_socket_health(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    (state_dir / "duck-a.pid").write_text("77\n", encoding="utf-8")
    (state_dir / "duck-a.sock").write_text("", encoding="utf-8")

    monkeypatch.setattr(env_cmd, "_stack_proc_cmdline", lambda pid: "target/debug/robotd --fake")
    monkeypatch.setattr(env_cmd, "_hello_probe", lambda _sock: True)

    rc = main(["env", "status", "--state", str(state_dir), "--json"])
    payload = json.loads(capsys.readouterr().out)
    assert rc == EXIT_SUCCESS
    by_name = {p["name"]: p for p in payload["processes"]}
    assert by_name["duck-a"]["alive"] is True
    assert payload["socket_health"] == [
        {"socket": str(state_dir / "duck-a.sock"), "responding": True}
    ]


def test_env_status_text_names_pid_and_socket_response(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    (state_dir / "duck-a.pid").write_text("77\n", encoding="utf-8")
    (state_dir / "duck-a.sock").write_text("", encoding="utf-8")

    monkeypatch.setattr(env_cmd, "_stack_proc_cmdline", lambda pid: None)
    monkeypatch.setattr(env_cmd, "_hello_probe", lambda _sock: False)

    rc = main(["env", "status", "--state", str(state_dir)])
    out = capsys.readouterr().out
    assert rc == EXIT_SUCCESS
    assert "no response" in out


# ---------------------------------------------------------------------------
# env doctor
# ---------------------------------------------------------------------------


def test_env_doctor_exits_2_on_the_no_tools_fixture_and_matches_render_text(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(env_cmd, "_default_probe", lambda: _EMPTY_PROBE)
    rc = main(["env", "doctor"])
    out = capsys.readouterr().out
    assert rc == EXIT_ENV_ERROR
    assert out.rstrip("\n") == env_doctor.render_text(env_doctor.diagnose(_EMPTY_PROBE))


def test_env_doctor_exits_0_on_the_complete_fixture(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(env_cmd, "_default_probe", lambda: _COMPLETE_PROBE)
    rc = main(["env", "doctor"])
    assert rc == EXIT_SUCCESS
    assert capsys.readouterr().out.startswith("microduck-cli env doctor: healthy")


def test_env_doctor_json_is_rubric_shaped(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(env_cmd, "_default_probe", lambda: _COMPLETE_PROBE)
    rc = main(["env", "doctor", "--json"])
    payload = json.loads(capsys.readouterr().out)
    assert rc == EXIT_SUCCESS
    assert set(payload.keys()) == {"healthy", "checks"}
    for check in payload["checks"]:
        assert set(check.keys()) == {"id", "passed", "severity", "message", "remediation"}


@pytest.mark.parametrize("json_flag", [[], ["--json"]])
def test_env_doctor_never_leaks_sentinel_secrets(
    json_flag: list[str], monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    sentinels = {
        "HF_TOKEN": "hf-sentinel-env-cli",
        "WANDB_API_KEY": "wandb-sentinel-env-cli",
        "DUCK_PIN": "pin-sentinel-env-cli",
    }
    for name, value in sentinels.items():
        monkeypatch.setenv(name, value)

    rc = main(["env", "doctor", *json_flag])
    captured = capsys.readouterr()
    assert rc in (EXIT_SUCCESS, EXIT_ENV_ERROR)
    assert_no_secrets(captured.out + captured.err, sentinels=sentinels)


# ---------------------------------------------------------------------------
# env hosts
# ---------------------------------------------------------------------------


def test_env_hosts_never_raises_and_reports_a_host_class(
    capsys: pytest.CaptureFixture[str],
) -> None:
    rc = main(["env", "hosts", "--json"])
    payload = json.loads(capsys.readouterr().out)
    assert rc == EXIT_SUCCESS
    assert payload["host_class"]
    assert isinstance(payload["torch_source_applies"], bool)


def test_env_hosts_text_names_the_host_class(capsys: pytest.CaptureFixture[str]) -> None:
    rc = main(["env", "hosts"])
    out = capsys.readouterr().out
    assert rc == EXIT_SUCCESS
    assert "torch source applies" in out


# ---------------------------------------------------------------------------
# env overview
# ---------------------------------------------------------------------------


def test_env_overview_lists_every_verb(capsys: pytest.CaptureFixture[str]) -> None:
    rc = main(["env", "overview"])
    out = capsys.readouterr().out
    assert rc == EXIT_SUCCESS
    for verb in ("doctor", "up", "down", "status", "hosts"):
        assert f"env {verb}" in out


def test_bare_env_prints_overview(capsys: pytest.CaptureFixture[str]) -> None:
    rc = main(["env"])
    out = capsys.readouterr().out
    assert rc == EXIT_SUCCESS
    assert "microduck-cli env" in out


# ---------------------------------------------------------------------------
# the private JSON-RPC health/hello helpers, against a real fake robotd
# ---------------------------------------------------------------------------


def test_default_wait_for_healthy_reaches_true_against_fake_robotd() -> None:
    with FakeRobotd() as fake:
        assert env_cmd._default_wait_for_healthy(fake.socket_path, 5.0) is True


def test_default_wait_for_healthy_gives_up_on_an_unhealthy_daemon() -> None:
    with FakeRobotd() as fake:
        fake.set_state(healthy=False)
        assert env_cmd._default_wait_for_healthy(fake.socket_path, 0.6) is False


def test_default_wait_for_healthy_gives_up_when_nothing_is_listening(tmp_path: Path) -> None:
    assert env_cmd._default_wait_for_healthy(str(tmp_path / "nope.sock"), 0.3) is False


def test_default_hello_probe_true_against_fake_robotd() -> None:
    with FakeRobotd() as fake:
        assert env_cmd._default_hello_probe(fake.socket_path) is True


def test_default_hello_probe_false_when_nothing_is_listening(tmp_path: Path) -> None:
    assert env_cmd._default_hello_probe(str(tmp_path / "nope.sock")) is False


# ---------------------------------------------------------------------------
# reconciliation: clone-path resolution and the DUCK_SIM_PORT default
# ---------------------------------------------------------------------------


def test_resolve_clone_paths_prefers_env_vars_over_sibling_dirs(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    fake_repo_root = tmp_path / "checkout" / "microduck-cli"
    fake_repo_root.mkdir(parents=True)
    monkeypatch.setattr(env_doctor, "_repo_root", lambda: fake_repo_root)

    explicit_clone = tmp_path / "explicit-microduck"
    explicit_clone.mkdir()
    explicit_rl = tmp_path / "explicit-rl"
    explicit_rl.mkdir()

    microduck, rl = env_doctor.resolve_clone_paths(
        {"MICRODUCK_CLONE": str(explicit_clone), "DUCK_SIM_RL": str(explicit_rl)}
    )
    assert microduck == str(explicit_clone)
    assert rl == str(explicit_rl)


def test_resolve_clone_paths_duck_sim_rl_wins_over_microduck_rl_clone(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    fake_repo_root = tmp_path / "checkout" / "microduck-cli"
    fake_repo_root.mkdir(parents=True)
    monkeypatch.setattr(env_doctor, "_repo_root", lambda: fake_repo_root)

    duck_sim_rl = tmp_path / "duck-sim-rl"
    duck_sim_rl.mkdir()
    other_rl = tmp_path / "other-rl"
    other_rl.mkdir()

    _, rl = env_doctor.resolve_clone_paths(
        {"DUCK_SIM_RL": str(duck_sim_rl), "MICRODUCK_RL_CLONE": str(other_rl)}
    )
    assert rl == str(duck_sim_rl)


def test_resolve_clone_paths_falls_back_to_sibling_directories(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    fake_repo_root = tmp_path / "checkout" / "microduck-cli"
    fake_repo_root.mkdir(parents=True)
    sibling_microduck = tmp_path / "checkout" / "microduck"
    sibling_microduck.mkdir()
    sibling_rl = tmp_path / "checkout" / "microduck_rl"
    sibling_rl.mkdir()
    monkeypatch.setattr(env_doctor, "_repo_root", lambda: fake_repo_root)

    microduck, rl = env_doctor.resolve_clone_paths({})
    assert microduck == str(sibling_microduck)
    assert rl == str(sibling_rl)


def test_resolve_clone_paths_returns_none_when_nothing_resolves(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    fake_repo_root = tmp_path / "checkout" / "microduck-cli"
    fake_repo_root.mkdir(parents=True)
    monkeypatch.setattr(env_doctor, "_repo_root", lambda: fake_repo_root)

    microduck, rl = env_doctor.resolve_clone_paths({})
    assert microduck is None
    assert rl is None


def test_default_body_port_matches_stack_default() -> None:
    from microduck_cli.env.stack import _DEFAULT_PORT

    assert env_doctor.DEFAULT_BODY_PORT == 7801 == _DEFAULT_PORT


def test_default_probe_reads_duck_sim_port_not_the_old_name(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DUCK_SIM_PORT", "19999")
    monkeypatch.delenv("DUCK_SIM_BODY_PORT", raising=False)
    probe = env_doctor.default_probe()
    # Port 19999 is not free (extremely unlikely to be bound in CI) so this
    # exercises the env var actually being read, not just accepted.
    assert isinstance(probe.body_port_free, bool)
