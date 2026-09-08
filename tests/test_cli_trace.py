"""Tests for the ``trace`` noun group (``microduck_cli/cli/_commands/trace.py``).

The module under test is argparse wiring: every assertion here is about the CLI
contracts (overview, --json, the two-line error/hint, exit codes) or about the
public ``microduck_cli.trace`` functions being called correctly — never about
rendering or recording internals, which have their own ``test_trace_*`` files.
"""

from __future__ import annotations

import ast
import json
import shutil
from pathlib import Path

import pytest

from microduck_cli.cli import main
from microduck_cli.trace.events import RunDir, load_events

REPO = Path(__file__).resolve().parents[1]
EXAMPLE = REPO / "docs" / "traces" / "2026-09-08-spark-tutorial"
FIXTURES = Path(__file__).resolve().parent / "fixtures"
TRACE_MODULE = REPO / "microduck_cli" / "cli" / "_commands" / "trace.py"

VERB_PATHS = [
    ["trace"],
    ["trace", "overview"],
    ["trace", "plan"],
    ["trace", "run"],
    ["trace", "exec"],
    ["trace", "import"],
    ["trace", "render"],
    ["trace", "serve"],
    ["trace", "list"],
]

TUTORIAL_MDX = """\
# Tiny tutorial

## Step 1: Say hello

```bash
printf 'hello\\n'
```

```text
not a command fence
```

## Step 2: Say goodbye

```sh
printf 'bye\\n'  # a trailing comment
```
"""


# --- overview -------------------------------------------------------------


def test_trace_overview_names_its_audience(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["trace", "overview"]) == 0
    out = capsys.readouterr().out
    assert "operators and agents" in out
    assert "reviewers" in out
    assert "tutorial" in out
    assert "a verification record says what happened" in out


def test_trace_overview_ignores_a_stray_positional(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["trace", "overview", "/no/such/path"]) == 0
    assert capsys.readouterr().out.strip()


def test_bare_trace_prints_the_noun_overview(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["trace"]) == 0
    assert "# microduck-cli trace" in capsys.readouterr().out


def test_trace_overview_json(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["trace", "overview", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["subject"] == "microduck-cli trace"
    titles = [section["title"] for section in payload["sections"]]
    assert "Audience" in titles
    assert "Verbs" in titles


# --- help and the error contract ------------------------------------------


@pytest.mark.parametrize("path", VERB_PATHS)
def test_every_verb_has_help(path: list[str], capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exc:
        main([*path, "--help"])
    assert exc.value.code == 0
    out = capsys.readouterr().out
    assert "usage: microduck-cli " + " ".join(path) in out


def test_bad_nested_argument_is_the_two_line_contract(
    capsys: pytest.CaptureFixture[str],
) -> None:
    with pytest.raises(SystemExit) as exc:
        main(["trace", "render", "--bogus"])
    assert exc.value.code == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    lines = captured.err.strip().splitlines()
    assert lines[0].startswith("error:")
    assert lines[1].startswith("hint:")


def test_bad_nested_argument_json(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exc:
        main(["trace", "render", "--bogus", "--json"])
    assert exc.value.code == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    payload = json.loads(captured.err)
    assert payload["code"] == 1
    assert payload["message"]
    assert payload["remediation"]


def test_unknown_trace_verb_errors(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exc:
        main(["trace", "zzz-nope"])
    assert exc.value.code == 1
    assert "hint:" in capsys.readouterr().err


def test_render_of_a_missing_run_dir_errors(capsys: pytest.CaptureFixture[str]) -> None:
    rc = main(["trace", "render", "/no/such/run/dir"])
    assert rc == 1
    err = capsys.readouterr().err
    assert err.startswith("error:")
    assert "hint:" in err


# --- render ---------------------------------------------------------------


def test_render_reproduces_the_committed_example(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    copy = tmp_path / "example"
    shutil.copytree(EXAMPLE, copy)
    (copy / "index.html").unlink()
    (copy / "trace.html").unlink()

    assert main(["trace", "render", str(copy)]) == 0
    out = capsys.readouterr().out
    assert str(copy / "index.html") in out

    assert (copy / "index.html").read_bytes() == (EXAMPLE / "index.html").read_bytes()
    assert (copy / "trace.html").read_bytes() == (EXAMPLE / "trace.html").read_bytes()


def test_render_json_reports_both_files(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    copy = tmp_path / "example"
    shutil.copytree(EXAMPLE, copy)
    assert main(["trace", "render", str(copy), "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    names = {Path(f["path"]).name for f in payload["files"]}
    assert names == {"index.html", "trace.html"}
    assert all(f["bytes"] > 0 for f in payload["files"])


# --- import + list --------------------------------------------------------


def test_import_then_list(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    state = tmp_path / "state"
    src = FIXTURES / "trace_import_src"

    assert main(["trace", "import", str(src), "--state", str(state), "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["events"] > 0
    assert payload["redacted"] is True
    run_dir = Path(payload["run_dir"])
    assert (run_dir / "index.html").is_file()
    assert "/home/" not in (run_dir / "events.jsonl").read_text(encoding="utf-8")

    assert main(["trace", "list", "--state", str(state), "--json"]) == 0
    listing = json.loads(capsys.readouterr().out)
    assert [r["path"] for r in listing["runs"]] == [str(run_dir)]
    assert listing["runs"][0]["events"] == payload["events"]
    assert listing["runs"][0]["rendered"] is True

    assert main(["trace", "list", "--state", str(state)]) == 0
    assert run_dir.name in capsys.readouterr().out


def test_list_with_no_runs_is_not_an_error(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["trace", "list", "--state", str(tmp_path / "empty")]) == 0
    assert "no runs under" in capsys.readouterr().out


def test_import_of_a_directory_without_events_errors(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    empty = tmp_path / "src"
    empty.mkdir()
    rc = main(["trace", "import", str(empty), "--out", str(tmp_path / "dst")])
    assert rc == 1
    assert "hint:" in capsys.readouterr().err


def test_a_failed_import_removes_the_run_dir_it_created(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    empty = tmp_path / "src"
    empty.mkdir()
    dst = tmp_path / "dst"

    assert main(["trace", "import", str(empty), "--out", str(dst)]) == 1
    capsys.readouterr()
    assert not dst.exists()


def test_a_failed_import_into_a_fresh_state_leaves_no_run_behind(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    empty = tmp_path / "src"
    empty.mkdir()
    state = tmp_path / "state"

    assert main(["trace", "import", str(empty), "--state", str(state)]) == 1
    capsys.readouterr()

    assert main(["trace", "list", "--state", str(state), "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["runs"] == []


def test_a_failed_import_leaves_an_existing_out_dir_alone(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    empty = tmp_path / "src"
    empty.mkdir()
    dst = tmp_path / "mine"
    dst.mkdir()
    (dst / "keepme.txt").write_text("mine\n", encoding="utf-8")

    assert main(["trace", "import", str(empty), "--out", str(dst)]) == 1
    capsys.readouterr()
    assert dst.is_dir()
    assert (dst / "keepme.txt").read_text(encoding="utf-8") == "mine\n"


def _src_with_malformed_meta(tmp_path: Path) -> Path:
    src = tmp_path / "bad-meta-src"
    shutil.copytree(FIXTURES / "trace_import_src", src)
    (src / "meta.json").write_text("{not json at all", encoding="utf-8")
    return src


def test_import_with_a_malformed_meta_is_the_two_line_contract(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    src = _src_with_malformed_meta(tmp_path)
    rc = main(["trace", "import", str(src), "--state", str(tmp_path / "state")])
    assert rc == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    lines = captured.err.strip().splitlines()
    assert len(lines) == 2
    assert lines[0].startswith("error:")
    assert lines[1].startswith("hint:")
    assert "Traceback" not in captured.err


def test_import_with_a_malformed_meta_json_mode(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    src = _src_with_malformed_meta(tmp_path)
    rc = main(["trace", "import", str(src), "--state", str(tmp_path / "state"), "--json"])
    assert rc == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    payload = json.loads(captured.err)
    assert payload["code"] == 1
    assert "meta.json" in payload["message"]
    assert payload["remediation"]


# --- exec -----------------------------------------------------------------


def test_exec_records_a_cmd_end(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    run = tmp_path / "run"
    assert main(["trace", "exec", "--run", str(run), "--json", "--", "printf", "hi"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["rc"] == 0
    assert payload["run_dir"] == str(run)

    events = load_events(RunDir(run))
    ends = [e for e in events if e.kind == "cmd-end"]
    assert len(ends) == 1
    assert ends[0].extra["rc"] == 0
    assert ends[0].extra["stdout_first"] == "hi"


def test_exec_reports_a_failing_commands_rc_but_still_exits_zero(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    run = tmp_path / "run"
    rc = main(["trace", "exec", "--run", str(run), "--json", "--", "sh", "-c", "exit 3"])
    assert rc == 0
    assert json.loads(capsys.readouterr().out)["rc"] == 3


def test_exec_without_a_command_errors(capsys: pytest.CaptureFixture[str]) -> None:
    rc = main(["trace", "exec"])
    assert rc == 1
    err = capsys.readouterr().err
    assert err.startswith("error:")
    assert "hint:" in err


def test_exec_uses_the_newest_run_dir_under_state(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    state = tmp_path / "state"
    older = state / "trace" / "20260101T000000Z"
    newer = state / "trace" / "20260102T000000Z"
    for path in (older, newer):
        RunDir(path).ensure()
        RunDir(path).write_meta({"t0_wall": 1000.0})

    assert main(["trace", "exec", "--state", str(state), "--json", "--", "printf", "x"]) == 0
    assert json.loads(capsys.readouterr().out)["run_dir"] == str(newer)


# --- plan -----------------------------------------------------------------


def test_plan_from_tutorial_yields_the_commands(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    mdx = tmp_path / "tutorial.mdx"
    mdx.write_text(TUTORIAL_MDX, encoding="utf-8")

    assert main(["trace", "plan", "--from-tutorial", str(mdx), "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert [s["cmd"] for s in payload["steps"]] == [
        "printf 'hello\\n'",
        "printf 'bye\\n'  # a trailing comment",
    ]
    assert payload["out"] is None

    assert main(["trace", "plan", "--from-tutorial", str(mdx)]) == 0
    assert "[[step]]" in capsys.readouterr().out


def test_plan_writes_out_and_reports_unmatched_sidecar_keys(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    mdx = tmp_path / "tutorial.mdx"
    mdx.write_text(TUTORIAL_MDX, encoding="utf-8")
    sidecar = tmp_path / "sidecar.toml"
    sidecar.write_text(
        '["printf \'hello\\\\n\'"]\nsleep_after = 1.5\n\n["nope --not-a-command"]\nretry = 1\n',
        encoding="utf-8",
    )
    out = tmp_path / "plan.toml"

    assert (
        main(
            [
                "trace",
                "plan",
                "--from-tutorial",
                str(mdx),
                "--sidecar",
                str(sidecar),
                "--out",
                str(out),
            ]
        )
        == 0
    )
    captured = capsys.readouterr()
    assert str(out) in captured.out
    assert "nope --not-a-command" in captured.err
    assert "sleep_after = 1.5" in out.read_text(encoding="utf-8")


def test_plan_with_an_unreadable_tutorial_errors(capsys: pytest.CaptureFixture[str]) -> None:
    rc = main(["trace", "plan", "--from-tutorial", "/no/such/tutorial.mdx"])
    assert rc == 1
    assert "hint:" in capsys.readouterr().err


def test_plan_without_from_tutorial_is_a_parse_error(
    capsys: pytest.CaptureFixture[str],
) -> None:
    with pytest.raises(SystemExit) as exc:
        main(["trace", "plan"])
    assert exc.value.code == 1
    assert "hint:" in capsys.readouterr().err


# --- run ------------------------------------------------------------------

TWO_STEP_PLAN = """\
title = "Two printfs"

[[step]]
step = "1"
label = "printf one"
cmd = "printf 'one\\n'"

[[step]]
step = "2"
label = "printf two"
cmd = "printf 'two\\n'"
"""


def test_run_executes_a_two_step_plan_headless(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    plan_path = tmp_path / "plan.toml"
    plan_path.write_text(TWO_STEP_PLAN, encoding="utf-8")
    out = tmp_path / "run"

    rc = main(
        [
            "trace",
            "run",
            "--plan",
            str(plan_path),
            "--out",
            str(out),
            "--state",
            str(tmp_path / "state"),
            "--headless",
            "--json",
        ]
    )
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["steps_run"] == 2
    assert payload["failures"] == 0
    assert payload["headless"] is True
    assert payload["autolock_paused"] is False
    assert (out / "index.html").is_file()
    assert (out / "plan.toml").is_file()

    labels = [e.extra.get("stdout_first") for e in load_events(RunDir(out)) if e.kind == "cmd-end"]
    assert labels == ["one", "two"]


def test_run_exits_zero_when_a_step_fails(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    plan_path = tmp_path / "plan.toml"
    plan_path.write_text(
        'title = "fails"\n\n[[step]]\nstep = "1"\nlabel = "boom"\ncmd = "exit 3"\n',
        encoding="utf-8",
    )
    out = tmp_path / "run"
    rc = main(
        [
            "trace",
            "run",
            "--plan",
            str(plan_path),
            "--out",
            str(out),
            "--state",
            str(tmp_path / "state"),
            "--headless",
            "--json",
        ]
    )
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["steps_run"] == 1
    assert payload["failures"] == 1
    assert (out / "index.html").is_file()


def test_run_with_an_invalid_plan_errors(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    plan_path = tmp_path / "plan.toml"
    plan_path.write_text('title = "no cmd"\n\n[[step]]\nstep = "1"\n', encoding="utf-8")
    rc = main(["trace", "run", "--plan", str(plan_path), "--out", str(tmp_path / "run")])
    assert rc == 1
    assert "hint:" in capsys.readouterr().err
    assert not (tmp_path / "run").exists()


def test_run_without_a_display_records_a_headless_note(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from microduck_cli.cli._commands import trace as trace_cmd

    monkeypatch.setattr(trace_cmd, "_discover_display", lambda: None)
    plan_path = tmp_path / "plan.toml"
    plan_path.write_text(TWO_STEP_PLAN, encoding="utf-8")
    out = tmp_path / "run"

    rc = main(
        [
            "trace",
            "run",
            "--plan",
            str(plan_path),
            "--out",
            str(out),
            "--state",
            str(tmp_path / "state"),
            "--json",
        ]
    )
    assert rc == 0
    assert json.loads(capsys.readouterr().out)["headless"] is True
    notes = RunDir(out).read_meta()["notes"]
    assert any("no display" in note for note in notes)


def test_run_never_injects_apply(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    plan_path = tmp_path / "plan.toml"
    plan_path.write_text(
        'title = "echo"\n\n[[step]]\nstep = "1"\nlabel = "duck do walk"\n'
        "cmd = \"printf 'ran: %s\\\\n' 'duck do walk'\"\n",
        encoding="utf-8",
    )
    out = tmp_path / "run"
    assert (
        main(
            [
                "trace",
                "run",
                "--plan",
                str(plan_path),
                "--out",
                str(out),
                "--state",
                str(tmp_path / "state"),
                "--headless",
            ]
        )
        == 0
    )
    capsys.readouterr()
    assert "--apply" not in (out / "events.jsonl").read_text(encoding="utf-8")


def test_run_pause_autolock_is_opt_in_and_confirmed_on_a_tty(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from contextlib import contextmanager

    from microduck_cli.cli._commands import trace as trace_cmd

    calls: list[str] = []

    @contextmanager
    def fake_pause(*, env=None):
        calls.append("paused")
        # A real pause yields the originals it saved; a falsy yield would mean
        # nothing was actually paused, and the run must then report False.
        yield {"idle-delay": "uint32 300", "lock-enabled": "true"}

    monkeypatch.setattr(trace_cmd, "_pause_autolock", fake_pause)
    monkeypatch.setattr(trace_cmd, "_isatty", lambda: False)
    plan_path = tmp_path / "plan.toml"
    plan_path.write_text(TWO_STEP_PLAN, encoding="utf-8")

    args = [
        "trace",
        "run",
        "--plan",
        str(plan_path),
        "--state",
        str(tmp_path / "state"),
        "--headless",
        "--json",
    ]
    # Without the flag, nothing touches the desktop settings.
    assert main([*args, "--out", str(tmp_path / "a")]) == 0
    assert json.loads(capsys.readouterr().out)["autolock_paused"] is False
    assert calls == []

    # With the flag on a pipe, the flag itself is the consent.
    assert main([*args, "--pause-autolock", "--out", str(tmp_path / "b")]) == 0
    assert json.loads(capsys.readouterr().out)["autolock_paused"] is True
    assert calls == ["paused"]

    # On a TTY the operator is asked, and a refusal leaves the desktop alone.
    monkeypatch.setattr(trace_cmd, "_isatty", lambda: True)
    monkeypatch.setattr(trace_cmd, "confirm_on_tty", lambda question: False)
    assert main([*args, "--pause-autolock", "--out", str(tmp_path / "c")]) == 0
    assert json.loads(capsys.readouterr().out)["autolock_paused"] is False
    assert calls == ["paused"]


def test_run_pauses_the_autolock_with_the_displays_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from contextlib import contextmanager

    from microduck_cli.cli._commands import trace as trace_cmd
    from microduck_cli.trace.capture import DisplayEnv

    display = DisplayEnv(display=":9", xauthority="/tmp/x9", dbus="unix:path=/tmp/bus9")
    seen: list[object] = []

    @contextmanager
    def fake_pause(*, env=None):
        seen.append(env)
        yield {}

    monkeypatch.setattr(trace_cmd, "_discover_display", lambda: display)
    monkeypatch.setattr(trace_cmd, "_pause_autolock", fake_pause)
    monkeypatch.setattr(trace_cmd, "_isatty", lambda: False)

    plan_path = tmp_path / "plan.toml"
    plan_path.write_text(TWO_STEP_PLAN, encoding="utf-8")
    rc = main(
        [
            "trace",
            "run",
            "--plan",
            str(plan_path),
            "--out",
            str(tmp_path / "run"),
            "--state",
            str(tmp_path / "state"),
            "--pause-autolock",
            "--json",
        ]
    )
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["headless"] is False
    assert seen == [display]


# --- the package functions the noun delegates to --------------------------


def test_run_traced_derives_headless_and_drops_the_shot(tmp_path: Path) -> None:
    from microduck_cli.trace.plan import load_plan
    from microduck_cli.trace.runner import run_traced

    plan = load_plan(TWO_STEP_PLAN)
    run = RunDir(tmp_path / "run")
    taken: list[str] = []

    traced = run_traced(
        plan,
        run,
        pause_autolock=False,
        display_env=None,
        state_dir=str(tmp_path / "state"),
        shot=lambda *a, **k: taken.append("shot"),
    )
    assert traced.headless is True
    assert traced.autolock_paused is False
    assert traced.plan_result.steps_run == 2
    assert taken == []


def test_list_runs_is_ordered_and_carries_the_title(tmp_path: Path) -> None:
    from microduck_cli.trace.events import ensure_run_dir, list_runs, update_meta

    state = tmp_path / "state"
    for stamp in ("20260102T000000Z", "20260101T000000Z"):
        run = ensure_run_dir(state / "trace" / stamp, 1000.0)
        update_meta(run, title=f"run {stamp}")
    (state / "trace" / "20260101T000000Z" / "index.html").write_text("x", encoding="utf-8")

    runs = list_runs(str(state))
    assert [r.stamp for r in runs] == ["20260101T000000Z", "20260102T000000Z"]
    assert runs[0].rendered is True
    assert runs[1].rendered is False
    assert runs[0].title == "run 20260101T000000Z"


def test_update_meta_keeps_the_keys_it_was_not_given(tmp_path: Path) -> None:
    from microduck_cli.trace.events import ensure_run_dir, update_meta

    run = ensure_run_dir(tmp_path / "run", 1234.5)
    meta = update_meta(run, title="a title")
    assert meta["t0_wall"] == 1234.5
    assert meta["title"] == "a title"
    assert update_meta(run, title=None)["title"] == "a title"


def test_list_json_reports_the_title(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    plan_path = tmp_path / "plan.toml"
    plan_path.write_text(TWO_STEP_PLAN, encoding="utf-8")
    state = tmp_path / "state"
    assert (
        main(
            [
                "trace",
                "run",
                "--plan",
                str(plan_path),
                "--state",
                str(state),
                "--headless",
                "--title",
                "Named run",
                "--json",
            ]
        )
        == 0
    )
    capsys.readouterr()

    assert main(["trace", "list", "--state", str(state), "--json"]) == 0
    runs = json.loads(capsys.readouterr().out)["runs"]
    assert len(runs) == 1
    assert runs[0]["title"] == "Named run"


# --- serve ----------------------------------------------------------------


def test_serve_check_prints_a_url(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    copy = tmp_path / "example"
    shutil.copytree(EXAMPLE, copy)
    assert main(["trace", "serve", str(copy), "--check"]) == 0
    out = capsys.readouterr().out.strip()
    assert out.startswith("http://127.0.0.1:")
    assert out.endswith("/index.html")


def test_serve_check_json(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    copy = tmp_path / "example"
    shutil.copytree(EXAMPLE, copy)
    assert main(["trace", "serve", str(copy), "--check", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["url"].endswith("/index.html")


def test_serve_of_a_missing_run_dir_errors(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["trace", "serve", "/no/such/dir", "--check"]) == 1
    assert "hint:" in capsys.readouterr().err


def test_serve_refuses_a_non_loopback_host_without_allow_remote(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    copy = tmp_path / "example"
    shutil.copytree(EXAMPLE, copy)
    assert main(["trace", "serve", str(copy), "--host", "0.0.0.0", "--check"]) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    lines = captured.err.strip().splitlines()
    assert lines[0].startswith("error:")
    assert lines[1].startswith("hint:")
    assert "0.0.0.0" in lines[0]
    # The hint names the CLI's flag, not the library keyword.
    assert "--allow-remote" in lines[1]


def test_serve_non_loopback_host_json_error(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    copy = tmp_path / "example"
    shutil.copytree(EXAMPLE, copy)
    assert main(["trace", "serve", str(copy), "--host", "0.0.0.0", "--check", "--json"]) == 1
    payload = json.loads(capsys.readouterr().err)
    assert payload["code"] == 1
    assert "loopback-only" in payload["message"]


def test_serve_allow_remote_passes_the_host_through(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from microduck_cli.cli._commands import trace as trace_cmd
    from microduck_cli.trace import serve as serve_mod

    seen: dict[str, object] = {}

    def spy(run_dir, **kwargs):
        seen.update(kwargs)
        return serve_mod.serve(run_dir, **{**kwargs, "host": "127.0.0.1"})

    monkeypatch.setattr(trace_cmd, "_serve", spy)
    copy = tmp_path / "example"
    shutil.copytree(EXAMPLE, copy)

    rc = main(["trace", "serve", str(copy), "--host", "0.0.0.0", "--allow-remote", "--check"])
    assert rc == 0
    assert seen["host"] == "0.0.0.0"
    assert seen["allow_remote"] is True
    assert capsys.readouterr().out.strip().endswith("/index.html")


# --- the module stays thin wiring ----------------------------------------


def _module_tree() -> ast.Module:
    return ast.parse(TRACE_MODULE.read_text(encoding="utf-8"))


def test_trace_command_module_imports_only_public_trace_names() -> None:
    tree = _module_tree()
    imported: list[tuple[str, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and (node.module or "").startswith("microduck_cli."):
            for alias in node.names:
                imported.append((node.module or "", alias.name))
        assert not isinstance(node, ast.Import) or not (
            node.names[0].name.startswith("microduck_cli")
        ), "import microduck_cli.* directly; use 'from … import <public name>'"

    trace_imports = [(mod, name) for mod, name in imported if mod.startswith("microduck_cli.trace")]
    assert trace_imports, "the noun must call the trace package"
    for module, name in trace_imports:
        assert not name.startswith("_"), f"{module}.{name} is private"
        assert module.count(".") == 2, f"{module} is not a microduck_cli.trace submodule"


def _attribute_names() -> set[str]:
    return {node.attr for node in ast.walk(_module_tree()) if isinstance(node, ast.Attribute)}


@pytest.mark.parametrize("attr", ["iterdir", "listdir", "read_meta", "write_meta", "events_path"])
def test_trace_command_module_does_not_touch_the_run_dir_itself(attr: str) -> None:
    # Directory traversal, meta mutation and reading events.jsonl are the
    # package's job (events.list_runs / update_meta / count_events).
    assert attr not in _attribute_names()


def test_trace_command_module_never_names_the_events_file_as_a_path() -> None:
    literals = {
        node.value
        for node in ast.walk(_module_tree())
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
    }
    # It appears inside help/overview prose, never as a path segment of its own.
    assert "events.jsonl" not in literals


def test_trace_command_module_defines_no_loop_or_thread_of_its_own() -> None:
    tree = _module_tree()
    imported = {
        alias.name.split(".")[0]
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    } | {
        (node.module or "").split(".")[0]
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
    }
    assert "threading" not in imported
    assert "subprocess" not in imported
    assert "http" not in imported
    for node in ast.walk(tree):
        assert not isinstance(node, ast.While), "no loop belongs in the noun wiring"
        assert not isinstance(node, ast.AsyncFunctionDef)
