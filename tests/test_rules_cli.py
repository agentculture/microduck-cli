"""The ``rules`` noun (t21): list, check, engine run/start/stop/status, intent.

Acceptance criteria exercised here, in order:

1. ``rules check`` on a malformed file exits 0 with the content issue NAMED by
   rule id; against a fake reporting skills ``[a, b]`` — on API 16 through
   ``robot.subscribe`` AND on API 18 through ``robot.policies`` (approved
   deviation d1) — a rule naming skill ``c`` is reported as
   ``rule '<id>': c not in [a, b]``; ``--replay`` over a fixture JSONL prints the
   inhibited actions once ``fallen`` reads true.
2. ``rules engine run --apply --max-ticks 50`` against the fake logs the six
   start steps IN ORDER on stderr, the fake's call log shows ``robot.init`` then
   ``robot.enable {"on": true}`` then ``robot.subscribe``, a rule firing a skill
   the daemon refuses is a NAMED drop with no retry, a non-TTY run without
   ``--apply`` prints the dry-run and sends nothing at all, and a fresh heartbeat
   makes a second run exit 1 naming ``engine live``.
3. ``rules intent`` with an over-limit payload and no engine live prints exactly
   the refusal text ``registry.inject`` produces (the expectation shared with
   ``tests/test_intents.py``) and exits 1; with a live engine an intent appended
   to the spool is admitted and acknowledged in ``intents.log`` within 2 s.
4. A ``KeyboardInterrupt`` raised from a tick driver yields the four release
   sends, never ``robot.relax``, and exits non-zero.

Every socket in this file is the in-process :class:`tests.fake_robotd.FakeRobotd`;
every clock is a fake, injected through the seams on
``microduck_cli.cli._commands.rules`` so a run terminates deterministically.
"""

from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path
from typing import Iterator

import pytest

from microduck_cli.behavior import compose, liveness
from microduck_cli.behavior.intents import default_registry
from microduck_cli.cli import main
from microduck_cli.cli._commands import rules as rules_cmd
from microduck_cli.cli._errors import EXIT_ENV_ERROR, EXIT_SUCCESS, EXIT_USER_ERROR
from microduck_cli.ipc import proto
from microduck_cli.ipc.client import RobotClient
from tests.fake_robotd import FakeRobotd

# --------------------------------------------------------------------------- #
# Fixtures and helpers                                                        #
# --------------------------------------------------------------------------- #


class FakeClock:
    """A deterministic monotonic clock: every read advances by a fixed step."""

    def __init__(self, step: float = 0.0005, start: float = 1000.0) -> None:
        self.t = start
        self.step = step

    def __call__(self) -> float:
        value = self.t
        self.t += self.step
        return value


@pytest.fixture()
def fake() -> Iterator[FakeRobotd]:
    server = FakeRobotd()
    try:
        yield server
    finally:
        server.close()


@pytest.fixture()
def seams(monkeypatch: pytest.MonkeyPatch) -> FakeClock:
    """Inject a fake clock and a no-op sleep into the rules command module."""
    clock = FakeClock()
    monkeypatch.setattr(rules_cmd, "_clock", clock)
    monkeypatch.setattr(rules_cmd, "_sleep", lambda _s: None)
    return clock


@pytest.fixture()
def client_factory(monkeypatch: pytest.MonkeyPatch, seams: FakeClock):
    """Point the CLI's client factory at real clients over the fake's socket."""
    made: list[RobotClient] = []

    def factory(socket_path: str) -> RobotClient:
        client = RobotClient(socket_path, clock=time.monotonic, request_timeout_s=2.0)
        made.append(client)
        return client.connect(verify_joints=False)

    monkeypatch.setattr(rules_cmd, "_client_factory", factory)
    return made


def _state(tmp_path: Path, name: str = "state") -> str:
    path = tmp_path / name
    path.mkdir(parents=True, exist_ok=True)
    return str(path)


def _write(path: Path, text: str) -> str:
    path.write_text(text, encoding="utf-8")
    return str(path)


#: A rules file naming a skill the daemon does not have.
_SKILL_RULE = """\
schema_version = 1

[[react]]
id = "peck-on-close"
when = { field = "tof_nearest_m", op = "lt", value = 0.2 }
run = "do"
params = { skill = "c" }
"""

#: A rules file that is well-formed TOML but breaks the schema, on a named rule.
_MALFORMED_RULE = """\
schema_version = 1

[[react]]
id = "bad-cooldown"
when = { field = "fallen", op = "is_true" }
run = "stop"
cooldown_s = -3.0
"""

#: A react rule that fires a canned skill from the very first tick. ``absent_for
#: 0`` is the one predicate that holds with no reading at all — the engine seeds
#: every field's last-seen stamp at its first tick — so it fires before the
#: daemon has streamed a single ``robot.state`` frame.
_SKILL_FROM_TICK_ZERO = """\
schema_version = 1

[[react]]
id = "always-do"
when = { field = "fallen", op = "absent_for", value = 0.0 }
run = "do"
params = { skill = "kick_left" }
cooldown_s = 300.0
"""


def _replay_fixture(path: Path) -> str:
    """A recorded sense stream: upright, then fallen (which inhibits six actions)."""
    lines = [
        {"ts": 0.0, "source": "state", "params": {"safety": {"fallen": False, "limp": False}}},
        {"ts": 0.5, "source": "state", "params": {"safety": {"fallen": True, "limp": False}}},
        {"ts": 1.0, "source": "state", "params": {"safety": {"fallen": True, "limp": True}}},
    ]
    return _write(path, "\n".join(json.dumps(line) for line in lines) + "\n")


def _sense_lines(err: str, stage: str = compose.STAGE) -> list[str]:
    marker = f"[SENSE stage={stage} "
    return [line for line in err.splitlines() if marker in line]


def _order(methods: list[str], wanted: list[str]) -> bool:
    """Do *wanted* appear in *methods* in that relative order?"""
    index = 0
    for method in methods:
        if index < len(wanted) and method == wanted[index]:
            index += 1
    return index == len(wanted)


# --------------------------------------------------------------------------- #
# rules list                                                                  #
# --------------------------------------------------------------------------- #


def test_list_renders_the_shipped_defaults_with_their_origin(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["rules", "list", "--state", _state(tmp_path)]) == EXIT_SUCCESS
    out = capsys.readouterr().out
    assert "stop-when-limp" in out
    assert "origin shipped" in out


def test_list_json_reports_overlay_and_tombstoned_origins(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    overlay = _write(
        tmp_path / "overlay.toml",
        """\
schema_version = 1

[[react]]
id = "stop-when-limp"
enabled = false

[[react]]
id = "mine"
when = { field = "fallen", op = "is_true" }
run = "sound"
params = { name = "alarm" }
""",
    )
    assert main(["rules", "list", "--rules", overlay, "--json"]) == EXIT_SUCCESS
    payload = json.loads(capsys.readouterr().out)
    origins = {row["id"]: row["origin"] for row in payload["rules"]}
    assert origins["mine"] == "overlay"
    assert origins["fallen-inhibit"] == "shipped"
    assert origins["stop-when-limp"] == "tombstoned"


# --------------------------------------------------------------------------- #
# 1. rules check                                                              #
# --------------------------------------------------------------------------- #


def test_check_on_a_malformed_file_exits_zero_naming_the_rule_id(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A descriptive verb never hard-fails on content — it names the rule."""
    overlay = _write(tmp_path / "bad.toml", _MALFORMED_RULE)
    assert main(["rules", "check", "--rules", overlay, "--json"]) == EXIT_SUCCESS
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is False
    assert payload["issues"]
    assert "bad-cooldown" in payload["issues"][0]
    assert "cooldown_s" in payload["issues"][0]


def test_check_text_mode_also_exits_zero_and_prints_the_issue(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    overlay = _write(tmp_path / "bad.toml", _MALFORMED_RULE)
    assert main(["rules", "check", "--rules", overlay]) == EXIT_SUCCESS
    assert "bad-cooldown" in capsys.readouterr().out


def test_check_without_a_snapshot_or_a_duck_skips_and_says_so(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["rules", "check", "--state", _state(tmp_path), "--json"]) == EXIT_SUCCESS
    captured = capsys.readouterr()
    assert json.loads(captured.out)["skills_source"] == "skipped"
    assert "not checked" in captured.err


@pytest.mark.parametrize("api_version", [16, 18])
def test_check_refuses_a_skill_the_live_duck_does_not_have(
    api_version: int,
    fake: FakeRobotd,
    client_factory,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """d1: skills come from robot.subscribe on API 16 and robot.policies on 18."""
    fake.set_state(api_version=api_version, skills=("a", "b"))
    overlay = _write(tmp_path / "skills.toml", _SKILL_RULE)
    rc = main(
        ["rules", "check", "--socket", fake.socket_path, "--rules", overlay, "--json"],
    )
    assert rc == EXIT_SUCCESS
    payload = json.loads(capsys.readouterr().out)
    assert payload["skills"] == ["a", "b"]
    assert payload["problems"] == ["rule 'peck-on-close': c not in [a, b]"]
    expected = "robot.policies" if api_version >= 18 else "robot.subscribe"
    assert payload["skills_source"].startswith(expected)


def test_check_uses_a_skills_snapshot_file_when_given(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    snapshot = _write(
        tmp_path / "skills.json",
        json.dumps({"skills": ["a", "b"], "slots": {}, "api_version": 16}),
    )
    overlay = _write(tmp_path / "skills.toml", _SKILL_RULE)
    rc = main(["rules", "check", "--rules", overlay, "--skills", snapshot, "--json"])
    assert rc == EXIT_SUCCESS
    payload = json.loads(capsys.readouterr().out)
    assert payload["problems"] == ["rule 'peck-on-close': c not in [a, b]"]


def test_check_replay_reports_the_actions_inhibited_after_a_fall(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    record = _replay_fixture(tmp_path / "session.jsonl")
    assert main(["rules", "check", "--replay", record]) == EXIT_SUCCESS
    out = capsys.readouterr().out
    assert "## replay" in out
    for action in ("do", "idle", "look", "mode", "move", "sound"):
        assert action in out.split("inhibited actions:")[1]


def test_check_replay_json_carries_every_tick(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    record = _replay_fixture(tmp_path / "session.jsonl")
    assert main(["rules", "check", "--replay", record, "--json"]) == EXIT_SUCCESS
    replay = json.loads(capsys.readouterr().out)["replay"]
    assert len(replay["ticks"]) == 3
    assert replay["ticks"][0]["inhibited"] == {}
    assert replay["ticks"][1]["inhibited"]["move"] == "fallen-inhibit"


def test_check_refuses_an_unreadable_replay_file(capsys: pytest.CaptureFixture[str]) -> None:
    """A broken INVOCATION is still an error — only content is reported softly."""
    assert main(["rules", "check", "--replay", "/no/such/file.jsonl"]) == EXIT_USER_ERROR
    err = capsys.readouterr().err
    assert err.startswith("error:")
    assert "hint:" in err


# --------------------------------------------------------------------------- #
# 2. rules engine run                                                         #
# --------------------------------------------------------------------------- #


def _run_argv(fake: FakeRobotd, state_dir: str, overlay: str | None = None) -> list[str]:
    argv = [
        "rules",
        "engine",
        "run",
        "--socket",
        fake.socket_path,
        "--state",
        state_dir,
        "--max-ticks",
        "50",
        "--json",
    ]
    if overlay:
        argv += ["--rules", overlay]
    return argv


def test_engine_run_logs_the_six_start_steps_in_order(
    fake: FakeRobotd, client_factory, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The obligation: connect, hello, health, init, enable, armed — in order."""
    state_dir = _state(tmp_path)
    rc = main([*_run_argv(fake, state_dir), "--apply"])
    captured = capsys.readouterr()
    assert rc == EXIT_SUCCESS, captured.err

    steps = compose.steps_in_order(_sense_lines(captured.err))
    assert steps == list(compose.START_STEPS)
    # The result is on stdout, the sense log on stderr, never mixed.
    assert not _sense_lines(captured.out)
    payload = json.loads(captured.out)
    assert [step["step"] for step in payload["steps"]] == list(compose.START_STEPS)
    assert payload["ticks"] == 50


def test_engine_run_sends_init_then_enable_then_subscribe(
    fake: FakeRobotd, client_factory, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    rc = main([*_run_argv(fake, _state(tmp_path)), "--apply"])
    assert rc == EXIT_SUCCESS, capsys.readouterr().err
    methods = fake.methods_called()
    assert _order(methods, [proto.ROBOT_INIT, proto.ROBOT_ENABLE, proto.ROBOT_SUBSCRIBE])
    enable = next(rec for rec in fake.call_log if rec.method == proto.ROBOT_ENABLE)
    assert enable.params == {"on": True}
    # robot.relax is never part of driving a duck.
    assert proto.ROBOT_RELAX not in methods


def test_a_refused_skill_is_a_named_drop_with_no_retry(
    fake: FakeRobotd, client_factory, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A rule fires ``do`` from tick 0 against a daemon that refuses ``robot.do``.

    The refusal must be ONE named drop, not fifty: the sink edge-triggers a
    discrete channel, so a skill whose value has not changed is never re-sent.
    """
    overlay = _write(tmp_path / "do.toml", _SKILL_FROM_TICK_ZERO)
    fake.refuse(proto.ROBOT_DO, message="no policy configured for that skill")
    rc = main([*_run_argv(fake, _state(tmp_path), overlay), "--apply"])
    captured = capsys.readouterr()
    assert rc == EXIT_SUCCESS, captured.err

    payload = json.loads(captured.out)
    assert payload["sink_drops"].get("sink-request-refused") == 1
    assert "event=sink-request-refused" in captured.err
    assert fake.methods_called().count(proto.ROBOT_DO) == 1


def test_a_non_tty_run_without_apply_prints_the_plan_and_sends_nothing(
    fake: FakeRobotd, client_factory, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    rc = main(_run_argv(fake, _state(tmp_path)))
    assert rc == EXIT_SUCCESS
    payload = json.loads(capsys.readouterr().out)
    assert payload["mode"] == "dry_run"
    assert len(payload["plan"]["calls"]) == len(compose.START_STEPS)
    assert "--apply" in payload["plan"]["apply_command"]
    assert "No sockets opened" in payload["text"]
    # Nothing was sent: not even a hello.
    assert fake.methods_called() == []


def test_a_fresh_heartbeat_refuses_a_second_engine(
    fake: FakeRobotd,
    client_factory,
    seams: FakeClock,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    state_dir = _state(tmp_path)
    liveness.Heartbeat(liveness.state_path(state_dir), pid=os.getpid(), clock=seams).beat(tick=7)
    rc = main([*_run_argv(fake, state_dir), "--apply"])
    captured = capsys.readouterr()
    assert rc == EXIT_USER_ERROR
    error = json.loads(captured.err)
    assert "engine live" in error["message"]
    assert error["remediation"]
    # Refused BEFORE the client was constructed: nothing reached the duck.
    assert fake.methods_called() == []


def test_engine_run_refuses_an_unhealthy_duck(
    fake: FakeRobotd, client_factory, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    fake.set_state(healthy=False, reason="the bus is not answering")
    rc = main([*_run_argv(fake, _state(tmp_path)), "--apply"])
    captured = capsys.readouterr()
    assert rc == EXIT_ENV_ERROR
    assert "not healthy" in captured.err
    assert "the bus is not answering" in captured.err
    # It stopped at health: no motion call was ever made.
    assert proto.ROBOT_INIT not in fake.methods_called()


# --------------------------------------------------------------------------- #
# 4. Ctrl-C releases the duck                                                 #
# --------------------------------------------------------------------------- #


def test_ctrl_c_from_a_tick_driver_releases_and_exits_non_zero(
    fake: FakeRobotd,
    client_factory,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    real_build = compose.build_runtime

    def build_with_interrupt(*args, **kwargs):
        runtime = real_build(*args, **kwargs)

        def interrupt(ctx) -> None:
            if ctx.tick >= 10:
                raise KeyboardInterrupt

        interrupt.name = "test-interrupt"
        runtime.bus.add_driver(interrupt)
        return runtime

    monkeypatch.setattr(compose, "build_runtime", build_with_interrupt)
    rc = main([*_run_argv(fake, _state(tmp_path)), "--apply"])
    captured = capsys.readouterr()

    released = [proto.ROBOT_STOP, proto.ROBOT_POSE, proto.ROBOT_MOUTH, proto.ROBOT_SOUND]
    deadline = time.monotonic() + 5.0
    while time.monotonic() < deadline and not _order(fake.methods_called(), released):
        time.sleep(0.005)

    assert rc != EXIT_SUCCESS
    assert "interrupted" in captured.err
    methods = fake.methods_called()
    assert _order(methods, released)
    assert proto.ROBOT_RELAX not in methods


# --------------------------------------------------------------------------- #
# 3. rules intent                                                             #
# --------------------------------------------------------------------------- #


def test_an_over_limit_intent_with_no_engine_prints_the_registry_refusal(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The shared expectation with tests/test_intents.py: ONE refusal text."""
    payload = {"vx": 0.0, "vy": 0.0, "vyaw": 9.0}
    expected = default_registry().inject("move", dict(payload)).reason

    rc = main(
        [
            "rules",
            "intent",
            "move",
            "--payload",
            json.dumps(payload),
            "--state",
            _state(tmp_path),
        ]
    )
    captured = capsys.readouterr()
    assert rc == EXIT_USER_ERROR
    assert captured.err.splitlines()[0] == f"error: {expected}"
    assert "out of range" in expected


def test_an_accepted_intent_with_no_engine_sends_nothing_and_exits_zero(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    rc = main(["rules", "intent", "stop", "--state", _state(tmp_path), "--json"])
    assert rc == EXIT_SUCCESS
    payload = json.loads(capsys.readouterr().out)
    assert payload["admitted"] is True
    assert payload["sent"] is False
    assert payload["engine"] is None


def test_a_bad_payload_is_a_user_error(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    rc = main(["rules", "intent", "move", "--payload", "{nope", "--state", _state(tmp_path)])
    assert rc == EXIT_USER_ERROR
    assert "not valid JSON" in capsys.readouterr().err


def test_an_intent_reaches_a_live_engine_and_is_acknowledged(
    fake: FakeRobotd, client_factory, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Run the engine on a thread and submit through the spool it drains."""
    state_dir = _state(tmp_path)
    outcome: list[int] = []

    def run_engine() -> None:
        outcome.append(main([*_run_argv(fake, state_dir), "--max-ticks", "400", "--apply"]))

    thread = threading.Thread(target=run_engine, name="engine-under-test", daemon=True)
    thread.start()
    try:
        spool = compose.IntentSpool(
            Path(state_dir) / compose.INTENT_SPOOL_NAME,
            Path(state_dir) / compose.INTENT_LOG_NAME,
            default_registry(),
        )
        intent_id = spool.submit("stop", {})
        deadline = time.monotonic() + 2.0
        record = None
        while record is None and time.monotonic() < deadline:
            record = next((r for r in spool.records() if r.get("id") == intent_id), None)
            if record is None:
                time.sleep(0.01)
    finally:
        thread.join(timeout=10.0)

    assert record is not None, "the engine did not acknowledge the intent within 2 s"
    assert record["admitted"] is True
    assert record["kind"] == "stop"
    assert outcome == [EXIT_SUCCESS]


# --------------------------------------------------------------------------- #
# engine start / stop / status                                                #
# --------------------------------------------------------------------------- #


def test_engine_start_spawns_a_detached_run_with_apply(
    fake: FakeRobotd,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    spawned: list[list[str]] = []

    class _Child:
        pid = 4242

    monkeypatch.setattr(rules_cmd, "_spawn", lambda argv: spawned.append(argv) or _Child())
    state_dir = _state(tmp_path)
    rc = main(
        ["rules", "engine", "start", "--socket", fake.socket_path, "--state", state_dir, "--json"]
    )
    assert rc == EXIT_SUCCESS
    payload = json.loads(capsys.readouterr().out)
    assert payload["pid"] == 4242
    argv = spawned[0]
    assert rules_cmd.ENGINE_MARKER in " ".join(argv)
    assert argv[-1] == "--apply"
    assert "--socket" in argv and fake.socket_path in argv
    # It writes nothing else: the heartbeat is the liveness record.
    assert sorted(os.listdir(state_dir)) == []


def test_engine_stop_signals_only_a_matching_cmdline(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    state_dir = _state(tmp_path)
    liveness.Heartbeat(liveness.state_path(state_dir), pid=9931).beat(tick=3)
    signalled: list[tuple[int, int]] = []
    monkeypatch.setattr(rules_cmd, "_kill", lambda pid, sig: signalled.append((pid, sig)))

    monkeypatch.setattr(rules_cmd, "_proc_cmdline", lambda pid: "/usr/bin/sshd -D")
    assert main(["rules", "engine", "stop", "--state", state_dir, "--json"]) == EXIT_SUCCESS
    assert json.loads(capsys.readouterr().out)["outcome"] == "stale"
    assert signalled == []

    monkeypatch.setattr(
        rules_cmd, "_proc_cmdline", lambda pid: "python -m microduck_cli rules engine run --apply"
    )
    assert main(["rules", "engine", "stop", "--state", state_dir, "--json"]) == EXIT_SUCCESS
    assert json.loads(capsys.readouterr().out)["outcome"] == "signalled"
    assert signalled == [(9931, 15)]


def test_engine_stop_with_no_heartbeat_is_an_answer_not_a_failure(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    rc = main(["rules", "engine", "stop", "--state", _state(tmp_path), "--json"])
    assert rc == EXIT_SUCCESS
    assert json.loads(capsys.readouterr().out)["outcome"] == "nothing-to-stop"


def test_engine_status_reports_liveness_and_daemon_reachability(
    fake: FakeRobotd,
    client_factory,
    seams: FakeClock,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    state_dir = _state(tmp_path)
    rc = main(
        ["rules", "engine", "status", "--socket", fake.socket_path, "--state", state_dir, "--json"]
    )
    assert rc == EXIT_SUCCESS
    payload = json.loads(capsys.readouterr().out)
    assert payload["live"] is False
    assert payload["daemon"]["reachable"] is True
    assert payload["daemon"]["api_version"] == proto.API_VERSION

    liveness.Heartbeat(liveness.state_path(state_dir), pid=os.getpid(), clock=seams).beat(
        tick=11, hz=50.0
    )
    rc = main(
        ["rules", "engine", "status", "--socket", fake.socket_path, "--state", state_dir, "--json"]
    )
    assert rc == EXIT_SUCCESS
    payload = json.loads(capsys.readouterr().out)
    assert payload["live"] is True
    assert payload["pid_alive"] is True
    assert payload["tick"] == 11


def test_engine_status_text_says_when_no_duck_could_be_resolved(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    rc = main(["rules", "engine", "status", "--state", _state(tmp_path)])
    assert rc == EXIT_SUCCESS
    out = capsys.readouterr().out
    assert "no heartbeat" in out
    assert "not probed" in out
