"""Live test: operate the CLI end to end against the REAL robotd (``--fake`` body).

This is the delivery obligation o23 made repeatable: every verb below is run the
way an operator runs it — as a subprocess of the console entry point — against
the daemon built from the pinned ``sim-remote-io`` commit (``docs/upstream-pins.md``),
not against ``tests/fake_robotd.py``. It is **opt-in**: the module is skipped
unless ``MICRODUCK_LIVE=1`` is set and a built clone resolves (``MICRODUCK_CLONE``
or ``../microduck`` beside this repo), so the default ``uv run pytest -n auto``
never starts a daemon. Run it serially::

    MICRODUCK_LIVE=1 uv run pytest -m live -n0 -q

``MICRODUCK_LIVE_BODY=sim`` runs the whole module against the MuJoCo body instead
of ``--fake`` (needs a synced ``microduck_rl`` venv; ``--headless``), and
``MICRODUCK_LIVE_SIM=1`` adds the sim-only checks (stand-up, walking); see
``docs/verification/2026-09-04-sim-bringup.md``.

What it asserts is the operator-visible contract: exit codes, the JSON payloads,
the six start steps, the cadence, the refusal texts — never the daemon's
internals. A daemon that answers differently after a re-pin fails here first.
"""

from __future__ import annotations

import json
import os
import pathlib
import subprocess
import sys
import tempfile
import time

import pytest

from microduck_cli.env.doctor import resolve_clone_paths

pytestmark = pytest.mark.live

LIVE = os.environ.get("MICRODUCK_LIVE") == "1"
LIVE_SIM = os.environ.get("MICRODUCK_LIVE_SIM") == "1"


def _clone_ready() -> bool:
    clone, _ = resolve_clone_paths(os.environ)
    if clone is None:
        return False
    return (pathlib.Path(clone) / "target" / "debug" / "robotd").is_file()


skip_unless_live = pytest.mark.skipif(
    not (LIVE and _clone_ready()),
    reason="set MICRODUCK_LIVE=1 with a built microduck clone (MICRODUCK_CLONE) to run",
)


def _cli(*args: str, env: dict[str, str], timeout: float = 90.0) -> subprocess.CompletedProcess:
    argv = [sys.executable, "-m", "microduck_cli", *args]
    return subprocess.run(  # nosec B603 - fixed argv, never shell=True
        argv, env=env, capture_output=True, text=True, timeout=timeout, check=False
    )


def _json(proc: subprocess.CompletedProcess) -> dict:
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout)


@pytest.fixture(scope="module")
def duck_env():
    """A short state dir and the environment every CLI call runs with."""
    state = tempfile.mkdtemp(prefix="mdlive", dir="/tmp")
    env = dict(os.environ)
    env["DUCK_SIM_STATE"] = state
    env.pop("DUCK_SIM_DUCK", None)
    yield env
    # Belt and braces: the test module tears the stack down itself.
    subprocess.run(  # nosec B603
        [sys.executable, "-m", "microduck_cli", "env", "down", "--json"],
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )


BODY = os.environ.get("MICRODUCK_LIVE_BODY", "fake")


@pytest.fixture(scope="module")
def fake_duck(duck_env):
    """``env up`` (``--fake`` or, with MICRODUCK_LIVE_BODY=sim, the MuJoCo body)."""
    headless = os.environ.get("MICRODUCK_LIVE_HEADLESS", "1") == "1"
    body = (["--sim", "--headless"] if headless else ["--sim"]) if BODY == "sim" else ["--fake"]
    up = _cli("env", "up", *body, "--skip-build", "--json", env=duck_env, timeout=180)
    payload = _json(up)
    assert payload["healthy"] is True, up.stderr
    assert payload["sockets"], up.stderr
    yield payload
    down = _cli("env", "down", "--json", env=duck_env, timeout=60)
    assert down.returncode == 0, down.stderr


@skip_unless_live
def test_env_doctor_is_healthy_on_this_box(duck_env):
    proc = _cli("env", "doctor", "--json", env=duck_env)
    report = _json(proc)
    assert report["healthy"] is True, [c for c in report["checks"] if not c["passed"]]


@skip_unless_live
def test_version_is_the_pinned_daemon(fake_duck, duck_env):
    payload = _json(_cli("duck", "version", "--json", env=duck_env))
    assert payload["api_version"] == 16
    assert payload["daemon_version"].startswith("0.10.")


@skip_unless_live
def test_health_is_healthy_at_50_hz(fake_duck, duck_env):
    time.sleep(1.5)  # the daemon reports achieved_hz only after its first second
    payload = _json(_cli("duck", "health", "--json", env=duck_env))
    assert payload["healthy"] is True
    loop = payload["health"]["control_loop"]
    assert loop["target_hz"] == 50.0
    assert loop["missed"] == 0
    assert loop["achieved_hz"] is None or abs(loop["achieved_hz"] - 50.0) < 1.0


@skip_unless_live
def test_init_dry_runs_on_a_pipe_then_applies(fake_duck, duck_env):
    dry = _cli("duck", "init", env=duck_env)
    assert dry.returncode == 0
    assert "Dry-run plan: init" in dry.stdout
    assert "robot.init" in dry.stdout
    applied = _json(_cli("duck", "init", "--apply", "--json", env=duck_env))
    assert applied["sent"] is True
    assert applied["result"]["accepted"] is True


@skip_unless_live
def test_enable_then_a_skill_is_accepted(fake_duck, duck_env):
    enabled = _json(_cli("duck", "enable", "--apply", "--json", env=duck_env))
    assert enabled["result"]["accepted"] is True
    done = _json(_cli("duck", "do", "roulade", "--apply", "--json", env=duck_env))
    assert done["sent"] is True
    assert done["result"]["accepted"] is True


@skip_unless_live
def test_move_refreshes_the_deadman_then_stops(fake_duck, duck_env):
    payload = _json(
        _cli(
            "duck",
            "move",
            "--vx",
            "0.1",
            "--vy",
            "0",
            "--vyaw",
            "0",
            "--duration",
            "0.5",
            "--apply",
            "--json",
            env=duck_env,
        )
    )
    assert payload["sent"] is True
    assert any("robot.move" in call for call in payload["calls"])
    assert any("robot.stop" in call for call in payload["calls"])


@skip_unless_live
def test_rules_check_reads_skills_from_subscribe_on_api16(fake_duck, duck_env):
    proc = _cli("rules", "check", "--duck", "duck-a", "--json", env=duck_env)
    payload = _json(proc)
    assert payload["ok"] is True, payload
    assert payload["issues"] == []
    assert payload["problems"] == []
    assert "subscribe" in payload["skills_source"]  # API 16: skills come from robot.subscribe


@skip_unless_live
def test_an_over_limit_intent_is_refused_verbatim_with_no_engine(fake_duck, duck_env):
    proc = _cli(
        "rules", "intent", "move", "--payload", '{"vx": 0.1, "vy": 0, "vyaw": 9.0}', env=duck_env
    )
    assert proc.returncode == 1
    assert "move.vyaw out of range: 9.0" in proc.stderr


@skip_unless_live
def test_engine_run_starts_in_order_and_holds_50_hz(fake_duck, duck_env):
    proc = _cli(
        "rules",
        "engine",
        "run",
        "--duck",
        "duck-a",
        "--apply",
        "--max-ticks",
        "150",
        "--json",
        env=duck_env,
        timeout=120,
    )
    payload = _json(proc)
    steps = [s["step"] if isinstance(s, dict) else s for s in payload["steps"]]
    assert steps == ["connect", "hello", "health", "init", "enable", "armed"], proc.stderr
    assert payload["ticks"] == 150
    metrics = payload["metrics"]
    assert abs(metrics["achieved_hz"] - 50.0) < 2.0, metrics
    assert metrics["overruns"] == 0
    for step in steps:
        assert f"event={step}]" in proc.stderr


@skip_unless_live
def test_record_writes_pure_jsonl(fake_duck, duck_env):
    proc = _cli("duck", "record", "--seconds", "0.6", env=duck_env)
    assert proc.returncode == 0, proc.stderr
    lines = [line for line in proc.stdout.splitlines() if line.strip()]
    records = [json.loads(line) for line in lines]
    assert all(set(r) == {"ts", "source", "params"} for r in records)
    sources = {r["source"] for r in records}
    assert {"hello", "state", "health"} <= sources


@skip_unless_live
def test_env_status_and_down_leave_nothing_tracked(fake_duck, duck_env):
    status = _json(_cli("env", "status", "--json", env=duck_env))
    assert status["processes"]
    assert all(p["alive"] for p in status["processes"])
    assert all(s["responding"] for s in status["socket_health"])


@pytest.mark.skipif(
    not (LIVE_SIM and LIVE and _clone_ready()),
    reason="set MICRODUCK_LIVE_SIM=1 (needs a synced microduck_rl venv) to run the MuJoCo body",
)
def test_sim_body_stands_the_duck_up(duck_env):
    state = tempfile.mkdtemp(prefix="mdsim", dir="/tmp")
    env = dict(duck_env)
    env["DUCK_SIM_STATE"] = state
    try:
        up = _json(
            _cli("env", "up", "--sim", "--headless", "--skip-build", "--json", env=env, timeout=180)
        )
        assert up["healthy"] is True
        assert _json(_cli("duck", "init", "--apply", "--json", env=env))["result"]["accepted"]
        time.sleep(8)
        assert _json(_cli("duck", "enable", "--apply", "--json", env=env))["result"]["accepted"]
        time.sleep(2)
        frames = _cli("duck", "monitor", "--frames", "2", "--json", env=env)
        last = json.loads(frames.stdout.splitlines()[-1])
        params = last.get("params", last)
        assert params["safety"]["fallen"] is False
        assert params["odom"]["position"][2] > 0.10, params["odom"]
    finally:
        _cli("env", "down", "--json", env=env, timeout=60)


@pytest.mark.skipif(
    not (LIVE_SIM and LIVE and _clone_ready() and BODY == "sim"),
    reason="MICRODUCK_LIVE_BODY=sim MICRODUCK_LIVE_SIM=1: the duck walks under the walk policy",
)
@pytest.mark.xfail(
    strict=False,
    reason="at the pinned commits (docs/upstream-pins.md) the walk policy engages (policy=walk, "
    "twist applied) but outputs a static pose in MuJoCo, so odometry does not advance; upstream's "
    "own scripts/duck-sim drive behaves the same, and its launcher does not start at this pin pair "
    "(body_server lacks --cameras). An XPASS here means a re-pin fixed walking — promote it.",
)
def test_sim_body_walks_forward_on_move(fake_duck, duck_env):
    def frame():
        frames = _cli("duck", "monitor", "--frames", "2", "--json", env=duck_env)
        last = json.loads(frames.stdout.splitlines()[-1])
        return last.get("params", last)

    # Bring the duck up ONCE: init re-homes a driving duck, so only init when it is held.
    if frame()["policy"] == "held":
        assert _json(_cli("duck", "init", "--apply", "--json", env=duck_env))["result"]["accepted"]
        time.sleep(8)
        assert _json(_cli("duck", "enable", "--apply", "--json", env=duck_env))["result"][
            "accepted"
        ]
        time.sleep(3)

    def odom():
        params = frame()
        return params["odom"]["position"], params["safety"]["fallen"]

    before, fallen = odom()
    assert fallen is False
    moved = _json(
        _cli(
            "duck",
            "move",
            "--vx",
            "0.15",
            "--vy",
            "0",
            "--vyaw",
            "0",
            "--duration",
            "4",
            "--apply",
            "--json",
            env=duck_env,
            timeout=60,
        )
    )
    assert moved["sent"] is True
    after, fallen_after = odom()
    assert fallen_after is False, after
    dx = after[0] - before[0]
    assert dx > 0.05, {"before": before, "after": after}
