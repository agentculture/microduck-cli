"""Tests for the ``policy`` noun (``microduck_cli/cli/_commands/policy.py``, t19).

Connection-oriented tests run against :class:`tests.fake_robotd.FakeRobotd` — a real
unix socket — via ``--socket`` (bypassing duck-name resolution, which
``microduck_cli.duck.addressing`` already owns and tests). Train-lane tests monkeypatch
the module-level ``_runner`` seam rather than spawning a real ``uv run ...``.
"""

from __future__ import annotations

import argparse
import json
from typing import Any

import pytest

from microduck_cli.cli import main
from microduck_cli.cli._commands import policy as policy_mod
from microduck_cli.cli._errors import EXIT_ENV_ERROR, EXIT_USER_ERROR
from microduck_cli.duck.gate import Consent
from microduck_cli.explain.policy import ENTRIES, VERBS
from microduck_cli.train import lane
from tests.fake_robotd import FakeRobotd
from tests.test_no_secrets_in_output import assert_no_secrets

TASK = "Mjlab-Velocity-Flat-MicroDuck"


def _rl_clone_at_commit(tmp_path, sha: str) -> str:
    """A real (detached-HEAD) ``.git`` under *tmp_path* whose HEAD is *sha*.

    Train-lane tests must resolve an RL clone that actually exists — never by
    depending on a ``../microduck_rl`` sibling checkout, which is present on
    a dev box but not in CI (see PR #3 review). Passing ``--rl-clone`` at
    this path makes them hermetic. A plain sha in ``.git/HEAD`` is enough for
    :func:`microduck_cli.train.lane.git_head` to read — no ``ref:``
    indirection needed for a detached head — and using the same sha for a
    ``smoke`` and a following ``train`` call satisfies the commit-match gate
    (finding 2) since both resolve the same clone.
    """
    clone = tmp_path / "rl_clone"
    git = clone / ".git"
    git.mkdir(parents=True, exist_ok=True)
    (git / "HEAD").write_text(f"{sha}\n", encoding="utf-8")
    return str(clone)


@pytest.fixture()
def fake():
    with FakeRobotd() as running:
        yield running


class _Result:
    def __init__(self, returncode: int = 0, stdout: str = "", stderr: str = "") -> None:
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


class _RecordingRunner:
    """Replaces ``policy._runner``: records every argv/env it was called with."""

    def __init__(self, returncode: int = 0) -> None:
        self.calls: list[dict[str, Any]] = []
        self.returncode = returncode

    def __call__(self, argv, *, cwd=None, env=None):
        self.calls.append({"argv": list(argv), "cwd": cwd, "env": dict(env or {})})
        return _Result(returncode=self.returncode)


def _non_hello(fake: FakeRobotd) -> list[str]:
    return [m for m in fake.methods_called() if m != "hello"]


# ---------------------------------------------------------------------------
# obligation o19 / acceptance 1: policy load issues robot.loadPolicy only,
# never opens a config file, and its text output contains the two sentences.
#
# robot.loadPolicy's only file field is `path` — an absolute path the daemon
# opens directly, never a fetch (LoadPolicyParams's doc comment: "the daemon
# resolves nothing relative"; there is no `source` field on main or on the
# pinned build). So a real call only happens for an absolute-path SOURCE; a
# Hub id like "pollen-robotics/microduck-walk" instead prints the robotctl
# line — see the "prints the robotctl line" block below.
# ---------------------------------------------------------------------------

LOCAL_WALK_POLICY = "/var/lib/robot/policies/walk.onnx"
LOCAL_UNTRUSTED_POLICY = "/home/pilot/untrusted-walk.onnx"


def test_policy_load_issues_robot_loadpolicy_only_on_api18(
    fake: FakeRobotd, capsys: pytest.CaptureFixture[str]
) -> None:
    fake.set_state(api_version=18)
    rc = main(
        [
            "policy",
            "load",
            "walk",
            LOCAL_WALK_POLICY,
            "--socket",
            fake.socket_path,
            "--apply",
        ]
    )
    assert rc == 0
    assert _non_hello(fake) == ["robot.loadPolicy"]
    record = fake.call_log[-1]
    assert record.params == {"slot": "walk", "path": LOCAL_WALK_POLICY}

    captured = capsys.readouterr()
    assert "survives a reboot" in captured.out
    assert "policy reset" in captured.out


def test_policy_load_json_mode_carries_the_same_sentences(
    fake: FakeRobotd, capsys: pytest.CaptureFixture[str]
) -> None:
    fake.set_state(api_version=18)
    rc = main(
        [
            "policy",
            "load",
            "walk",
            LOCAL_WALK_POLICY,
            "--socket",
            fake.socket_path,
            "--apply",
            "--json",
        ]
    )
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert "survives a reboot" in payload["text"]
    assert "policy reset" in payload["text"]
    assert payload["call"] == "robot.loadPolicy"


def test_policy_load_on_api16_exits_2_naming_api_ge_18(
    fake: FakeRobotd, capsys: pytest.CaptureFixture[str]
) -> None:
    # Default fake state is the pinned sim-remote-io build: API 16.
    rc = main(
        [
            "policy",
            "load",
            "walk",
            LOCAL_WALK_POLICY,
            "--socket",
            fake.socket_path,
            "--apply",
            "--json",
        ]
    )
    assert rc == EXIT_ENV_ERROR
    payload = json.loads(capsys.readouterr().err)
    assert "needs API >= 18" in payload["remediation"]
    assert _non_hello(fake) == []


# ---------------------------------------------------------------------------
# A non-path source: this CLI cannot fetch a Hub repo, so it prints the
# robotctl line and never opens a socket at all — same pattern as
# policy search/check/update.
# ---------------------------------------------------------------------------


def test_policy_load_with_a_hub_id_prints_the_robotctl_line(
    capsys: pytest.CaptureFixture[str],
) -> None:
    rc = main(["policy", "load", "walk", "pollen-robotics/microduck-walk"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "sudo robotctl policy load walk pollen-robotics/microduck-walk" in out
    assert "cannot fetch" in out


def test_policy_add_with_a_hub_id_prints_the_robotctl_line(
    capsys: pytest.CaptureFixture[str],
) -> None:
    rc = main(["policy", "add", "polite-bow", "fffiloni/microduck-polite-bow"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "sudo robotctl policy add polite-bow fffiloni/microduck-polite-bow" in out
    assert "cannot fetch" in out


# ---------------------------------------------------------------------------
# The three gate outcomes (dry-run / prompt-confirm / prompt-decline).
# ---------------------------------------------------------------------------


def test_policy_load_dry_run_sends_nothing(
    fake: FakeRobotd, capsys: pytest.CaptureFixture[str]
) -> None:
    fake.set_state(api_version=18)
    # No --apply, and pytest's captured stdin is not a tty -> Consent.DRY_RUN.
    rc = main(["policy", "load", "walk", LOCAL_UNTRUSTED_POLICY, "--socket", fake.socket_path])
    assert rc == 0
    assert _non_hello(fake) == []
    out = capsys.readouterr().out
    assert "Dry-run plan" in out
    assert "robot.loadPolicy" in out
    assert "Nothing a stranger publishes is verified" in out  # SAFETY_COMMUNITY_POLICY
    assert "survives a reboot" in out


def test_policy_load_dry_run_apply_command_names_the_literal_prog(
    fake: FakeRobotd, capsys: pytest.CaptureFixture[str]
) -> None:
    """Finding 4: the generated `apply_command` line names the literal
    installed-script prog, `microduck-cli`, matching every other generated
    command line and the internal prog name — not the bare `microduck` used
    for the installed console script itself (see CLAUDE.md's half-rename
    note)."""
    fake.set_state(api_version=18)
    rc = main(
        [
            "policy",
            "load",
            "walk",
            LOCAL_UNTRUSTED_POLICY,
            "--socket",
            fake.socket_path,
            "--json",
        ]
    )
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    apply_command = payload["text"]
    assert f"microduck-cli policy load walk {LOCAL_UNTRUSTED_POLICY} --apply" in apply_command


def test_policy_reset_all_dry_run_apply_command_names_the_literal_prog(
    fake: FakeRobotd, capsys: pytest.CaptureFixture[str]
) -> None:
    fake.set_state(api_version=18)
    rc = main(["policy", "reset", "--socket", fake.socket_path, "--json"])
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert "microduck-cli policy reset --apply" in payload["text"]


def test_policy_load_prompt_confirmed_sends_the_call(
    monkeypatch: pytest.MonkeyPatch, fake: FakeRobotd
) -> None:
    fake.set_state(api_version=18)
    monkeypatch.setattr(policy_mod, "consent", lambda apply: Consent.PROMPT)
    monkeypatch.setattr(policy_mod, "confirm_on_tty", lambda question: True)
    rc = main(
        [
            "policy",
            "load",
            "walk",
            LOCAL_WALK_POLICY,
            "--socket",
            fake.socket_path,
        ]
    )
    assert rc == 0
    assert _non_hello(fake) == ["robot.loadPolicy"]


def test_policy_load_prompt_declined_sends_nothing(
    monkeypatch: pytest.MonkeyPatch, fake: FakeRobotd, capsys: pytest.CaptureFixture[str]
) -> None:
    fake.set_state(api_version=18)
    monkeypatch.setattr(policy_mod, "consent", lambda apply: Consent.PROMPT)
    monkeypatch.setattr(policy_mod, "confirm_on_tty", lambda question: False)
    rc = main(
        [
            "policy",
            "load",
            "walk",
            LOCAL_WALK_POLICY,
            "--socket",
            fake.socket_path,
            "--json",
        ]
    )
    assert rc == EXIT_USER_ERROR
    assert _non_hello(fake) == []
    payload = json.loads(capsys.readouterr().err)
    assert "cancelled" in payload["message"]


# ---------------------------------------------------------------------------
# reset: robot.loadPolicy {slot, path: null} / robot.reloadPolicies, same gate + d1
# ---------------------------------------------------------------------------


def test_policy_reset_slot_uses_load_policy_with_null_path(fake: FakeRobotd) -> None:
    fake.set_state(api_version=18, walk_policy="something.onnx", unavailable=None)
    rc = main(["policy", "reset", "walk", "--socket", fake.socket_path, "--apply"])
    assert rc == 0
    assert _non_hello(fake) == ["robot.loadPolicy"]
    assert fake.call_log[-1].params == {"slot": "walk", "path": None}


def test_policy_reset_all_uses_reload_policies(fake: FakeRobotd) -> None:
    fake.set_state(api_version=18)
    rc = main(["policy", "reset", "--socket", fake.socket_path, "--apply"])
    assert rc == 0
    assert _non_hello(fake) == ["robot.reloadPolicies"]


# ---------------------------------------------------------------------------
# add / remove: robot.setSkill / robot.removeSkill, same gate + d1.
# add's `repo` gets the same absolute-path-or-robotctl-line treatment as
# load's `source` (see above) — robot.setSkill's `path` is not a fetch either.
# ---------------------------------------------------------------------------


def test_policy_add_on_api18_issues_exactly_robot_setskill(fake: FakeRobotd) -> None:
    fake.set_state(api_version=18)
    rc = main(
        [
            "policy",
            "add",
            "polite-bow",
            "/var/lib/robot/policies/polite-bow.onnx",
            "--hold",
            "5",
            "--command",
            "1,1,0",
            "--socket",
            fake.socket_path,
            "--apply",
        ]
    )
    assert rc == 0
    assert _non_hello(fake) == ["robot.setSkill"]
    assert fake.call_log[-1].params == {
        "name": "polite-bow",
        "path": "/var/lib/robot/policies/polite-bow.onnx",
        "duration": 5.0,
        "command": [1.0, 1.0, 0.0],
    }


def test_policy_add_on_api16_exits_2(fake: FakeRobotd) -> None:
    rc = main(
        [
            "policy",
            "add",
            "polite-bow",
            "/var/lib/robot/policies/polite-bow.onnx",
            "--socket",
            fake.socket_path,
            "--apply",
        ]
    )
    assert rc == EXIT_ENV_ERROR
    assert _non_hello(fake) == []


def test_policy_remove_uses_remove_skill(fake: FakeRobotd) -> None:
    fake.set_state(api_version=18)
    rc = main(["policy", "remove", "polite-bow", "--socket", fake.socket_path, "--apply"])
    assert rc == 0
    assert _non_hello(fake) == ["robot.removeSkill"]
    assert fake.call_log[-1].params == {"name": "polite-bow"}


# ---------------------------------------------------------------------------
# list: robot.policies on API >= 18, robot.subscribe fallback on API 16
# ---------------------------------------------------------------------------


def test_policy_list_uses_robot_policies_on_api18(fake: FakeRobotd) -> None:
    fake.set_state(api_version=18, walk_policy="alpha_walking.onnx", skills=("kick_left",))
    rc = main(["policy", "list", "--socket", fake.socket_path, "--json"])
    assert rc == 0
    assert "robot.policies" in _non_hello(fake)


# ---------------------------------------------------------------------------
# finding 3: a noun-level `--json` (before the verb) must not be discarded by
# the verb's own `--json` default
# ---------------------------------------------------------------------------


def test_noun_level_json_flag_is_not_discarded_by_the_verb(
    fake: FakeRobotd, capsys: pytest.CaptureFixture[str]
) -> None:
    """`microduck-cli policy --json list` must emit JSON: the noun's own
    `--json`, parsed before the verb, used to get clobbered by the verb
    subparser's own `--json` action re-applying its `False` default."""
    fake.set_state(api_version=18, walk_policy="alpha_walking.onnx", skills=("kick_left",))
    rc = main(["policy", "--json", "list", "--socket", fake.socket_path])
    assert rc == 0
    out = capsys.readouterr().out
    payload = json.loads(out)  # raises if text mode (non-JSON) leaked through
    assert payload["source"] == "robot.policies"


def test_verb_level_json_flag_still_wins_over_no_noun_flag(
    fake: FakeRobotd, capsys: pytest.CaptureFixture[str]
) -> None:
    """The existing, already-working direction: `--json` typed on the verb
    itself still emits JSON with no noun-level flag at all."""
    fake.set_state(api_version=18, walk_policy="alpha_walking.onnx", skills=("kick_left",))
    rc = main(["policy", "list", "--socket", fake.socket_path, "--json"])
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["source"] == "robot.policies"


def test_policy_list_falls_back_to_subscribe_on_api16(
    fake: FakeRobotd, capsys: pytest.CaptureFixture[str]
) -> None:
    fake.set_state(skills=("kick_left",))  # default api_version=16
    rc = main(["policy", "list", "--socket", fake.socket_path, "--json"])
    assert rc == 0
    assert "robot.policies" not in _non_hello(fake)
    assert "robot.subscribe" in _non_hello(fake)
    payload = json.loads(capsys.readouterr().out)
    assert payload["source"] == "robot.subscribe"
    assert any(s.get("name") == "kick_left" for s in payload["skills"])


# ---------------------------------------------------------------------------
# search / check / update / pad / install: print the robotctl line, exit 0
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "argv,expected_fragment",
    [
        (["policy", "search", "microduck"], "robotctl policy search microduck"),
        (["policy", "check"], "robotctl policy check"),
        (["policy", "update"], "sudo robotctl policy update"),
        (["policy", "update", "--version", "v1"], "--version v1"),
        (["policy", "pad", "bindings"], "robotctl pad bindings"),
        (["policy", "pad", "bind", "x", "polite-bow"], "sudo robotctl pad bind x polite-bow"),
        (["policy", "pad", "reset"], "sudo robotctl pad reset"),
        (["policy", "pad", "reset", "x"], "sudo robotctl pad reset x"),
        (
            ["policy", "install", "add", "polite-bow", "fffiloni/microduck-polite-bow"],
            "sudo robotctl policy add polite-bow fffiloni/microduck-polite-bow",
        ),
    ],
)
def test_robotctl_line_verbs_exit_zero_and_print_the_line(
    argv: list[str], expected_fragment: str, capsys: pytest.CaptureFixture[str]
) -> None:
    rc = main(argv)
    assert rc == 0
    assert expected_fragment in capsys.readouterr().out


def test_search_never_opens_a_socket(capsys: pytest.CaptureFixture[str]) -> None:
    # No --socket/--duck/--state passed at all, and it still succeeds: these verbs
    # never touch addressing or the client.
    rc = main(["policy", "search", "microduck", "--json"])
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert "updaterd" in payload["note"]


# ---------------------------------------------------------------------------
# train lane: smoke gate, argv table, secrets, artifact ledger
# ---------------------------------------------------------------------------


def test_policy_train_without_smoke_record_exits_1_naming_smoke_command(
    tmp_path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    runner = _RecordingRunner()
    monkeypatch.setattr(policy_mod, "_runner", runner)
    rl_clone = _rl_clone_at_commit(tmp_path, "abc1234")
    rc = main(["policy", "train", TASK, "--state", str(tmp_path), "--rl-clone", rl_clone, "--json"])
    assert rc == EXIT_USER_ERROR
    payload = json.loads(capsys.readouterr().err)
    smoke_argv, _ = lane.smoke(TASK, rl_clone=rl_clone)
    assert " ".join(smoke_argv) in payload["message"]
    assert runner.calls == []


def test_policy_train_without_a_resolvable_rl_clone_exits_2(
    tmp_path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """No ``--rl-clone``, no env knob, and no ``../microduck_rl`` sibling: the
    train verb exits 2 with the 'no microduck_rl clone found' remediation —
    this failure mode is correct and must stay, only the *other* tests here
    (which want a resolvable clone) needed to stop depending on it by luck.
    """
    monkeypatch.setattr("microduck_cli.env.doctor.resolve_clone_paths", lambda env: (None, None))
    monkeypatch.delenv("DUCK_SIM_RL", raising=False)
    monkeypatch.delenv("MICRODUCK_RL_CLONE", raising=False)
    runner = _RecordingRunner()
    monkeypatch.setattr(policy_mod, "_runner", runner)

    rc = main(["policy", "train", TASK, "--state", str(tmp_path), "--json"])

    assert rc == EXIT_ENV_ERROR
    payload = json.loads(capsys.readouterr().err)
    assert "no microduck_rl clone found" in payload["message"]
    assert runner.calls == []


def test_policy_smoke_then_train_records_and_unblocks(
    tmp_path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    runner = _RecordingRunner(returncode=0)
    monkeypatch.setattr(policy_mod, "_runner", runner)
    rl_clone = _rl_clone_at_commit(tmp_path, "abc1234")

    rc = main(["policy", "smoke", TASK, "--state", str(tmp_path), "--rl-clone", rl_clone, "--json"])
    assert rc == 0
    assert runner.calls[0]["argv"] == [
        "uv",
        "run",
        "train",
        TASK,
        "--env.scene.num-envs",
        "64",
        "--agent.max_iterations",
        "5",
    ]
    capsys.readouterr()

    rc = main(["policy", "train", TASK, "--state", str(tmp_path), "--rl-clone", rl_clone, "--json"])
    assert rc == 0
    capsys.readouterr()


def test_policy_train_hf_jobs_argv_matches_scripts_hf_readme_table(
    tmp_path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    runner = _RecordingRunner(returncode=0)
    monkeypatch.setattr(policy_mod, "_runner", runner)
    rl_clone = _rl_clone_at_commit(tmp_path, "abc1234")
    main(["policy", "smoke", TASK, "--state", str(tmp_path), "--rl-clone", rl_clone])
    capsys.readouterr()

    rc = main(
        [
            "policy",
            "train",
            TASK,
            "--state",
            str(tmp_path),
            "--rl-clone",
            rl_clone,
            "--num-envs",
            "4096",
            "--flavor",
            "a100-large",
            "--namespace",
            "pollen-robotics",
            "--timeout",
            "12h",
            "--detach",
            "--hf-jobs",
        ]
    )
    assert rc == 0
    train_call = runner.calls[-1]
    assert train_call["argv"] == [
        "uv",
        "run",
        "train",
        TASK,
        "--env.scene.num-envs",
        "4096",
        "--flavor",
        "a100-large",
        "--namespace",
        "pollen-robotics",
        "--timeout",
        "12h",
        "--detach",
        "--hf-jobs",
    ]


def test_train_lane_never_leaks_secrets_into_argv_or_output(
    tmp_path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    hf_sentinel = "hf_sentinel_abc123"
    wandb_sentinel = "wandb_sentinel_xyz789"
    monkeypatch.setenv("HF_TOKEN", hf_sentinel)
    monkeypatch.setenv("WANDB_API_KEY", wandb_sentinel)

    seen_env: dict[str, str] = {}

    def _capturing_runner(argv, *, cwd=None, env=None):
        seen_env.update(env or {})
        return _Result(returncode=0)

    monkeypatch.setattr(policy_mod, "_runner", _capturing_runner)
    rl_clone = _rl_clone_at_commit(tmp_path, "abc1234")

    main(["policy", "smoke", TASK, "--state", str(tmp_path), "--rl-clone", rl_clone, "--json"])
    smoke_out = capsys.readouterr()
    main(
        [
            "policy",
            "train",
            TASK,
            "--state",
            str(tmp_path),
            "--rl-clone",
            rl_clone,
            "--hf-jobs",
            "--flavor",
            "a100-large",
            "--json",
        ]
    )
    train_out = capsys.readouterr()

    # Secrets DID reach the child via env (never leaked into argv/captured text).
    assert seen_env.get("HF_TOKEN") == hf_sentinel
    assert seen_env.get("WANDB_API_KEY") == wandb_sentinel

    smoke_argv, _ = lane.smoke(TASK, rl_clone=rl_clone)
    train_argv, _ = lane.train(
        TASK,
        hf_jobs=True,
        flavor="a100-large",
        state_dir=str(tmp_path),
        rl_clone=rl_clone,
        force=True,
        reason="x",
    )
    sentinels = {"hf_token": hf_sentinel, "wandb_api_key": wandb_sentinel}
    assert_no_secrets(
        smoke_out.out + smoke_out.err + train_out.out + train_out.err,
        [smoke_argv, train_argv],
        sentinels=sentinels,
    )


def test_policy_publish_records_an_artifact_and_never_writes_config(
    tmp_path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    runner = _RecordingRunner(returncode=0)
    monkeypatch.setattr(policy_mod, "_runner", runner)
    # publish() has no smoke gate — it just needs an rl_clone that resolves,
    # never a real checkout, so a bare directory is enough.
    rl_clone = str(tmp_path / "rl_clone")
    (tmp_path / "rl_clone").mkdir()
    rc = main(
        [
            "policy",
            "publish",
            "--onnx",
            "walk.onnx",
            "--repo",
            "you/microduck-walk",
            "--kind",
            "perpetual",
            "--state",
            str(tmp_path),
            "--rl-clone",
            rl_clone,
            "--json",
        ]
    )
    assert rc == 0
    from microduck_cli.train.artifacts import read_artifacts

    records = read_artifacts(str(tmp_path))
    assert any(r.hf_repo == "you/microduck-walk" for r in records)


def test_policy_install_prints_line_and_never_runs_the_runner(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    runner = _RecordingRunner()
    monkeypatch.setattr(policy_mod, "_runner", runner)
    rc = main(
        [
            "policy",
            "install",
            "load",
            "walk",
            "RemiFabre/microduck-flamingo-cycle",
            "--hold",
            "5",
        ]
    )
    assert rc == 1  # hold is only valid with kind="add" (lane.install_argv's own contract)
    assert runner.calls == []


def test_policy_install_add_with_hold(capsys: pytest.CaptureFixture[str]) -> None:
    rc = main(
        ["policy", "install", "add", "polite-bow", "fffiloni/microduck-polite-bow", "--hold", "5"]
    )
    assert rc == 0
    assert "--hold 5" in capsys.readouterr().out


# ---------------------------------------------------------------------------
# lockstep / rubric surface: every verb takes --json and has a catalog entry
# ---------------------------------------------------------------------------


def _policy_subactions(parser: argparse.ArgumentParser):
    return [a for a in parser._actions if isinstance(a, argparse._SubParsersAction)]


def _all_policy_parsers():
    from microduck_cli.cli import _build_parser

    top = _build_parser()
    policy_action = next(a for a in _policy_subactions(top) if "policy" in a.choices)
    policy_parser = policy_action.choices["policy"]
    found = {("policy",): policy_parser}
    for action in _policy_subactions(policy_parser):
        for name, child in action.choices.items():
            found[("policy", name)] = child
            for sub_action in _policy_subactions(child):
                for sub_name, grandchild in sub_action.choices.items():
                    found[("policy", name, sub_name)] = grandchild
    return found


def test_every_policy_verb_has_a_json_flag():
    for path, parser in _all_policy_parsers().items():
        option_strings = {opt for action in parser._actions for opt in action.option_strings}
        assert "--json" in option_strings, f"{path} has no --json flag"


def test_every_policy_verb_has_a_catalog_entry():
    for path in _all_policy_parsers():
        assert path in ENTRIES, f"{path} has no explain/policy.py ENTRIES entry"


def test_verbs_list_and_entries_agree_on_count_of_documented_paths():
    # Every ENTRIES key is a real, reachable verb path (no orphaned docs).
    paths = set(_all_policy_parsers())
    for path in ENTRIES:
        assert path in paths, f"ENTRIES has a stale path {path} with no registered verb"
    assert len(VERBS) > 0
    assert VERBS  # non-empty; per-path coverage is asserted by test_lockstep.py
