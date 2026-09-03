"""Tests for microduck_cli.env.stack (sim stack lifecycle, t13).

Every subprocess call is injected. Nothing here starts a process, reads
``/proc``, or sends a signal to anything real: ``runner``, ``proc_cmdline``,
``kill``, ``sleep`` and ``monotonic`` are all fakes, so the suite runs on a
box with no cargo, no clone and no simulator.
"""

from __future__ import annotations

import signal
import tomllib
from pathlib import Path

import pytest

from microduck_cli.cli._errors import EXIT_ENV_ERROR, EXIT_USER_ERROR, CliError
from microduck_cli.env.stack import ORT_ENV_VAR, SimStack, expected_marker

# --- fakes -----------------------------------------------------------------


class FakeProc:
    """A Popen-like: a pid, and a poll() that reports an already-exited child."""

    def __init__(self, pid: int, returncode: int | None = 0) -> None:
        self.pid = pid
        self._returncode = returncode

    def poll(self) -> int | None:
        return self._returncode


class FakeRunner:
    """Records every launch and hands back a FakeProc with a fresh pid."""

    def __init__(self, *, first_pid: int = 1000, build_status: int = 0) -> None:
        self.calls: list[dict] = []
        self._next_pid = first_pid
        self.build_status = build_status

    def __call__(self, *, argv, cwd=None, env=None, stdout=None, stderr=None):
        pid = self._next_pid
        self._next_pid += 1
        self.calls.append(
            {"argv": list(argv), "cwd": cwd, "env": dict(env or {}), "stdout": stdout, "pid": pid}
        )
        status = self.build_status if argv[0] == "cargo" else None
        return FakeProc(pid, returncode=status)

    @property
    def argvs(self) -> list[list[str]]:
        return [call["argv"] for call in self.calls]


class FakeSignals:
    """Records signals; never sends one."""

    def __init__(self) -> None:
        self.sent: list[tuple[int, int]] = []

    def __call__(self, pid: int, sig: int) -> None:
        self.sent.append((pid, sig))


class ProcTable:
    """A fake ``/proc``: pid -> cmdline, with signal delivery modelled.

    ``kill`` records the signal and, unless the pid is in ``ignore_term``,
    removes the entry — which is what a process exiting on SIGTERM looks
    like from ``/proc``.
    """

    def __init__(self, cmdlines: dict[int, str], *, ignore_term: tuple[int, ...] = ()) -> None:
        self.cmdlines = dict(cmdlines)
        self.ignore_term = ignore_term
        self.sent: list[tuple[int, int]] = []

    def cmdline(self, pid: int) -> str | None:
        return self.cmdlines.get(pid)

    def kill(self, pid: int, sig: int) -> None:
        self.sent.append((pid, sig))
        if sig == signal.SIGKILL or (sig == signal.SIGTERM and pid not in self.ignore_term):
            self.cmdlines.pop(pid, None)


class FakeClock:
    def __init__(self) -> None:
        self.now = 0.0

    def monotonic(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.now += seconds


def _clone_with_policies(tmp_path: Path) -> Path:
    clone = tmp_path / "microduck"
    policies = clone / "policies"
    policies.mkdir(parents=True)
    for name in (
        "alpha_walking.onnx",
        "alpha_stand.onnx",
        "alpha_sitstand.onnx",
        "alpha_ground_pick.onnx",
        "ball_kick_left.onnx",
        "ball_kick_right.onnx",
        "roulade.onnx",
    ):
        (policies / name).write_bytes(b"")
    (clone / "target" / "debug").mkdir(parents=True)
    return clone


def _rl_with_ort(tmp_path: Path) -> Path:
    rl = tmp_path / "microduck_rl"
    capi = rl / ".venv" / "lib" / "python3.12" / "site-packages" / "onnxruntime" / "capi"
    capi.mkdir(parents=True)
    (capi / "libonnxruntime.so.1.24.4").write_bytes(b"")
    # The providers stub must not be mistaken for the runtime itself.
    (capi / "libonnxruntime_providers_shared.so").write_bytes(b"")
    return rl


def _stack(tmp_path: Path, **overrides) -> SimStack:
    clone = overrides.pop("clone", None) or _clone_with_policies(tmp_path)
    rl = overrides.pop("rl", None) or _rl_with_ort(tmp_path)
    state = tmp_path / "st"
    state.mkdir(exist_ok=True)
    clock = overrides.pop("clock", None) or FakeClock()
    kwargs = {
        "clone": str(clone),
        "rl": str(rl),
        "state_dir": str(state),
        "runner": FakeRunner(),
        "proc_cmdline": lambda pid: None,
        "kill": FakeSignals(),
        "sleep": clock.sleep,
        "monotonic": clock.monotonic,
        "base_env": {"PATH": "/usr/bin"},
    }
    kwargs.update(overrides)
    return SimStack(**kwargs)


def _sockets_appear(stack: SimStack) -> None:
    """Make every socket the stack waits on already present."""
    stack.exists = lambda path: Path(path).exists() or path.endswith(".sock")


# --- acceptance 1: the documented command lines, in order ------------------


def test_up_fake_issues_cargo_build_then_robotd_fake(tmp_path: Path) -> None:
    stack = _stack(tmp_path)
    _sockets_appear(stack)

    report = stack.up(mode="fake")

    argvs = stack.runner.argvs
    assert argvs[0] == [
        "cargo",
        "build",
        "--quiet",
        "-p",
        "robotd",
        "-p",
        "robotctl",
        "-p",
        "tof",
        "-p",
        "sounds",
    ]
    assert len(argvs) == 2, "fake mode starts no body"
    robotd = str(Path(stack.clone) / "target" / "debug" / "robotd")
    assert argvs[1] == [
        robotd,
        "--fake",
        "--socket",
        f"{stack.state_dir}/duck-a.sock",
        "--params",
        f"{stack.state_dir}/duck-a.toml",
    ]
    assert "--sim" not in argvs[1], "--sim conflicts with --fake"
    assert report.mode == "fake"
    assert report.built is True


def test_up_sim_issues_body_then_one_robotd_per_duck(tmp_path: Path) -> None:
    stack = _stack(tmp_path)
    _sockets_appear(stack)

    report = stack.up(mode="sim", ducks=2, port=7801, scene="apartment", headless=True)

    argvs = stack.runner.argvs
    assert argvs[0][0] == "cargo"
    assert argvs[1] == [
        f"{stack.rl}/.venv/bin/python",
        "-m",
        "mjlab_microduck.sim.body_server",
        "--port",
        "7801",
        "--ducks",
        "2",
        "--headless",
        "--scene",
        f"{stack.rl}/src/mjlab_microduck/robot/microduck/scene_apartment.xml",
    ]
    assert stack.runner.calls[1]["cwd"] == stack.rl

    robotd = str(Path(stack.clone) / "target" / "debug" / "robotd")
    assert argvs[2][:4] == [robotd, "--sim", "127.0.0.1:7801", "--socket"]
    assert argvs[3][:4] == [robotd, "--sim", "127.0.0.1:7802", "--socket"]
    assert argvs[2][4] == f"{stack.state_dir}/duck-a.sock"
    assert argvs[3][4] == f"{stack.state_dir}/duck-b.sock"
    assert [p.name for p in report.processes] == ["body", "duck-a", "duck-b"]


def test_scene_with_a_slash_is_passed_through_verbatim(tmp_path: Path) -> None:
    stack = _stack(tmp_path)
    _sockets_appear(stack)
    stack.up(mode="sim", scene="/worlds/mine.xml")
    assert stack.runner.argvs[1][-1] == "/worlds/mine.xml"


def test_skip_build_omits_cargo(tmp_path: Path) -> None:
    stack = _stack(tmp_path)
    _sockets_appear(stack)
    report = stack.up(mode="fake", skip_build=True)
    assert all(argv[0] != "cargo" for argv in stack.runner.argvs)
    assert report.built is False


def test_ort_is_exported_to_every_daemon(tmp_path: Path) -> None:
    stack = _stack(tmp_path)
    _sockets_appear(stack)
    stack.up(mode="fake")

    robotd_call = stack.runner.calls[-1]
    assert robotd_call["env"][ORT_ENV_VAR].endswith("libonnxruntime.so.1.24.4")
    assert "_providers_shared" not in robotd_call["env"][ORT_ENV_VAR]
    assert robotd_call["env"]["DUCK_IDENTITY"] == "duck-a"
    assert robotd_call["env"]["DUCK_RUNTIME_DIR"] == stack.state_dir


def test_missing_onnxruntime_is_an_env_error_naming_uv_sync(tmp_path: Path) -> None:
    empty_rl = tmp_path / "empty_rl"
    empty_rl.mkdir()
    stack = _stack(tmp_path, rl=empty_rl)
    with pytest.raises(CliError) as excinfo:
        stack.up(mode="fake", skip_build=True)
    assert excinfo.value.code == EXIT_ENV_ERROR
    assert "uv sync" in excinfo.value.remediation
    assert ORT_ENV_VAR in excinfo.value.remediation


def test_failed_cargo_build_is_an_env_error(tmp_path: Path) -> None:
    stack = _stack(tmp_path, runner=FakeRunner(build_status=101))
    with pytest.raises(CliError) as excinfo:
        stack.up(mode="fake")
    assert excinfo.value.code == EXIT_ENV_ERROR
    assert "cargo build" in excinfo.value.message


def test_socket_that_never_appears_times_out_naming_the_log(tmp_path: Path) -> None:
    stack = _stack(tmp_path)
    stack.exists = lambda path: Path(path).exists()
    with pytest.raises(CliError) as excinfo:
        stack.up(mode="fake", skip_build=True, timeout=1.0)
    assert excinfo.value.code == EXIT_ENV_ERROR
    assert "duck-a.sock" in excinfo.value.message
    assert "duck-a.log" in excinfo.value.remediation


def test_up_writes_one_pidfile_per_process_and_params_per_duck(tmp_path: Path) -> None:
    stack = _stack(tmp_path)
    _sockets_appear(stack)
    report = stack.up(mode="sim", ducks=2, skip_build=True)

    state = Path(stack.state_dir)
    assert sorted(p.name for p in state.glob("*.pid")) == [
        "body.pid",
        "duck-a.pid",
        "duck-b.pid",
    ]
    assert (state / "duck-a.pid").read_text().strip() == str(report.processes[1].pid)
    # And a params file per duck, round-tripping, never named robotd.toml.
    assert sorted(p.name for p in state.glob("*.toml")) == ["duck-a.toml", "duck-b.toml"]
    data = tomllib.loads((state / "duck-b.toml").read_text(encoding="utf-8"))
    assert data["policy"]["walk"].endswith("policies/alpha_walking.onnx")


def test_bad_mode_and_bad_duck_count_are_user_errors(tmp_path: Path) -> None:
    stack = _stack(tmp_path)
    for kwargs in ({"mode": "real"}, {"mode": "fake", "ducks": 0}, {"mode": "fake", "ducks": 2}):
        with pytest.raises(CliError) as excinfo:
            stack.up(skip_build=True, **kwargs)
        assert excinfo.value.code == EXIT_USER_ERROR


def test_more_ducks_than_upstream_names_is_a_user_error(tmp_path: Path) -> None:
    """Duck naming comes from duck.addressing, which stops at duck-p."""
    stack = _stack(tmp_path)
    _sockets_appear(stack)
    with pytest.raises(CliError) as excinfo:
        stack.up(mode="sim", ducks=17, skip_build=True)
    assert excinfo.value.code == EXIT_USER_ERROR
    assert "16" in excinfo.value.message


def test_scene_without_sim_is_a_user_error(tmp_path: Path) -> None:
    stack = _stack(tmp_path)
    with pytest.raises(CliError) as excinfo:
        stack.up(mode="fake", scene="apartment", skip_build=True)
    assert excinfo.value.code == EXIT_USER_ERROR


# --- acceptance 1 (second half) + the obligation: TERM only on a match -----


def _write_pidfiles(stack: SimStack, mapping: dict[str, int]) -> None:
    for stem, pid in mapping.items():
        Path(stack.state_dir, f"{stem}.pid").write_text(f"{pid}\n", encoding="utf-8")


def test_down_signals_only_pids_whose_cmdline_matches(tmp_path: Path) -> None:
    table = ProcTable(
        {
            11: "/opt/microduck/target/debug/robotd --sim 127.0.0.1:7801 --socket /st/duck-a.sock",
            12: "/opt/microduck_rl/.venv/bin/python -m mjlab_microduck.sim.body_server "
            "--port 7801",
        }
    )
    stack = _stack(tmp_path, kill=table.kill, proc_cmdline=table.cmdline)
    _write_pidfiles(stack, {"duck-a": 11, "body": 12})

    results = stack.down()

    assert table.sent == [(11, signal.SIGTERM), (12, signal.SIGTERM)], "ducks first, body last"
    assert {r.name: r.outcome for r in results} == {"duck-a": "terminated", "body": "terminated"}
    assert list(Path(stack.state_dir).glob("*.pid")) == []


def test_down_kills_after_the_grace_period_when_term_is_ignored(tmp_path: Path) -> None:
    table = ProcTable({11: "target/debug/robotd --fake"}, ignore_term=(11,))
    stack = _stack(tmp_path, kill=table.kill, proc_cmdline=table.cmdline)
    _write_pidfiles(stack, {"duck-a": 11})

    results = stack.down()

    assert table.sent == [(11, signal.SIGTERM), (11, signal.SIGKILL)]
    assert results[0].outcome == "killed"


def test_down_never_signals_by_name() -> None:
    """No pkill/killall/pgrep path exists: only os.kill(pid, sig) is used.

    Scanned over code tokens only — the module docstring names ``pkill``
    precisely because it explains why the code must never call it.
    """
    import io
    import tokenize

    source = Path("microduck_cli/env/stack.py").read_text(encoding="utf-8")
    names = {
        token.string
        for token in tokenize.generate_tokens(io.StringIO(source).readline)
        if token.type == tokenize.NAME
    }
    for forbidden in ("pkill", "killall", "pgrep", "killpg", "getpgid", "system"):
        assert forbidden not in names, f"stack.py must not reach for {forbidden}"


# --- acceptance 2: a stale pidfile is removed, never signalled -------------


def test_stale_pidfile_is_removed_without_signalling_the_recycled_pid(tmp_path: Path) -> None:
    killer = FakeSignals()
    stack = _stack(
        tmp_path,
        kill=killer,
        # pid 11 was duck-a; it is now somebody's login shell.
        proc_cmdline=lambda pid: "/usr/bin/login -- someone" if pid == 11 else None,
    )
    _write_pidfiles(stack, {"duck-a": 11})

    results = stack.down()

    assert killer.sent == [], "a recycled pid must never be signalled"
    assert results[0].outcome == "stale"
    assert "without signalling" in results[0].detail
    assert not Path(stack.state_dir, "duck-a.pid").exists()


# --- finding 1: re-validate the cmdline marker immediately before EVERY ----
# --- signal (TERM and KILL), not just once up front -------------------------


def test_down_reports_recycled_when_pid_is_reused_between_admission_and_term(
    tmp_path: Path,
) -> None:
    """A pid that still matched when ``down()`` first looked, but was reused
    for an unrelated process by the time ``_terminate`` is about to send
    SIGTERM, must never be signalled — the whole point of re-validating
    immediately before the signal rather than trusting an earlier check.
    """
    calls = {"n": 0}

    def cmdline(pid: int) -> str | None:
        calls["n"] += 1
        # The very first read (down()'s own pre-flight check) still matches;
        # every read after that (the immediate-before-TERM re-check inside
        # _terminate) reports a different process — recycled in between.
        if calls["n"] == 1:
            return "target/debug/robotd --fake"
        return "/usr/bin/login -- someone"

    killer = FakeSignals()
    stack = _stack(tmp_path, kill=killer, proc_cmdline=cmdline)
    _write_pidfiles(stack, {"duck-a": 11})

    results = stack.down()

    assert killer.sent == [], "a pid recycled just before SIGTERM must never be signalled"
    assert results[0].outcome == "recycled"
    assert "not signalled" in results[0].detail
    assert calls["n"] >= 2, "the re-check before SIGTERM must actually re-read /proc"


def test_down_reports_recycled_when_pid_is_reused_before_kill(tmp_path: Path) -> None:
    """A pid that matched through SIGTERM, then got reused for an unrelated
    process while ``down()`` was waiting out the grace period, must never
    receive the follow-up SIGKILL — only the SIGTERM already in flight is
    sent.
    """
    killer = FakeSignals()

    def cmdline(pid: int) -> str | None:
        # Once SIGTERM has actually gone out, every subsequent /proc read
        # reports a different process — the kernel reused the pid during the
        # grace-period wait, before SIGKILL would otherwise be sent.
        if any(sig == signal.SIGTERM for _, sig in killer.sent):
            return "/usr/bin/login -- someone"
        return "target/debug/robotd --fake"

    stack = _stack(tmp_path, kill=killer, proc_cmdline=cmdline)
    _write_pidfiles(stack, {"duck-a": 11})

    results = stack.down()

    assert results[0].outcome == "recycled"
    assert killer.sent == [(11, signal.SIGTERM)], "a recycled pid must never receive SIGKILL"
    assert "not signalled" in results[0].detail


def test_pidfile_for_a_process_that_already_exited_is_reported_gone(tmp_path: Path) -> None:
    killer = FakeSignals()
    stack = _stack(tmp_path, kill=killer, proc_cmdline=lambda pid: None)
    _write_pidfiles(stack, {"duck-a": 11})

    results = stack.down()

    assert killer.sent == []
    assert results[0].outcome == "gone"
    assert not Path(stack.state_dir, "duck-a.pid").exists()


def test_pid_1_and_garbage_are_never_signalled(tmp_path: Path) -> None:
    killer = FakeSignals()
    stack = _stack(tmp_path, kill=killer, proc_cmdline=lambda pid: "/sbin/init")
    Path(stack.state_dir, "duck-a.pid").write_text("1\n", encoding="utf-8")
    Path(stack.state_dir, "duck-b.pid").write_text("not a pid\n", encoding="utf-8")

    results = stack.down()

    assert killer.sent == []
    assert {r.outcome for r in results} == {"unreadable"}


def test_pidfile_is_deleted_before_anything_is_signalled(tmp_path: Path) -> None:
    """A stale file must not be actionable twice, even if the kill explodes."""
    state = tmp_path / "st"

    def exploding_kill(pid: int, sig: int) -> None:
        raise PermissionError("not yours")

    stack = _stack(
        tmp_path,
        kill=exploding_kill,
        proc_cmdline=lambda pid: "target/debug/robotd --fake",
    )
    _write_pidfiles(stack, {"duck-a": 11})

    with pytest.raises(PermissionError):
        stack.down()

    assert not (state / "duck-a.pid").exists()


# --- status ----------------------------------------------------------------


def test_status_reports_alive_stale_and_sockets(tmp_path: Path) -> None:
    cmdlines = {11: "target/debug/robotd --fake", 12: "/usr/bin/login"}
    stack = _stack(tmp_path, proc_cmdline=lambda pid: cmdlines.get(pid))
    _write_pidfiles(stack, {"duck-a": 11, "duck-b": 12})
    Path(stack.state_dir, "duck-a.sock").write_text("", encoding="utf-8")

    report = stack.status()

    by_name = {p["name"]: p for p in report["processes"]}
    assert by_name["duck-a"]["alive"] is True
    assert by_name["duck-b"]["alive"] is False
    assert by_name["duck-b"]["stale"] is True
    assert report["sockets"] == [f"{stack.state_dir}/duck-a.sock"]


# --- marker table ----------------------------------------------------------


@pytest.mark.parametrize(
    "stem,marker",
    [
        ("duck-a", "robotd"),
        ("duck-p", "robotd"),
        ("body", "mjlab_microduck.sim.body_server"),
        ("ether", "duck-ether"),
        ("duck-a-tof", "tofd"),
        ("duck-a-media", "mediad"),
    ],
)
def test_expected_marker(stem: str, marker: str) -> None:
    assert expected_marker(stem) == marker
