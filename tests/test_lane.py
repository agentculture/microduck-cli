"""Tests for microduck_cli.train.lane (train lane argv builders, t14).

Fixture task id used throughout: ``Mjlab-Velocity-Flat-MicroDuck`` — the
"main task" from `microduck_rl`'s README.md table at the pinned commit
(``docs/upstream-pins.md``,
``pollen-robotics/microduck_rl@29e887ecfbf5d37144759e5a9f8a176dfb83d547``).
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from microduck_cli.cli._errors import CliError
from microduck_cli.train import artifacts, lane
from tests.test_no_secrets_in_output import assert_no_secrets

TASK = "Mjlab-Velocity-Flat-MicroDuck"
RL_CLONE = "/fixture/microduck_rl"


# ---------------------------------------------------------------------------
# 1. Table test: each builder's argv equals the upstream-documented command.
# ---------------------------------------------------------------------------


def _train_case():
    # README.md quickstart:
    #   uv run train Mjlab-Velocity-Flat-MicroDuck --env.scene.num-envs 4096
    return lane.train(
        TASK,
        num_envs=4096,
        state_dir="/fixture/state",
        rl_clone=RL_CLONE,
        force=True,
        reason="table test",
    ), [
        "uv",
        "run",
        "train",
        TASK,
        "--env.scene.num-envs",
        "4096",
    ]


def _train_resume_case():
    # README.md "Resume from a checkpoint" (minus --agent.run-name, out of
    # this builder's documented signature):
    #   uv run train <TASK> --env.scene.num-envs 4096 \
    #       --agent.load-checkpoint model_29999.pt --agent.resume True
    return lane.train(
        TASK,
        num_envs=4096,
        resume_checkpoint="model_29999.pt",
        state_dir="/fixture/state",
        rl_clone=RL_CLONE,
        force=True,
        reason="table test",
    ), [
        "uv",
        "run",
        "train",
        TASK,
        "--env.scene.num-envs",
        "4096",
        "--agent.load-checkpoint",
        "model_29999.pt",
        "--agent.resume",
        "True",
    ]


def _train_hf_jobs_case():
    # scripts/hf/README.md "Submit a run":
    #   uv run train Mjlab-Kick-Flat-MicroDuck \
    #       --env.scene.num-envs 4096 --agent.max_iterations 4000 --hf-jobs
    return lane.train(
        TASK,
        num_envs=4096,
        max_iterations=4000,
        hf_jobs=True,
        state_dir="/fixture/state",
        rl_clone=RL_CLONE,
        force=True,
        reason="table test",
    ), [
        "uv",
        "run",
        "train",
        TASK,
        "--env.scene.num-envs",
        "4096",
        "--agent.max_iterations",
        "4000",
        "--hf-jobs",
    ]


def _train_hf_jobs_flavor_namespace_timeout_detach_case():
    # scripts/hf/README.md "Useful flags": --namespace, --flavor, --timeout, --detach.
    return lane.train(
        TASK,
        num_envs=4096,
        hf_jobs=True,
        flavor="a100-large",
        namespace="pollen-robotics",
        timeout="12h",
        detach=True,
        state_dir="/fixture/state",
        rl_clone=RL_CLONE,
        force=True,
        reason="table test",
    ), [
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


TABLE = [
    pytest.param(lane.list_envs(rl_clone=RL_CLONE), ["uv", "run", "list-envs"], id="list_envs"),
    pytest.param(
        lane.smoke(TASK, rl_clone=RL_CLONE),
        # AGENTS.md: uv run train <TASK> --env.scene.num-envs 64 --agent.max_iterations 5
        [
            "uv",
            "run",
            "train",
            TASK,
            "--env.scene.num-envs",
            "64",
            "--agent.max_iterations",
            "5",
        ],
        id="smoke",
    ),
    pytest.param(*_train_case(), id="train"),
    pytest.param(*_train_resume_case(), id="train_resume"),
    pytest.param(*_train_hf_jobs_case(), id="train_hf_jobs"),
    pytest.param(*_train_hf_jobs_flavor_namespace_timeout_detach_case(), id="train_hf_jobs_flags"),
    pytest.param(
        # README.md: uv run play <TASK> --wandb-run-path <entity/project/run_id>
        lane.play(TASK, wandb_run_path="entity/project/run_id", rl_clone=RL_CLONE),
        ["uv", "run", "play", TASK, "--wandb-run-path", "entity/project/run_id"],
        id="play",
    ),
    pytest.param(
        lane.play(TASK, rl_clone=RL_CLONE),
        ["uv", "run", "play", TASK],
        id="play_no_wandb",
    ),
    pytest.param(
        # README.md: uv run scripts/export.py <TASK> --wandb-run-path <...>
        lane.export(TASK, wandb_run_path="entity/project/run_id", rl_clone=RL_CLONE),
        ["uv", "run", "scripts/export.py", TASK, "--wandb-run-path", "entity/project/run_id"],
        id="export",
    ),
    pytest.param(
        # README.md publish (from an already-exported ONNX):
        #   uv run publish --onnx output.onnx --repo <user>/microduck-flamingo \
        #       --kind perpetual --unwind-s 1.5
        lane.publish(
            "output.onnx", "user/microduck-flamingo", "perpetual", unwind_s=1.5, rl_clone=RL_CLONE
        ),
        [
            "uv",
            "run",
            "publish",
            "--onnx",
            "output.onnx",
            "--repo",
            "user/microduck-flamingo",
            "--kind",
            "perpetual",
            "--unwind-s",
            "1.5",
        ],
        id="publish_perpetual_unwind",
    ),
    pytest.param(
        # README.md publish, episodic dry-run:
        #   uv run publish --onnx output.onnx --repo <user>/microduck-bow \
        #       --kind episodic --duration-s 4.0 --dry-run
        lane.publish(
            "output.onnx",
            "user/microduck-bow",
            "episodic",
            duration_s=4.0,
            dry_run=True,
            rl_clone=RL_CLONE,
        ),
        [
            "uv",
            "run",
            "publish",
            "--onnx",
            "output.onnx",
            "--repo",
            "user/microduck-bow",
            "--kind",
            "episodic",
            "--duration-s",
            "4.0",
            "--dry-run",
        ],
        id="publish_episodic_dry_run",
    ),
    pytest.param(
        # README.md publish, gait for a slot:
        #   uv run publish --onnx output.onnx --repo <user>/microduck-my-walk \
        #       --kind perpetual --slot walk
        lane.publish(
            "output.onnx", "user/microduck-my-walk", "perpetual", slot="walk", rl_clone=RL_CLONE
        ),
        [
            "uv",
            "run",
            "publish",
            "--onnx",
            "output.onnx",
            "--repo",
            "user/microduck-my-walk",
            "--kind",
            "perpetual",
            "--slot",
            "walk",
        ],
        id="publish_gait_slot",
    ),
    pytest.param(
        # README.md: uv run scripts/infer_policy.py --walking output.onnx
        lane.infer("output.onnx", rl_clone=RL_CLONE),
        ["uv", "run", "scripts/infer_policy.py", "--walking", "output.onnx"],
        id="infer_walking_only",
    ),
    pytest.param(
        # README.md multi-policy invocation (--standing, --sitstand, --roulade, --new-cmd-obs)
        lane.infer(
            "walk.onnx",
            standing="stand.onnx",
            sitstand="sitstand.onnx",
            roulade="roulade.onnx",
            new_cmd_obs=True,
            rl_clone=RL_CLONE,
        ),
        [
            "uv",
            "run",
            "scripts/infer_policy.py",
            "--walking",
            "walk.onnx",
            "--standing",
            "stand.onnx",
            "--sitstand",
            "sitstand.onnx",
            "--roulade",
            "roulade.onnx",
            "--new-cmd-obs",
        ],
        id="infer_multi_policy",
    ),
]


@pytest.mark.parametrize("built,expected_argv", TABLE)
def test_builder_argv_matches_upstream_docs(built, expected_argv):
    argv, cwd = built
    assert argv == expected_argv
    assert cwd == RL_CLONE


def test_install_argv_episodic_add():
    # README.md: sudo robotctl policy add polite-bow <user>/microduck-polite-bow
    line = lane.install_argv("add", "polite-bow", "user/microduck-polite-bow")
    assert line == "sudo robotctl policy add polite-bow user/microduck-polite-bow"


def test_install_argv_held_pose_with_hold():
    # README.md: sudo robotctl policy add flamingo <user>/microduck-flamingo --hold 5
    line = lane.install_argv("add", "flamingo", "user/microduck-flamingo", hold=5)
    assert line == "sudo robotctl policy add flamingo user/microduck-flamingo --hold 5"


def test_install_argv_gait_load():
    # README.md: sudo robotctl policy load walk <user>/microduck-my-walk
    line = lane.install_argv("load", "walk", "user/microduck-my-walk")
    assert line == "sudo robotctl policy load walk user/microduck-my-walk"


def test_install_argv_rejects_hold_with_load():
    with pytest.raises(CliError):
        lane.install_argv("load", "walk", "user/microduck-my-walk", hold=5)


def test_install_argv_rejects_unknown_kind():
    with pytest.raises(CliError):
        lane.install_argv("delete", "walk", "user/microduck-my-walk")


# ---------------------------------------------------------------------------
# 2. train() without a recorded smoke pass refuses, naming the smoke command.
# ---------------------------------------------------------------------------


def test_train_without_smoke_record_refuses_naming_smoke_command(tmp_path):
    state_dir = tmp_path / "state"

    with pytest.raises(CliError) as excinfo:
        lane.train(TASK, num_envs=4096, state_dir=state_dir, rl_clone=RL_CLONE)

    err = excinfo.value
    assert err.code == 1

    smoke_argv, _ = lane.smoke(TASK, rl_clone=RL_CLONE)
    smoke_cmd = " ".join(smoke_argv)
    assert smoke_cmd in err.message
    expected_smoke_cmd = (
        "uv run train Mjlab-Velocity-Flat-MicroDuck "
        "--env.scene.num-envs 64 --agent.max_iterations 5"
    )
    assert smoke_cmd == expected_smoke_cmd


def test_train_succeeds_after_recorded_smoke_pass(tmp_path):
    state_dir = tmp_path / "state"
    lane.record_smoke_pass(state_dir, TASK, commit="abc1234")

    argv, cwd = lane.train(TASK, num_envs=4096, state_dir=state_dir, rl_clone=RL_CLONE)

    assert argv == ["uv", "run", "train", TASK, "--env.scene.num-envs", "4096"]
    assert cwd == RL_CLONE


def test_train_smoke_record_is_keyed_by_task_id(tmp_path):
    state_dir = tmp_path / "state"
    lane.record_smoke_pass(state_dir, "Mjlab-StandUp-Flat-MicroDuck", commit="abc1234")

    # A pass recorded for a different task id does not satisfy this task's gate.
    with pytest.raises(CliError):
        lane.train(TASK, num_envs=4096, state_dir=state_dir, rl_clone=RL_CLONE)


def test_train_without_smoke_record_and_without_force_reason_still_refuses(tmp_path):
    state_dir = tmp_path / "state"
    with pytest.raises(CliError):
        lane.train(TASK, num_envs=4096, state_dir=state_dir, rl_clone=RL_CLONE, force=True)


def test_train_force_with_reason_bypasses_gate(tmp_path):
    state_dir = tmp_path / "state"
    argv, cwd = lane.train(
        TASK,
        num_envs=4096,
        state_dir=state_dir,
        rl_clone=RL_CLONE,
        force=True,
        reason="known-good config",
    )
    assert argv == ["uv", "run", "train", TASK, "--env.scene.num-envs", "4096"]


def test_train_of_the_exact_smoke_argv_needs_no_gate(tmp_path):
    state_dir = tmp_path / "state"
    argv, cwd = lane.train(
        TASK, num_envs=64, max_iterations=5, state_dir=state_dir, rl_clone=RL_CLONE
    )
    assert argv == [
        "uv",
        "run",
        "train",
        TASK,
        "--env.scene.num-envs",
        "64",
        "--agent.max_iterations",
        "5",
    ]


def test_smoke_record_roundtrip(tmp_path):
    state_dir = tmp_path / "state"
    recorded = lane.record_smoke_pass(
        state_dir, TASK, commit="deadbeef", timestamp="2026-09-03T00:00:00+00:00"
    )
    loaded = lane.load_smoke_record(state_dir, TASK)
    assert loaded == recorded
    assert loaded.task_id == TASK
    assert loaded.commit == "deadbeef"


def test_smoke_record_missing_returns_none(tmp_path):
    state_dir = tmp_path / "state"
    assert lane.load_smoke_record(state_dir, TASK) is None


# ---------------------------------------------------------------------------
# 3. assert_no_secrets over every argv the lane builds, secrets in env only.
# ---------------------------------------------------------------------------


def test_no_secrets_leak_into_any_built_argv(tmp_path):
    sentinels = {
        "HF_TOKEN": "hf_sentinel_token_do_not_leak",
        "WANDB_API_KEY": "wandb_sentinel_key_do_not_leak",
    }
    fake_env = {**sentinels, "PATH": "/usr/bin"}
    state_dir = tmp_path / "state"
    lane.record_smoke_pass(state_dir, TASK, commit="abc1234")

    calls = []

    def fake_runner(argv, cwd, env):
        calls.append((list(argv), cwd, dict(env)))
        return 0

    built = [
        lane.list_envs(rl_clone=RL_CLONE),
        lane.smoke(TASK, rl_clone=RL_CLONE),
        lane.train(TASK, num_envs=4096, state_dir=state_dir, rl_clone=RL_CLONE),
        lane.train(
            TASK,
            num_envs=4096,
            hf_jobs=True,
            flavor="a100-large",
            namespace="pollen-robotics",
            timeout="12h",
            detach=True,
            state_dir=state_dir,
            rl_clone=RL_CLONE,
        ),
        lane.play(TASK, wandb_run_path="entity/project/run_id", rl_clone=RL_CLONE),
        lane.export(TASK, wandb_run_path="entity/project/run_id", rl_clone=RL_CLONE),
        lane.publish(
            "output.onnx", "user/microduck-bow", "episodic", duration_s=4.0, rl_clone=RL_CLONE
        ),
        lane.infer("output.onnx", rl_clone=RL_CLONE),
    ]

    argv_log = []
    captured_chunks = []
    for argv, cwd in built:
        lane.run(argv, fake_env, fake_runner, cwd=cwd)
        argv_log.append(argv)
        captured_chunks.append(" ".join(argv))

    install_line = lane.install_argv("add", "polite-bow", "user/microduck-polite-bow")
    captured_chunks.append(install_line)

    assert len(calls) == len(built)
    for _, _, env_seen in calls:
        assert env_seen["HF_TOKEN"] == sentinels["HF_TOKEN"]
        assert env_seen["WANDB_API_KEY"] == sentinels["WANDB_API_KEY"]

    assert_no_secrets("\n".join(captured_chunks), argv_log, sentinels=sentinels)


# ---------------------------------------------------------------------------
# artifacts.py — append-only ledger of what the lane produced.
# ---------------------------------------------------------------------------


def test_append_artifact_roundtrip(tmp_path):
    state_dir = tmp_path / "state"
    record = artifacts.append_artifact(
        state_dir,
        TASK,
        run_path="entity/project/run_id",
        checkpoint="model_3000.pt",
        onnx_path="output.onnx",
        hf_repo="user/microduck-velocity",
        timestamp="2026-09-03T00:00:00+00:00",
    )

    loaded = artifacts.read_artifacts(state_dir)
    assert loaded == [record]
    assert record.task_id == TASK
    assert record.hf_repo == "user/microduck-velocity"


def test_append_artifact_is_append_only(tmp_path):
    state_dir = tmp_path / "state"
    first = artifacts.append_artifact(state_dir, TASK, run_path="run1")
    second = artifacts.append_artifact(state_dir, TASK, run_path="run2")
    other_task = artifacts.append_artifact(
        state_dir, "Mjlab-StandUp-Flat-MicroDuck", run_path="run3"
    )

    all_records = artifacts.read_artifacts(state_dir)
    assert all_records == [first, second, other_task]

    task_only = artifacts.read_artifacts(state_dir, task_id=TASK)
    assert task_only == [first, second]


def test_read_artifacts_missing_ledger_returns_empty_list(tmp_path):
    state_dir = tmp_path / "state"
    assert artifacts.read_artifacts(state_dir) == []


def test_read_artifacts_skips_malformed_lines(tmp_path):
    state_dir = tmp_path / "state"
    artifacts.append_artifact(state_dir, TASK, run_path="run1")
    ledger = state_dir / "train" / "artifacts.jsonl"
    with ledger.open("a", encoding="utf-8") as handle:
        handle.write("not json\n")
        handle.write("\n")

    records = artifacts.read_artifacts(state_dir)
    assert len(records) == 1
    assert records[0].run_path == "run1"


# ---------------------------------------------------------------------------
# 4. Nothing under microduck_cli/ imports the RL runtime.
# ---------------------------------------------------------------------------

_FORBIDDEN_TOP_LEVEL_MODULES = {"mjlab_microduck", "mjlab", "torch", "warp"}


def _iter_python_files() -> list[Path]:
    root = Path(__file__).resolve().parent.parent / "microduck_cli"
    return sorted(root.rglob("*.py"))


def _imported_top_level_modules(source: str) -> set[str]:
    tree = ast.parse(source)
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                found.add(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            if node.module and node.level == 0:
                found.add(node.module.split(".")[0])
    return found


def test_no_rl_runtime_imports():
    offenders = {}
    for path in _iter_python_files():
        source = path.read_text(encoding="utf-8")
        modules = _imported_top_level_modules(source)
        hit = modules & _FORBIDDEN_TOP_LEVEL_MODULES
        if hit:
            offenders[str(path)] = hit

    assert not offenders, f"forbidden RL-runtime imports found: {offenders}"
