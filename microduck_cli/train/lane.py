"""Argv builders for the upstream `microduck_rl` train/play/export/publish tooling.

Every public function here returns *data* — a `list[str]` argv (plus the cwd
the RL clone should be run from) — and never executes anything itself. The
commands are transcribed verbatim from `microduck_rl`'s README.md, AGENTS.md
and scripts/hf/README.md at the pinned commit
(`docs/upstream-pins.md`, `pollen-robotics/microduck_rl@
29e887ecfbf5d37144759e5a9f8a176dfb83d547`); flag spellings must match those
docs exactly.

**Never import `mjlab_microduck`, `mjlab`, `torch` or `warp` here (or
anywhere under `microduck_cli/`).** This module only builds subprocess argv
for a `uv run ...` invocation into the RL clone; it never imports the RL
repo's own packages. `tests/test_lane.py::test_no_rl_runtime_imports` guards
this with a grep over the source tree.

## The smoke gate

`microduck_rl`'s own AGENTS.md is explicit: "A 5-iteration smoke test at 64
envs catches ~95% of config errors for cents. Never launch a long run without
one." `train()` enforces that here: it refuses to build argv for anything
other than the exact smoke-test invocation (`smoke(task_id)`) unless a
`SmokeRecord` for that task id has been recorded under the injected state
dir (`record_smoke_pass`), or the caller passes `force=True` with a stated
`reason` for bypassing the gate.

## Secrets

`HF_TOKEN` and a wandb API key are never placed on argv — `--wandb-run-path`
identifies a *run*, not a credential, and it is only ever added when the
caller supplies it. Secrets reach the child process only through the `env`
mapping passed to :func:`run`.
"""

from __future__ import annotations

import json
import os
from collections.abc import Callable, Mapping
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from microduck_cli.cli._errors import EXIT_ENV_ERROR, EXIT_USER_ERROR, CliError

# Environment variable used to locate the `microduck_rl` clone when a caller
# does not pass `rl_clone` explicitly. `microduck_cli/env/doctor.py` (t15)
# owns actually verifying the clone exists at the pinned commit; this module
# only needs *a path* to set as the child process's cwd.
RL_CLONE_ENV_VAR = "MICRODUCK_RL_CLONE"
_DEFAULT_RL_CLONE = "microduck_rl"

# Upstream pages every remediation about the smoke gate or `robotctl policy`
# install line points at (docs/upstream-pins.md pins the exact commit).
_RL_AGENTS_URL = "https://github.com/pollen-robotics/microduck_rl/blob/develop/AGENTS.md"
_CHEATSHEET_URL = "https://github.com/pollen-robotics/microduck/blob/main/docs/robot/cheatsheet.md"

_SMOKE_NUM_ENVS = "64"
_SMOKE_MAX_ITERATIONS = "5"


def _resolve_rl_clone(
    rl_clone: str | os.PathLike[str] | None, env: Mapping[str, str] | None = None
) -> str:
    if rl_clone is not None:
        return str(rl_clone)
    env = env if env is not None else os.environ
    # Upstream's own knob first (scripts/duck-sim uses DUCK_SIM_RL), then this
    # CLI's, then the sibling checkout beside this repo — the same lookup
    # `env doctor` reports, so the lane and the doctor never disagree.
    from microduck_cli.env.doctor import resolve_clone_paths

    _, resolved = resolve_clone_paths(env)
    if resolved is not None:
        return resolved
    explicit = env.get(RL_CLONE_ENV_VAR) or env.get("DUCK_SIM_RL")
    if explicit:
        return explicit  # named but absent: the runner reports the missing directory
    raise CliError(
        code=EXIT_ENV_ERROR,
        message="no microduck_rl clone found (DUCK_SIM_RL / MICRODUCK_RL_CLONE unset and "
        f"no ../{_DEFAULT_RL_CLONE} beside this repo)",
        remediation="clone https://github.com/pollen-robotics/microduck_rl at the pinned "
        "commit (docs/upstream-pins.md), run `uv sync` there, then set DUCK_SIM_RL to it — "
        "see https://github.com/pollen-robotics/microduck_rl#quickstart",
    )


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# --------------------------------------------------------------------------
# Argv builders
# --------------------------------------------------------------------------


def list_envs(*, rl_clone: str | os.PathLike[str] | None = None) -> tuple[list[str], str]:
    """`uv run list-envs` — the live task registry (AGENTS.md)."""
    return ["uv", "run", "list-envs"], _resolve_rl_clone(rl_clone)


def smoke(task_id: str, *, rl_clone: str | os.PathLike[str] | None = None) -> tuple[list[str], str]:
    """The mandatory smoke test (AGENTS.md): 64 envs, 5 iterations.

    uv run train <TASK_ID> --env.scene.num-envs 64 --agent.max_iterations 5
    """
    argv = [
        "uv",
        "run",
        "train",
        task_id,
        "--env.scene.num-envs",
        _SMOKE_NUM_ENVS,
        "--agent.max_iterations",
        _SMOKE_MAX_ITERATIONS,
    ]
    return argv, _resolve_rl_clone(rl_clone)


def _build_train_argv(
    task_id: str,
    num_envs: int,
    max_iterations: int | None,
    hf_jobs: bool,
    flavor: str | None,
    namespace: str | None,
    timeout: str | None,
    detach: bool,
    resume_checkpoint: str | None,
) -> list[str]:
    argv = ["uv", "run", "train", task_id, "--env.scene.num-envs", str(num_envs)]
    if max_iterations is not None:
        argv += ["--agent.max_iterations", str(max_iterations)]
    if resume_checkpoint is not None:
        # README "Resume from a checkpoint": --agent.load-checkpoint <file> --agent.resume True
        argv += ["--agent.load-checkpoint", str(resume_checkpoint), "--agent.resume", "True"]
    if flavor is not None:
        argv += ["--flavor", str(flavor)]
    if namespace is not None:
        argv += ["--namespace", str(namespace)]
    if timeout is not None:
        argv += ["--timeout", str(timeout)]
    if detach:
        argv.append("--detach")
    if hf_jobs:
        # scripts/hf/README.md: --hf-jobs is appended to the normal train command.
        argv.append("--hf-jobs")
    return argv


def train(
    task_id: str,
    num_envs: int = 4096,
    max_iterations: int | None = None,
    hf_jobs: bool = False,
    flavor: str | None = None,
    namespace: str | None = None,
    timeout: str | None = None,
    detach: bool = False,
    resume_checkpoint: str | None = None,
    *,
    rl_clone: str | os.PathLike[str] | None = None,
    state_dir: str | os.PathLike[str],
    force: bool = False,
    reason: str | None = None,
) -> tuple[list[str], str]:
    """Build argv for a (possibly long) training run.

        uv run train <TASK_ID> --env.scene.num-envs <N> [--agent.max_iterations <N>]
        uv run train <TASK_ID> ... --agent.load-checkpoint <file> --agent.resume True
        uv run train <TASK_ID> ... [--flavor F] [--namespace NS] [--timeout T] [--detach] --hf-jobs

    Refuses (raises :class:`CliError`, exit 1) to build anything other than
    the exact smoke-test argv (see :func:`smoke`) unless a `SmokeRecord` for
    `task_id` has already been recorded under `state_dir`, or the caller
    passes `force=True` with a `reason` stating why the gate is bypassed.
    """
    cwd = _resolve_rl_clone(rl_clone)
    argv = _build_train_argv(
        task_id,
        num_envs,
        max_iterations,
        hf_jobs,
        flavor,
        namespace,
        timeout,
        detach,
        resume_checkpoint,
    )

    smoke_argv, _ = smoke(task_id, rl_clone=rl_clone)
    if argv == smoke_argv:
        # This call literally builds the smoke-test invocation itself; the
        # gate exists to stop a *long* run, not the smoke test.
        return argv, cwd

    record = load_smoke_record(state_dir, task_id)
    smoke_cmd = " ".join(smoke_argv)
    if record is None:
        if not force:
            raise CliError(
                EXIT_USER_ERROR,
                f"no recorded smoke pass for task {task_id!r}; "
                f"run the smoke test first: {smoke_cmd}",
                remediation=(
                    f"run `{smoke_cmd}` (microduck_rl AGENTS.md: 'never launch a long run "
                    "without one'), or pass force=True with a reason to bypass this gate — see "
                    f"{_RL_AGENTS_URL}"
                ),
            )
        if not reason:
            raise CliError(
                EXIT_USER_ERROR,
                (
                    "force=True bypasses the smoke gate but needs a stated reason "
                    f"(no recorded smoke pass for task {task_id!r}; smoke command: {smoke_cmd})"
                ),
                remediation=(
                    "pass reason=<why> explaining why the smoke gate is being bypassed — see "
                    f"{_RL_AGENTS_URL}"
                ),
            )

    return argv, cwd


def play(
    task_id: str,
    wandb_run_path: str | None = None,
    checkpoint_path: str | None = None,
    *,
    rl_clone: str | os.PathLike[str] | None = None,
) -> tuple[list[str], str]:
    """`uv run play <TASK_ID> --wandb-run-path <entity/project/run_id>` (README)."""
    argv = ["uv", "run", "play", task_id]
    if wandb_run_path is not None:
        argv += ["--wandb-run-path", str(wandb_run_path)]
    if checkpoint_path is not None:
        argv += ["--checkpoint-file", str(checkpoint_path)]
    return argv, _resolve_rl_clone(rl_clone)


def export(
    task_id: str,
    wandb_run_path: str | None = None,
    checkpoint_path: str | None = None,
    *,
    rl_clone: str | os.PathLike[str] | None = None,
) -> tuple[list[str], str]:
    """`uv run scripts/export.py <TASK_ID> --wandb-run-path <...>` (README)."""
    argv = ["uv", "run", "scripts/export.py", task_id]
    if wandb_run_path is not None:
        argv += ["--wandb-run-path", str(wandb_run_path)]
    if checkpoint_path is not None:
        argv += ["--checkpoint-file", str(checkpoint_path)]
    return argv, _resolve_rl_clone(rl_clone)


def publish(
    onnx: str,
    repo: str,
    kind: str,
    duration_s: float | None = None,
    slot: str | None = None,
    unwind_s: float | None = None,
    dry_run: bool = False,
    force: bool = False,
    *,
    rl_clone: str | os.PathLike[str] | None = None,
) -> tuple[list[str], str]:
    """`uv run publish --onnx <...> --repo <...> --kind <episodic|perpetual> ...` (README)."""
    argv = ["uv", "run", "publish", "--onnx", str(onnx), "--repo", str(repo), "--kind", str(kind)]
    if duration_s is not None:
        argv += ["--duration-s", str(duration_s)]
    if slot is not None:
        argv += ["--slot", str(slot)]
    if unwind_s is not None:
        argv += ["--unwind-s", str(unwind_s)]
    if force:
        argv.append("--force")
    if dry_run:
        argv.append("--dry-run")
    return argv, _resolve_rl_clone(rl_clone)


def infer(
    walking_onnx: str, *, rl_clone: str | os.PathLike[str] | None = None, **others: Any
) -> tuple[list[str], str]:
    """`uv run scripts/infer_policy.py --walking <onnx> [...]` (README).

    `others` maps additional `infer_policy.py` flags by their python-ish name
    (`standing`, `sitstand`, `new_cmd_obs`, ...) to a value: `True` adds a
    bare `--flag` (store_true flags), `None`/`False` omits it, anything else
    is stringified as `--flag <value>`.
    """
    argv = ["uv", "run", "scripts/infer_policy.py", "--walking", str(walking_onnx)]
    for key, value in others.items():
        flag = "--" + key.replace("_", "-")
        if value is None or value is False:
            continue
        if value is True:
            argv.append(flag)
        else:
            argv += [flag, str(value)]
    return argv, _resolve_rl_clone(rl_clone)


def install_argv(kind: str, name: str, repo: str, hold: int | None = None) -> str:
    """The `robotctl policy add|load` line a human runs on the duck (README).

    `kind` is `"add"` (episodic, or a held perpetual pose — optionally with
    `hold` seconds) or `"load"` (a perpetual gait installed into a named
    slot). Returned as text: this lane never executes `robotctl` itself —
    that IPC path belongs to the `policy` noun (t5).
    """
    if kind not in ("add", "load"):
        raise CliError(
            EXIT_USER_ERROR,
            f"install_argv: kind must be 'add' or 'load', got {kind!r}",
            remediation=(
                "use 'add' for an episodic/held policy, 'load' for a gait slot — see "
                f"{_CHEATSHEET_URL}"
            ),
        )
    line = f"sudo robotctl policy {kind} {name} {repo}"
    if hold is not None:
        if kind != "add":
            raise CliError(
                EXIT_USER_ERROR,
                "install_argv: hold is only valid with kind='add' (a held pose)",
                remediation=(
                    f"drop hold, or use kind='add' for a held-pose policy — see {_CHEATSHEET_URL}"
                ),
            )
        line += f" --hold {hold}"
    return line


# --------------------------------------------------------------------------
# Execution seam
# --------------------------------------------------------------------------


def run(
    argv: list[str],
    env: Mapping[str, str],
    runner: Callable[..., Any],
    *,
    cwd: str | os.PathLike[str] | None = None,
) -> Any:
    """Thin execution seam: hand `argv` to an injected `runner`.

    Never called by this module's own tests — `runner` is always a fake in
    tests, and the real subprocess runner is wired by the `policy` noun
    (t16/t17). `env` is passed through unmodified so secrets (HF_TOKEN,
    a wandb key) reach the child only via this mapping, never via `argv`.
    """
    return runner(argv, cwd=str(cwd) if cwd is not None else None, env=dict(env))


# --------------------------------------------------------------------------
# Smoke-gate record
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class SmokeRecord:
    """A recorded smoke-test pass for one task id."""

    task_id: str
    commit: str
    timestamp: str


def _smoke_record_path(state_dir: str | os.PathLike[str], task_id: str) -> Path:
    safe = task_id.replace("/", "_").replace(os.sep, "_")
    return Path(state_dir) / "train" / "smoke" / f"{safe}.json"


def record_smoke_pass(
    state_dir: str | os.PathLike[str],
    task_id: str,
    commit: str,
    *,
    timestamp: str | None = None,
) -> SmokeRecord:
    """Record that the smoke test passed for `task_id`, keyed by task id."""
    record = SmokeRecord(task_id=task_id, commit=commit, timestamp=timestamp or _now_iso())
    path = _smoke_record_path(state_dir, task_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(asdict(record), ensure_ascii=False), encoding="utf-8")
    return record


def load_smoke_record(state_dir: str | os.PathLike[str], task_id: str) -> SmokeRecord | None:
    """Load the recorded smoke pass for `task_id`, or `None` if absent/unreadable."""
    path = _smoke_record_path(state_dir, task_id)
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return SmokeRecord(
            task_id=str(data["task_id"]),
            commit=str(data["commit"]),
            timestamp=str(data["timestamp"]),
        )
    except (OSError, ValueError, KeyError, TypeError):
        return None
