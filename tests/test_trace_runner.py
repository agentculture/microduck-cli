"""Tests for microduck_cli/trace/runner.py — real subprocesses, tiny commands."""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from microduck_cli.trace import runner as runner_mod
from microduck_cli.trace.capture import ShotOutcome
from microduck_cli.trace.events import RunDir, load_events
from microduck_cli.trace.plan import Plan, Step
from microduck_cli.trace.runner import (
    Recorder,
    default_state_dir,
    exec_one,
    lane_for_command,
    run_plan,
)

RUNNER_SRC = Path(runner_mod.__file__)


@pytest.fixture
def run_dir(tmp_path: Path) -> RunDir:
    rd = RunDir(tmp_path / "run")
    rd.ensure()
    rd.write_meta({"t0_wall": 1000.0})
    return rd


def _events(rd: RunDir, kind: str | None = None, lane: str | None = None):
    out = load_events(rd)
    if kind:
        out = [e for e in out if e.kind == kind]
    if lane:
        out = [e for e in out if e.lane == lane]
    return out


def test_run_executes_plan_line_verbatim(run_dir: RunDir) -> None:
    rec = Recorder(run_dir)
    cmd = "printf 'hello\\nworld\\n'"
    result = rec.run("1", "cli", cmd, cmd)
    assert result.rc == 0
    assert Path(result.out_path).read_text() == "hello\nworld\n"
    assert result.stdout_first == "hello"
    starts = _events(run_dir, "cmd-start")
    ends = _events(run_dir, "cmd-end")
    assert [e.label for e in starts] == [cmd]
    assert ends[0].label == cmd
    assert ends[0].extra["rc"] == 0
    assert ends[0].extra["stdout_lines"] == 2
    assert ends[0].extra["stderr_lines"] == 0
    assert ends[0].extra["out"].startswith("steps/01-")
    assert ends[0].step == "1"


def test_runner_never_injects_apply() -> None:
    assert "--apply" not in RUNNER_SRC.read_text(encoding="utf-8")


def test_stderr_lines_are_timestamped_and_laned(run_dir: RunDir) -> None:
    rec = Recorder(run_dir)
    cmd = (
        "echo plain >&2; sleep 0.01; "
        "echo '[SENSE stage=rule source=verify-look event=fired] look -> look-1' >&2"
    )
    rec.run("9", "cli", "engine-ish", cmd)
    errs = _events(run_dir, "stderr")
    assert [e.lane for e in errs] == ["cli", "engine"]
    assert errs[0].t <= errs[1].t
    assert errs[1].extra["ev"] == "fired"
    assert errs[1].extra["stage"] == "rule"
    ends = _events(run_dir, "cmd-end")
    assert ends[0].extra["stderr_lines"] == 2
    assert (
        Path(run_dir.path / ends[0].extra["out"]).with_suffix(".err").read_text().count("\n") == 2
    )


def test_run_plan_retry_records_both_attempts(run_dir: RunDir, tmp_path: Path) -> None:
    flag = tmp_path / "flag"
    plan = Plan(
        title="retry",
        steps=[
            Step(
                step="6",
                label="flaky",
                cmd=f"test -f {flag} || {{ touch {flag}; exit 2; }}",
                retry=1,
            )
        ],
    )
    result = run_plan(plan, run_dir, shot=None, state_dir=str(tmp_path / "state"))
    ends = _events(run_dir, "cmd-end")
    assert [e.extra["rc"] for e in ends] == [2, 0]
    assert len(_events(run_dir, "cmd-start")) == 2
    assert result.retried == 1
    assert result.failures == 0
    assert result.steps_run == 1
    assert (run_dir.path / "plan.toml").exists()
    assert "flaky" in (run_dir.path / "plan.toml").read_text()


def test_run_plan_counts_final_failures(run_dir: RunDir, tmp_path: Path) -> None:
    plan = Plan(title="fail", steps=[Step(step="1", label="boom", cmd="exit 3")])
    result = run_plan(plan, run_dir, shot=None, state_dir=str(tmp_path / "s"))
    assert result.failures == 1
    assert _events(run_dir, "cmd-end")[0].extra["rc"] == 3


def test_timeout_kills_and_records(run_dir: RunDir) -> None:
    rec = Recorder(run_dir)
    started = time.monotonic()
    result = rec.run("x", "cli", "slow", "sleep 5", timeout_s=0.3)
    assert time.monotonic() - started < 3
    assert result.rc == -9
    kinds = [e.kind for e in load_events(run_dir)]
    assert "timeout" not in kinds  # timeout is not a KINDS member: it is a note
    notes = _events(run_dir, "note")
    assert any("timeout" in n.label for n in notes)
    assert _events(run_dir, "cmd-end")[0].extra["rc"] == -9


def test_log_tail_strips_ansi_and_robotd_prefix(run_dir: RunDir, tmp_path: Path) -> None:
    duck = tmp_path / "duck-a.log"
    body = tmp_path / "body.log"
    duck.write_text("old line that must not be picked up\n")
    rec = Recorder(run_dir)
    rec.start_log_tail({str(duck): "robotd", str(body): "body"})
    time.sleep(0.15)
    with duck.open("a") as fh:
        fh.write(
            "\x1b[2m2026-09-08T14:21:41.260362Z\x1b[0m \x1b[32m INFO\x1b[0m "
            "duck_control::sim: simulated body addr=127.0.0.1:7801 protocol=1\n"
        )
    with body.open("a") as fh:
        fh.write("== duck 0: daemon connected from ('127.0.0.1', 56268)\n")
    time.sleep(0.3)
    rec.stop_log_tail()
    logs = _events(run_dir, "log")
    labels = {e.lane: e.label for e in logs}
    assert labels["robotd"] == ("duck_control::sim: simulated body addr=127.0.0.1:7801 protocol=1")
    assert labels["body"] == "== duck 0: daemon connected from ('127.0.0.1', 56268)"
    assert all("old line" not in e.label for e in logs)
    assert all("\x1b" not in e.label for e in logs)


def test_shot_records_outcome_or_reason(run_dir: RunDir) -> None:
    rec = Recorder(run_dir)

    def ok_shot(rd, name, *, env, runner=None):
        (rd.shots / f"{name}.png").write_bytes(b"x")
        return ShotOutcome(ok=True, file=f"shots/{name}.png", window=True, size=[1, 1])

    def bad_shot(rd, name, *, env, runner=None):
        return ShotOutcome(ok=False, reason="headless")

    rec.shot("7", "standing", "s1", shot=ok_shot)
    rec.shot("7", "standing again", "s2", shot=bad_shot)
    shots = _events(run_dir, "shot")
    assert len(shots) == 1 and shots[0].extra["file"] == "shots/s1.png"
    assert shots[0].extra["window"] is True
    notes = _events(run_dir, "note", "viewer")
    assert notes and "headless" in notes[0].label


def test_exec_one_creates_meta_when_absent(tmp_path: Path) -> None:
    rd = RunDir(tmp_path / "fresh")
    result = exec_one(["printf", "a b"], rd, clock=lambda: 42.0)
    assert result.rc == 0
    assert rd.read_meta()["t0_wall"] == 42.0
    ends = _events(rd, "cmd-end")
    assert ends[0].label == "printf 'a b'"
    assert ends[0].step == "exec"
    assert Path(result.out_path).read_text() == "a b"


def test_run_uses_injected_clock(run_dir: RunDir) -> None:
    ticks = iter([1000.5, 1001.0, 1001.25])
    rec = Recorder(run_dir, clock=lambda: next(ticks, 1002.0))
    rec.note("operator", "hello")
    ev = load_events(run_dir)[0]
    assert ev.t == 0.5 and ev.wall == 1000.5


def test_default_state_dir() -> None:
    assert default_state_dir({"DUCK_SIM_STATE": "/x/y"}) == "/x/y"
    assert default_state_dir({"HOME": "/home/u"}).endswith("/.cache/duck-sim")


def test_lane_for_command() -> None:
    assert lane_for_command("free -g") == "operator"
    assert lane_for_command("microduck duck health") == "cli"


def test_run_plan_sleeps_and_shots(run_dir: RunDir, tmp_path: Path) -> None:
    slept: list[float] = []
    taken: list[str] = []

    def fake_shot(rd, name, *, env, runner=None):
        taken.append(name)
        return ShotOutcome(ok=True, file=f"shots/{name}.png", window=True, size=[1, 1])

    plan = Plan(
        title="s",
        steps=[
            Step(step="7", label="l", cmd="true", sleep_before=0.5, sleep_after=1.5, shot="after"),
            Step(step="7", label="n", cmd="true", note="a note"),
        ],
    )
    rec = Recorder(run_dir, sleep=slept.append)
    run_plan(plan, run_dir, recorder=rec, shot=fake_shot, state_dir=str(tmp_path / "s"))
    assert slept == [0.5, 1.5]
    assert taken == ["after"]
    notes = [e.label for e in _events(run_dir, "note")]
    assert "a note" in notes
    assert json.loads(run_dir.meta_path.read_text())["t0_wall"] == 1000.0
