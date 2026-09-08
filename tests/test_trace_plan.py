"""Tests for microduck_cli/trace/plan.py.

Parity with docs/tools/check_tutorial.py::extract_commands is loaded by path via
importlib, exactly as tests/test_check_tutorial.py does (that script is not part
of the installed package).
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

from microduck_cli.cli._errors import CliError
from microduck_cli.trace import plan as trace_plan
from microduck_cli.trace.plan import Plan, Step, dump_plan, from_tutorial, load_plan

CHECK_TUTORIAL_PATH = Path(__file__).resolve().parents[1] / "docs" / "tools" / "check_tutorial.py"


def _load_check_tutorial():
    spec = importlib.util.spec_from_file_location("check_tutorial", CHECK_TUTORIAL_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


check_tutorial = _load_check_tutorial()


FIXTURE_TUTORIAL = """\
# Tutorial

Some intro text before any Step heading.

## Step 1: Setup

```bash
$ echo hello
```

```text
this is a transcript, not a command fence, and must be ignored
```

```bash nocheck
this-is-not-checked --flag
```

## Step 2: Run it

```sh
git status
free -h  # a trailing comment
```

```bash
foo bar \\
  --baz
```

## Checks

```bash
pgrep robotd
```
"""


# ---------------------------------------------------------------------------
# (a) parity with check_tutorial.extract_commands
# ---------------------------------------------------------------------------


def test_from_tutorial_commands_match_check_tutorial_extract_commands():
    expected = check_tutorial.extract_commands(FIXTURE_TUTORIAL)
    plan = from_tutorial(FIXTURE_TUTORIAL)
    assert [step.cmd for step in plan.steps] == expected
    # Sanity: the fixture actually exercises nocheck-skip, ignored non-bash/sh
    # fences, a trailing comment, and a continuation join.
    assert "this-is-not-checked --flag" not in expected
    assert "this is a transcript, not a command fence, and must be ignored" not in expected
    assert "foo bar --baz" in expected


def test_from_tutorial_local_extract_commands_matches_too():
    # plan.py's own extract_commands() helper (used internally) must agree too.
    assert trace_plan.extract_commands(FIXTURE_TUTORIAL) == check_tutorial.extract_commands(
        FIXTURE_TUTORIAL
    )


# ---------------------------------------------------------------------------
# (e) step numbers follow the "## Step N" headings
# ---------------------------------------------------------------------------


def test_step_numbers_follow_headings():
    plan = from_tutorial(FIXTURE_TUTORIAL)
    by_cmd = {step.cmd: step.step for step in plan.steps}
    assert by_cmd["echo hello"] == "1"
    assert by_cmd["git status"] == "2"
    assert by_cmd["foo bar --baz"] == "2"
    assert by_cmd["pgrep robotd"] == "0"  # under "## Checks", not a Step heading


def test_lane_is_operator_for_host_commands_else_cli():
    plan = from_tutorial(FIXTURE_TUTORIAL)
    by_cmd = {step.cmd: step.lane for step in plan.steps}
    assert by_cmd["echo hello"] == "cli"
    assert by_cmd["git status"] == "operator"
    assert by_cmd["pgrep robotd"] == "operator"


# ---------------------------------------------------------------------------
# (b) unmatched sidecar entries are reported
# ---------------------------------------------------------------------------


def test_sidecar_override_lands_on_matching_step():
    sidecar = {
        "echo hello": {"sleep_before": 1.5, "shot": "opened.png", "note": "first run"},
    }
    plan = from_tutorial(FIXTURE_TUTORIAL, sidecar=sidecar)
    target = next(step for step in plan.steps if step.cmd == "echo hello")
    assert target.sleep_before == 1.5
    assert target.shot == "opened.png"
    assert target.note == "first run"
    assert "unmatched" not in plan.meta


def test_sidecar_unmatched_entries_reported_not_dropped():
    sidecar = {
        "echo hello": {"sleep_before": 1.0},
        "this command does not appear anywhere": {"note": "orphan"},
        "another missing one": {"note": "also orphan"},
    }
    plan = from_tutorial(FIXTURE_TUTORIAL, sidecar=sidecar)
    assert plan.meta["unmatched"] == [
        "another missing one",
        "this command does not appear anywhere",
    ]


# ---------------------------------------------------------------------------
# (c) dump -> load -> dump round-trip identical
# ---------------------------------------------------------------------------


def test_dump_load_dump_round_trip_identical():
    plan = Plan(
        title="Round trip",
        steps=[
            Step(step="1", label="echo hello", cmd="echo hello"),
            Step(
                step="2",
                label="do the thing",
                cmd="git status",
                lane="operator",
                sleep_before=1.5,
                sleep_after=0.25,
                shot="frame.png",
                retry=2,
                note="flaky the first time",
                timeout_s=30.0,
            ),
        ],
        meta={"unmatched": ["a missing cmd", "another one"]},
    )
    first = dump_plan(plan)
    reloaded = load_plan(first)
    second = dump_plan(reloaded)
    assert first == second
    assert reloaded.title == plan.title
    assert reloaded.meta == plan.meta
    assert reloaded.steps == plan.steps


def test_from_tutorial_plan_round_trips():
    plan = from_tutorial(FIXTURE_TUTORIAL, sidecar={"echo hello": {"sleep_before": 0.5}})
    dumped = dump_plan(plan)
    reloaded = load_plan(dumped)
    assert dump_plan(reloaded) == dumped
    assert [s.cmd for s in reloaded.steps] == [s.cmd for s in plan.steps]


def test_dump_plan_omits_defaults():
    plan = Plan(title="t", steps=[Step(step="1", label="x", cmd="x")])
    text = dump_plan(plan)
    assert "lane" not in text
    assert "sleep_before" not in text
    assert "sleep_after" not in text
    assert "shot" not in text
    assert "retry" not in text
    assert "note" not in text
    assert "timeout_s" not in text


# ---------------------------------------------------------------------------
# (f) malformed plans raise CliError naming the step index
# ---------------------------------------------------------------------------


def test_load_plan_missing_cmd_raises_cli_error_with_index():
    text = 'title = "bad"\n\n[[step]]\nstep = "1"\nlabel = "x"\n'
    with pytest.raises(CliError) as excinfo:
        load_plan(text)
    assert "step 0" in excinfo.value.message


def test_load_plan_negative_retry_raises_cli_error_with_index():
    text = (
        'title = "bad"\n\n'
        '[[step]]\nstep = "1"\nlabel = "x"\ncmd = "x"\nretry = 0\n\n'
        '[[step]]\nstep = "2"\nlabel = "y"\ncmd = "y"\nretry = -1\n'
    )
    with pytest.raises(CliError) as excinfo:
        load_plan(text)
    assert "step 1" in excinfo.value.message


def test_load_plan_unknown_lane_raises_cli_error_with_index():
    text = '[[step]]\nstep = "1"\nlabel = "x"\ncmd = "x"\nlane = "not-a-real-lane"\n'
    with pytest.raises(CliError) as excinfo:
        load_plan(text)
    assert "step 0" in excinfo.value.message
    assert "not-a-real-lane" in excinfo.value.message


def test_load_plan_defaults_when_title_and_meta_absent():
    plan = load_plan('[[step]]\nstep = "1"\nlabel = "x"\ncmd = "x"\n')
    assert plan.title == ""
    assert plan.meta == {}
    assert plan.steps[0].lane == "cli"
