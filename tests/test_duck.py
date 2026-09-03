"""Tests for the ``duck`` noun (``microduck_cli/cli/_commands/duck.py``).

Everything runs against :class:`tests.fake_robotd.FakeRobotd` on a real unix
socket, driven through ``main()`` — so a failure here is a failure of the verb
as an operator or an agent meets it, not of a stubbed handler.

Three properties are pinned hardest:

* the **verb set** is ``robotctl``'s, plus exactly two additions (a table test);
* every **gated** verb passes the same three gate tests — a pty prompt that
  sends nothing on "n", a non-TTY dry-run with an empty call log, and a non-TTY
  ``--apply`` that sends the exact call;
* **stdout never mixes**: ``record`` and ``monitor --json`` put JSON on stdout
  and nothing else, and no sentinel secret reaches any stream.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import pty
import sys
from typing import Iterator

import pytest

from microduck_cli.cli import _build_parser, main
from microduck_cli.cli._commands import duck as duck_cmd
from microduck_cli.cli._output import PROG
from microduck_cli.duck.gate import SAFETY_INIT, SAFETY_RELAX, SAFETY_STOP
from microduck_cli.explain.duck import CHEATSHEET, ENTRIES, VERBS
from microduck_cli.ipc import proto
from tests.fake_robotd import FakeRobotd
from tests.test_no_secrets_in_output import assert_no_secrets

# ---------------------------------------------------------------------------
# fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def fake() -> Iterator[FakeRobotd]:
    """A fake daemon with a policy loaded and driving, so no verb is refused for setup."""
    with FakeRobotd() as running:
        running.set_state(
            enabled=True,
            skills=("ground_pick", "kick_left", "kick_right", "sit_toggle", "roulade"),
            walk_policy="walk.onnx",
            stand_policy="stand.onnx",
            unavailable=None,
        )
        yield running


@contextlib.contextmanager
def pty_stdin(answer: str) -> Iterator[None]:
    """Swap ``sys.stdin`` for a real pseudo-terminal already holding *answer*.

    A genuine tty, not a mocked ``isatty()``: this is what makes the PROMPT
    branch of :func:`microduck_cli.duck.gate.consent` the one under test.
    """
    primary, secondary = pty.openpty()
    os.write(primary, answer.encode())
    old = sys.stdin
    try:
        with os.fdopen(secondary, "r", closefd=True) as tty:
            sys.stdin = tty
            yield
    finally:
        sys.stdin = old
        os.close(primary)


def _sock(fake: FakeRobotd, *args: str) -> list[str]:
    return ["duck", *args, "--socket", fake.socket_path]


# ---------------------------------------------------------------------------
# 1. the verb table, pinned against robotctl at the pinned commit
# ---------------------------------------------------------------------------

#: ``robotctl``'s ``RobotCommand`` enum, transcribed from ``robotctl/src/main.rs``
#: at ``pollen-robotics/microduck`` ``0cd676d6fbb6e90a762c84aa63abe7a02dbc9495``
#: (the commit pinned in ``docs/upstream-pins.md``). Read, never copied.
ROBOTCTL_ROBOT_NAMESPACE = frozenset({"init", "enable", "relax", "do", "mode", "look"})

#: The daemon-level verbs from the same file's ``Namespace`` enum that this noun
#: mirrors. (``net``, ``system``, ``pad``, ``update``, ``chorale``, ``theremin``
#: and ``completions`` belong to other nouns or to no noun here.)
ROBOTCTL_DAEMON_VERBS = frozenset({"health", "version", "monitor", "quack", "configure"})

#: ``robot.stop`` has no ``robotctl`` subcommand — upstream documents it under
#: ``robot relax`` ("`robot.stop` zeroes the velocity and keeps the robot
#: standing") and fires it from the console and the gamepad. It is a daemon verb
#: this CLI exposes, not an invention: the method is in the pinned proto.
DAEMON_METHOD_VERBS = frozenset({"stop"})

UPSTREAM_VERBS = ROBOTCTL_ROBOT_NAMESPACE | ROBOTCTL_DAEMON_VERBS | DAEMON_METHOD_VERBS

#: The only two verbs beyond upstream's words, and why each is allowed:
#:
#: ``move``   upstream drives ``robot.move`` from the gamepad and the browser
#:            console, so there is no CLI subcommand to mirror — but a headless
#:            agent has neither, and the deadman means a single notification is
#:            not a drive. It is a robot verb, and it is ours.
#: ``record`` is not a robot command at all: it commands nothing and changes
#:            nothing, it *records* what the duck reports. Upstream has no
#:            equivalent because ``robotctl monitor`` is a live view, not a file.
ALLOWED_EXTRA_VERBS = {"move", "record"}


def _registered_duck_verbs() -> set[str]:
    parser = _build_parser()
    sub = [a for a in parser._actions if isinstance(a, argparse._SubParsersAction)][0]
    duck_parser = sub.choices["duck"]
    noun_sub = [a for a in duck_parser._actions if isinstance(a, argparse._SubParsersAction)][0]
    return set(noun_sub.choices) - {"overview"}


def test_verb_set_is_robotctls_plus_exactly_move_and_record() -> None:
    verbs = _registered_duck_verbs()
    assert UPSTREAM_VERBS - verbs == set(), "a robotctl verb is missing from the duck noun"
    assert verbs - UPSTREAM_VERBS == ALLOWED_EXTRA_VERBS


def test_every_verb_has_a_catalog_entry_and_a_verb_line() -> None:
    verbs = _registered_duck_verbs() | {"overview"}
    for verb in verbs:
        assert ("duck", verb) in ENTRIES, f"{verb} has no explain entry"
        assert any(line.startswith(f"duck {verb} ") for line in VERBS), f"{verb} has no VERBS line"


def test_every_catalog_entry_links_the_owning_upstream_page() -> None:
    for path, body in ENTRIES.items():
        assert CHEATSHEET in body, f"{' '.join(path)} does not link the upstream cheatsheet"


def test_skill_names_are_robotctls_and_translate_to_the_wire() -> None:
    # SkillArg::as_skill at the pinned commit.
    assert duck_cmd.SKILL_ARGS == {
        "ground-pick": "ground_pick",
        "kick-left": "kick_left",
        "kick-right": "kick_right",
        "sit": "sit_toggle",
        "roulade": "roulade",
    }


# ---------------------------------------------------------------------------
# 2. the gate: three tests per gated verb
# ---------------------------------------------------------------------------

#: ``(verb, extra argv, the method the apply path must send)``.
GATED: list[tuple[str, list[str], str]] = [
    ("init", [], proto.ROBOT_INIT),
    ("relax", ["--yes"], proto.ROBOT_RELAX),
    ("enable", ["--on"], proto.ROBOT_ENABLE),
    ("do", ["roulade"], proto.ROBOT_DO),
    ("move", ["--vx", "0.1", "--duration", "0.05"], proto.ROBOT_MOVE),
    ("mode", ["--set", "walk"], proto.ROBOT_SET_MODE),
    ("look", ["--x", "1", "--y", "0", "--z", "0"], proto.ROBOT_LOOK),
]
GATED_IDS = [row[0] for row in GATED]


@pytest.mark.parametrize("verb,extra,method", GATED, ids=GATED_IDS)
def test_gated_verb_non_tty_prints_the_plan_and_sends_nothing(
    verb: str, extra: list[str], method: str, fake: FakeRobotd, capsys: pytest.CaptureFixture[str]
) -> None:
    rc = main(_sock(fake, verb, *extra))
    out = capsys.readouterr().out
    assert rc == 0
    assert "Dry-run plan" in out
    assert method in out
    assert "No sockets opened, no calls sent" in out
    assert fake.methods_called() == [], "a dry run must not even open the socket"


@pytest.mark.parametrize("verb,extra,method", GATED, ids=GATED_IDS)
def test_gated_verb_apply_sends_the_exact_call(
    verb: str, extra: list[str], method: str, fake: FakeRobotd, capsys: pytest.CaptureFixture[str]
) -> None:
    rc = main(_sock(fake, verb, *extra, "--apply"))
    capsys.readouterr()
    called = fake.methods_called()
    assert rc == 0, f"{verb} --apply failed"
    assert called[0] == proto.HELLO
    assert method in called, f"{verb} --apply did not send {method}: {called}"


@pytest.mark.parametrize("verb,extra,method", GATED, ids=GATED_IDS)
def test_gated_verb_prompts_on_a_tty_and_no_sends_nothing(
    verb: str, extra: list[str], method: str, fake: FakeRobotd, capsys: pytest.CaptureFixture[str]
) -> None:
    with pty_stdin("n\n"):
        rc = main(_sock(fake, verb, *extra))
    captured = capsys.readouterr()
    assert rc == 1, "an aborted gated verb exits 1 so the exit code answers 'did it move?'"
    assert "Proceed? [y/N]" in captured.err
    assert "Safety:" in captured.err
    assert method in captured.err
    assert captured.err.splitlines()[-2].startswith("error: ")
    assert captured.err.splitlines()[-1].startswith("hint: ")
    assert fake.methods_called() == []


def test_gated_verb_prompt_accepts_yes_and_sends(
    fake: FakeRobotd, capsys: pytest.CaptureFixture[str]
) -> None:
    with pty_stdin("y\n"):
        rc = main(_sock(fake, "init"))
    capsys.readouterr()
    assert rc == 0
    assert proto.ROBOT_INIT in fake.methods_called()


def test_dry_run_plan_carries_the_verbs_own_safety_sentence(
    fake: FakeRobotd, capsys: pytest.CaptureFixture[str]
) -> None:
    main(_sock(fake, "init"))
    assert SAFETY_INIT in capsys.readouterr().out
    main(_sock(fake, "relax"))
    assert SAFETY_RELAX in capsys.readouterr().out


def test_dry_run_json_carries_the_plan_and_sends_nothing(
    fake: FakeRobotd, capsys: pytest.CaptureFixture[str]
) -> None:
    rc = main(_sock(fake, "init", "--json"))
    payload = json.loads(capsys.readouterr().out)
    assert rc == 0
    assert payload["dry_run"] is True
    assert payload["sent"] is False
    assert payload["safety"] == SAFETY_INIT
    assert any(proto.ROBOT_INIT in call for call in payload["calls"])
    assert "--apply" in payload["apply_command"]
    assert fake.methods_called() == []


def test_relax_apply_without_yes_is_refused_before_anything_is_sent(
    fake: FakeRobotd, capsys: pytest.CaptureFixture[str]
) -> None:
    rc = main(_sock(fake, "relax", "--apply"))
    err = capsys.readouterr().err
    assert rc == 1
    assert "--yes" in err
    assert SAFETY_RELAX in err
    assert err.splitlines()[1].startswith("hint: ")
    assert fake.methods_called() == []


def test_relax_yes_on_a_non_tty_still_only_plans(
    fake: FakeRobotd, capsys: pytest.CaptureFixture[str]
) -> None:
    """``--yes`` answers the prompt; it never implies ``--apply`` for an agent."""
    rc = main(_sock(fake, "relax", "--yes"))
    assert rc == 0
    assert "Dry-run plan" in capsys.readouterr().out
    assert fake.methods_called() == []


def test_move_sends_intents_at_rate_then_stops(
    fake: FakeRobotd, capsys: pytest.CaptureFixture[str]
) -> None:
    rc = main(_sock(fake, "move", "--vx", "0.2", "--duration", "0.2", "--apply", "--json"))
    payload = json.loads(capsys.readouterr().out)
    called = fake.methods_called()
    assert rc == 0
    assert payload["intents_sent"] >= 2, "a deadman refresh means more than one notification"
    assert called.count(proto.ROBOT_MOVE) >= 2
    assert called[-1] == proto.ROBOT_STOP, "the drive must end with a stop"
    moves = [rec for rec in fake.call_log if rec.method == proto.ROBOT_MOVE]
    assert all(rec.is_notification for rec in moves), "robot.move is a notification"
    assert moves[0].params == {"vx": 0.2, "vy": 0.0, "vyaw": 0.0}


def test_do_translates_the_robotctl_skill_name_to_the_wire(
    fake: FakeRobotd, capsys: pytest.CaptureFixture[str]
) -> None:
    rc = main(_sock(fake, "do", "sit", "--apply"))
    capsys.readouterr()
    assert rc == 0
    sent = [rec for rec in fake.call_log if rec.method == proto.ROBOT_DO]
    assert sent[0].params == {"skill": "sit_toggle"}


def test_a_refusal_is_surfaced_with_the_robots_own_reason(
    fake: FakeRobotd, capsys: pytest.CaptureFixture[str]
) -> None:
    fake.set_state(fallen=True)
    rc = main(_sock(fake, "enable", "--on", "--apply"))
    err = capsys.readouterr().err
    assert rc == 2, "the robot answered, and the answer was no"
    assert "the robot has fallen" in err
    assert err.splitlines()[1].startswith("hint: ")


# ---------------------------------------------------------------------------
# 3. the read-only verbs
# ---------------------------------------------------------------------------


def test_health_healthy_exits_zero_and_reports(
    fake: FakeRobotd, capsys: pytest.CaptureFixture[str]
) -> None:
    rc = main(_sock(fake, "health"))
    out = capsys.readouterr().out
    assert rc == 0
    assert "healthy" in out
    assert "not measured" in out, "an unmeasured battery is reported as such, never as zero"


def test_health_unhealthy_exits_two_and_still_prints_the_report(
    fake: FakeRobotd, capsys: pytest.CaptureFixture[str]
) -> None:
    fake.set_state(healthy=False, reason="bus is down")
    rc = main(_sock(fake, "health", "--json"))
    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert rc == 2, "mirrors robotctl health's non-zero exit on an unhealthy robot"
    assert payload["healthy"] is False
    assert payload["reason"] == "bus is down"
    # JSON mode: the error is one structured object on stderr, never the two text lines.
    error = json.loads(captured.err)
    assert error["code"] == 2
    assert error["remediation"]


def test_version_reports_the_handshake(
    fake: FakeRobotd, capsys: pytest.CaptureFixture[str]
) -> None:
    rc = main(_sock(fake, "version", "--json"))
    payload = json.loads(capsys.readouterr().out)
    assert rc == 0
    assert payload["api_version"] == proto.API_VERSION
    assert payload["daemon_version"] == "0.10.0"
    assert payload["client_api_version"] == proto.API_VERSION


def test_monitor_json_is_pure_ndjson(fake: FakeRobotd, capsys: pytest.CaptureFixture[str]) -> None:
    rc = main(_sock(fake, "monitor", "--json", "--frames", "3", "--hz", "50"))
    captured = capsys.readouterr()
    lines = [line for line in captured.out.splitlines() if line]
    assert rc == 0
    assert len(lines) == 3
    for line in lines:
        frame = json.loads(line)
        assert "joints" in frame
        assert "loop" in frame


def test_monitor_text_prints_one_line_per_frame(
    fake: FakeRobotd, capsys: pytest.CaptureFixture[str]
) -> None:
    rc = main(_sock(fake, "monitor", "--frames", "2", "--hz", "50"))
    lines = [line for line in capsys.readouterr().out.splitlines() if line]
    assert rc == 0
    assert len(lines) == 2
    assert all("policy=" in line and "applied=" in line for line in lines)


class _InterruptingTime:
    """``duck.time`` with a ``sleep`` that raises ``KeyboardInterrupt`` — a Ctrl-C.

    Swapped in for the module's own ``time`` only, so the client's threads keep
    the real one and the interrupt lands exactly where an operator's would: in
    the verb's own loop.
    """

    monotonic = staticmethod(__import__("time").monotonic)

    @staticmethod
    def sleep(_seconds: float) -> None:
        raise KeyboardInterrupt


def test_monitor_ends_cleanly_on_ctrl_c(
    fake: FakeRobotd, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(duck_cmd, "time", _InterruptingTime)
    rc = main(_sock(fake, "monitor", "--hz", "1"))
    captured = capsys.readouterr()
    assert rc == 0, "Ctrl-C is how monitor ends; it is not an error"
    assert "interrupted" in captured.err
    assert "Traceback" not in captured.err


def test_move_stops_the_robot_on_ctrl_c(
    fake: FakeRobotd, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(duck_cmd, "time", _InterruptingTime)
    rc = main(_sock(fake, "move", "--vx", "0.3", "--duration", "30", "--apply", "--json"))
    payload = json.loads(capsys.readouterr().out)
    assert rc == 0
    assert payload["interrupted"] is True
    assert fake.methods_called()[-1] == proto.ROBOT_STOP, "an interrupted drive still stops"


def test_stop_is_ungated_and_states_it_is_not_an_emergency_stop(
    fake: FakeRobotd, capsys: pytest.CaptureFixture[str]
) -> None:
    rc = main(_sock(fake, "stop"))
    out = capsys.readouterr().out
    assert rc == 0
    assert SAFETY_STOP in out
    assert proto.ROBOT_STOP in fake.methods_called()


def test_quack_sends_the_chirp_tag_as_a_notification(
    fake: FakeRobotd, capsys: pytest.CaptureFixture[str]
) -> None:
    rc = main(_sock(fake, "quack", "--json"))
    payload = json.loads(capsys.readouterr().out)
    assert rc == 0
    assert payload["tag"] == duck_cmd.QUACK_TAG == "chirp"
    sounds = [rec for rec in fake.call_log if rec.method == proto.ROBOT_SOUND]
    assert sounds
    assert sounds[0].is_notification
    assert sounds[0].params == {"tag": "chirp"}


def test_mode_read_is_ungated(fake: FakeRobotd, capsys: pytest.CaptureFixture[str]) -> None:
    rc = main(_sock(fake, "mode", "--json"))
    payload = json.loads(capsys.readouterr().out)
    assert rc == 0
    assert payload["mode"] == "walk"
    assert proto.ROBOT_MODE in fake.methods_called()
    assert proto.ROBOT_SET_MODE not in fake.methods_called()


# ---------------------------------------------------------------------------
# configure --list
# ---------------------------------------------------------------------------


def test_configure_list_prints_the_generated_params_file(
    fake: FakeRobotd, tmp_path, capsys: pytest.CaptureFixture[str]
) -> None:
    (tmp_path / "duck-a.sock").write_bytes(b"")
    (tmp_path / "duck-a.toml").write_text("[policy]\nenabled = false\n", encoding="utf-8")
    rc = main(
        ["duck", "configure", "--list", "--duck", "duck-a", "--state", str(tmp_path), "--json"]
    )
    payload = json.loads(capsys.readouterr().out)
    assert rc == 0
    assert payload["present"] is True
    assert "enabled = false" in payload["contents"]


def test_configure_list_says_so_when_there_is_none(
    tmp_path, capsys: pytest.CaptureFixture[str]
) -> None:
    (tmp_path / "duck-a.sock").write_bytes(b"")
    rc = main(["duck", "configure", "--list", "--duck", "duck-a", "--state", str(tmp_path)])
    out = capsys.readouterr().out
    assert rc == 0
    assert "no generated params file" in out


def test_configure_without_list_refuses_and_names_robotctl(
    tmp_path, capsys: pytest.CaptureFixture[str]
) -> None:
    (tmp_path / "duck-a.sock").write_bytes(b"")
    rc = main(["duck", "configure", "--duck", "duck-a", "--state", str(tmp_path)])
    err = capsys.readouterr().err
    assert rc == 1
    assert err.splitlines()[0].startswith("error: ")
    assert err.splitlines()[1].startswith("hint: ")
    assert "robotctl configure" in err


def test_configure_only_ever_looks_under_the_state_dir(
    tmp_path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Every path this verb opens is under the state dir — never /etc/robot/robotd.toml."""
    opened: list[str] = []
    real_open = open

    def spy(path, *args, **kwargs):  # type: ignore[no-untyped-def]
        opened.append(str(path))
        return real_open(path, *args, **kwargs)

    (tmp_path / "duck-a.sock").write_bytes(b"")
    (tmp_path / "duck-a.toml").write_text("[policy]\n", encoding="utf-8")
    monkeypatch.setattr("builtins.open", spy)
    rc = main(["duck", "configure", "--list", "--duck", "duck-a", "--state", str(tmp_path)])
    capsys.readouterr()
    assert rc == 0
    assert opened == [str(tmp_path / "duck-a.toml")]


# ---------------------------------------------------------------------------
# 4. unattended agent mode, and no secrets anywhere
# ---------------------------------------------------------------------------

#: Every verb, with the argv that makes it terminate unattended.
ALL_VERBS: list[tuple[str, list[str]]] = [
    ("overview", []),
    ("health", []),
    ("version", []),
    ("monitor", ["--frames", "1", "--hz", "50"]),
    ("stop", []),
    ("quack", []),
    ("configure", ["--list"]),
    ("record", ["--seconds", "0.05"]),
    ("init", ["--apply"]),
    ("relax", ["--yes", "--apply"]),
    ("enable", ["--on", "--apply"]),
    ("do", ["roulade", "--apply"]),
    ("mode", ["--set", "walk", "--apply"]),
    ("look", ["--x", "1", "--y", "0", "--z", "0", "--apply"]),
    ("move", ["--vx", "0.1", "--duration", "0.05", "--apply"]),
]
ALL_IDS = [row[0] for row in ALL_VERBS]


@pytest.mark.parametrize("verb,extra", ALL_VERBS, ids=ALL_IDS)
def test_every_verb_runs_unattended_with_json(
    verb: str,
    extra: list[str],
    fake: FakeRobotd,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Non-TTY, ``--json``, nothing to answer — and a planted secret never leaks."""
    sentinel = "duck-pin-sentinel-90210"
    monkeypatch.setenv("DUCK_PIN", sentinel)
    argv = ["duck", verb, *extra, "--json"]
    if verb != "overview":
        argv += ["--socket", fake.socket_path]
    rc = main(argv)
    captured = capsys.readouterr()
    assert rc == 0, f"{verb} did not run unattended: {captured.err}"
    assert_no_secrets(captured.out + captured.err, sentinels={"DUCK_PIN": sentinel})


@pytest.mark.parametrize("verb,extra,method", GATED, ids=GATED_IDS)
def test_every_gated_verb_prompts_on_a_tty(
    verb: str,
    extra: list[str],
    method: str,
    fake: FakeRobotd,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    sentinel = "duck-pin-sentinel-31337"
    monkeypatch.setenv("DUCK_PIN", sentinel)
    with pty_stdin("y\n"):
        rc = main(_sock(fake, verb, *extra, "--json"))
    captured = capsys.readouterr()
    assert rc == 0
    assert "Proceed? [y/N]" in captured.err
    assert method in fake.methods_called()
    assert_no_secrets(captured.out + captured.err, sentinels={"DUCK_PIN": sentinel})


# ---------------------------------------------------------------------------
# the client seam
# ---------------------------------------------------------------------------


def test_client_factory_is_injectable(
    fake: FakeRobotd, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A verb opens its socket through CLIENT_FACTORY and nothing else."""
    built: list[str] = []
    original = duck_cmd.CLIENT_FACTORY

    def factory(socket_path: str):
        built.append(socket_path)
        return original(socket_path)

    monkeypatch.setattr(duck_cmd, "CLIENT_FACTORY", factory)
    rc = main(_sock(fake, "version"))
    capsys.readouterr()
    assert rc == 0
    assert built == [fake.socket_path]


def test_an_unreachable_socket_is_an_environment_error(
    tmp_path, capsys: pytest.CaptureFixture[str]
) -> None:
    missing = str(tmp_path / "nope.sock")
    rc = main(["duck", "health", "--socket", missing])
    err = capsys.readouterr().err
    assert rc == 2
    assert err.splitlines()[0].startswith("error: ")
    assert err.splitlines()[1].startswith("hint: ")
    assert "Traceback" not in err


# ---------------------------------------------------------------------------
# the noun-level --json flag, and one spelling for every generated command
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "argv",
    [["duck", "--json", "health"], ["duck", "health", "--json"]],
)
def test_duck_json_before_or_after_the_verb_both_emit_json(
    argv: list[str], fake: FakeRobotd, capsys: pytest.CaptureFixture[str]
) -> None:
    """``duck --json health`` must not be silently downgraded to text.

    The verb's own ``--json`` used to default to False and overwrite the
    noun-level flag argparse had already recorded, so an agent that asked for
    JSON got prose. Both positions mean the same thing now.
    """
    rc = main([*argv, "--socket", fake.socket_path])
    payload = json.loads(capsys.readouterr().out)
    assert rc == 0
    assert payload["healthy"] is True


def test_generated_commands_all_use_the_one_prog_name(
    fake: FakeRobotd, capsys: pytest.CaptureFixture[str]
) -> None:
    """Dry-run apply lines and remediations spell the CLI exactly one way."""
    rc = main(_sock(fake, "init", "--json"))
    payload = json.loads(capsys.readouterr().out)
    assert rc == 0
    assert payload["apply_command"].startswith(f"{PROG} duck init")

    rc = main(["duck", "configure", "--socket", fake.socket_path])
    err = capsys.readouterr().err
    assert rc == 1
    assert f"{PROG} duck configure --list" in err
