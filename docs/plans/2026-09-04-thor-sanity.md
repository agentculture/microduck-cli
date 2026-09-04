# Build Plan — thor-sanity

slug: `thor-sanity` · status: `exported` · from frame: `thor-sanity`

> microduck-cli passes its on-box sanity on Jetson AGX Thor: the same six checks recorded for DGX Spark in docs/verification/2026-09-04-sim-bringup.md run headless on Thor at the same upstream pins, the result is recorded verbatim in a second verification record, and env doctor's jetson-thor 'training path unverified' remediation is either retired or kept with evidence

## Tasks

### t1 — Provision Thor for tiers 1-3 (outside the repo): capture the pre-provisioning 'env doctor --json' output, install rustup (cargo >= 1.89), clone microduck at 0cd676d and `microduck_rl` at 29e887e under ~/git beside this repo, cargo build -p robotd -p robotctl -p tof -p sounds natively

- covers: c15, h12
- acceptance:
  - The pre-provisioning env doctor --json output (7 provisioning errors, `host_class` jetson-thor) is saved verbatim to the scratchpad for the record
  - target/debug/robotd, robotctl, tofd and sounds exist in the microduck clone and 'git rev-parse HEAD' in each clone equals its pinned commit
  - env doctor reports `cargo_version`, `daemons_built`, both clone and both pinned-commit checks passed; only `rl_venv_with_onnxruntime` may still fail

### t2 — Sync the RL venv Jetson-index first: write a LOCAL uncommitted \[tool.uv.sources\]/\[\[tool.uv.index\]\] override in the `microduck_rl` clone routing torch (and warp if needed) to pypi.jetson-ai-lab.io/jp7/cu130, run `UV_HTTP_TIMEOUT`=600 uv sync, probe torch.cuda.`is_available`() and warp.init(); on failure revert the override and sync upstream's cu129 routing as the recorded second attempt

- depends on: t1
- acceptance:
  - The override text and the exact 'uv sync' resolution lines for torch and warp (version, wheel tag, index) are saved verbatim for the record, per attempt
  - python -c 'import torch, warp; print(torch.`__version__`, torch.version.cuda, torch.cuda.`is_available`()); warp.init()' output is saved per attempt, with the first exception verbatim on failure
  - env doctor on Thor reports healthy (13/13) after whichever attempt succeeded; if neither did, the doctor output is saved and tiers 2-3 are recorded as blocked by it

### t3 — Tier 1 - fake body: run `MICRODUCK_LIVE`=1 uv run pytest -m live -n0 -v tests/live with `MICRODUCK_CLONE` set, on Thor

- depends on: t1
- covers: c4, h2
- acceptance:
  - The pytest summary line (N passed / xfailed / failed) and the CLI commit hash are saved verbatim for the record
  - No robotd process and no socket under the live suite's temp state dir remain afterwards (pgrep robotd empty)

### t4 — Tier 2 - MuJoCo body headless: with lobes lowered by the operator and 'free -g' captured immediately before, run `MICRODUCK_LIVE`=1 `MICRODUCK_LIVE_BODY`=sim `MICRODUCK_LIVE_SIM`=1 uv run pytest -m live -n0 -v tests/live on Thor

- depends on: t2, t3
- covers: c3, h1
- acceptance:
  - The 'free -g' line before the run and the pytest summary line (including the walking xfail's state) are saved verbatim with the commit hash
  - A stand-up check shows policy stand and a trunk height consistent with the Spark record, or the failure is saved verbatim; the walking sentinel's xfail/XPASS state is noted

### t5 — Tier 3 - the train smoke under a wall-clock bound: 'free -g' captured, then 'timeout 30m' (or the operator's bound) around `WANDB_MODE`=offline microduck policy smoke Mjlab-Velocity-Flat-MicroDuck --json on the venv t2 produced; if the Jetson-index venv fails here, repeat once on the cu129 venv

- depends on: t2, t4
- covers: c22, h16
- acceptance:
  - The smoke's argv, ok/failure line, elapsed wall-clock time and, on failure, the first exception with the library that raised it (warp/nvrtc, torch, mjlab) are saved verbatim per attempt
  - A timeout is recorded as 'timed out after N min', never as a pass or an unexplained failure
  - The HF Jobs --dry-run half of check 5 is attempted and its outcome (including any missing hf CLI or login) is saved verbatim

### t6 — Write docs/verification/2026-09-04-thor-sanity.md in the Spark record's shape (box-and-pins table, one section per check in the Spark order, verbatim outputs, tier-3 attempts as separate subsections, a 'What was NOT verified' section naming plan risk t8/t15 and which way it resolved), update the Spark record's 'Jetson Thor / AGX Orin training - not attempted' bullet to link it, and grep the record for secrets before commit

- depends on: t3, t4, t5
- covers: c1, h6, c5, h3, c14, h11, c16, h13, c17, h14, c23, h18
- acceptance:
  - The record has the six Spark headings, each ending in Pass or a verbatim failure, and one pass/fail line per tier; a failing tier 3 sits beside passing tiers 1-2
  - grep -E '`hf_`\[A-Za-z0-9\]{20,}|`WANDB_API_KEY`=' docs/verification/2026-09-04-thor-sanity.md returns nothing; env doctor lines appear only in set/unset form
  - docs/verification/2026-09-04-sim-bringup.md's Thor bullet links to the new record; markdownlint-cli2 passes on both files

### t7 — Adjust env/hosts.py's jetson-thor remediation and docstring to the tier-3 outcome (retire the unverified text with tests/`test_hosts.py` updated if the smoke passed on either attempt, or keep it and cite the record if it failed), touching nothing else in the module

- depends on: t5
- covers: c6, h4, c8, h8
- acceptance:
  - git diff `microduck_cli`/env/hosts.py shows changes only in `_UNVERIFIED_REMEDIATION` / the module docstring; classify() and `_classify`() are byte-identical
  - uv run pytest tests/`test_hosts.py` passes and 'microduck env doctor --json' on Thor prints a `host_class` remediation consistent with the record

### t8 — Bump the version (version-bump skill), add the CHANGELOG entry naming the Thor record and the remediation outcome, run the six gates (teken cli doctor --strict, black, isort, flake8, bandit, pytest -n auto --cov, markdownlint), verify the untouched-file boundaries, and open the PR via the cicd skill

- depends on: t6, t7
- covers: c18, h19, c7, h7, c9, h9, c10, h10
- acceptance:
  - git diff origin/main --stat shows no change to docs/upstream-pins.md, .claude/skills/operate-microduck/SKILL.md or .github/workflows/tests.yml
  - pyproject.toml version differs from origin/main and CHANGELOG.md has a matching dated entry
  - All six gate commands exit 0 and their summary lines are pasted into the record's check-6 section

## Risks

- [unknown_nonblocking] Neither the Jetson-index nor the cu129 venv may initialise warp on compute capability 11.0 (CUDA 12.9 predates `sm_110`; the JetPack 7 index's torch/warp versions are unread from here) - t5 then records a double failure and t7 keeps the unverified text; the plan still converges because a recorded failure is a valid tier-3 outcome (task t5)
- [unknown_nonblocking] The JetPack 7 index may resolve a torch other than upstream's ==2.9.1 pin, forcing the local override to relax the pin; mjlab 1.3.0 / `rsl_rl` compatibility with that torch is unknown until sync (task t2)
- [follow_up] Tiers 1-5 are on-box operator steps on a shared machine (lobes lowered, self-hosted runner present); they are serial by nature and should not be fanned out to parallel agents - only t6/t7 are file-disjoint repo edits
- [unknown_nonblocking] The hf CLI is absent and no HF session exists on Thor; the --dry-run half of check 5 may need 'uv tool install `huggingface_hub`' or a login, decided at t5 (task t5)
