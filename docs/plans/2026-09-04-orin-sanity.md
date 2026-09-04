# Build Plan — orin-sanity

slug: `orin-sanity` · status: `exported` · from frame: `orin-sanity`

> microduck-cli passes the on-box sanity checks on Jetson AGX Orin, recorded as a verification doc beside the Spark one

## Tasks

### t1 — Bring-up: rustup toolchain, clones at the pins, RL venv, env doctor to healthy

- covers: c3, h3, c9, h8
- acceptance:
  - cargo --version reports >= 1.89, installed via rustup
  - microduck clone at 0cd676d6 (sim-remote-io) and `microduck_rl` at 29e887ec (develop), verified by git rev-parse
  - 'microduck env doctor' reports healthy on this box, or every remaining failure is recorded verbatim with its remediation
  - the bare-box before-state (no rust, no clones, `MICRODUCK_CLONE`/`DUCK_SIM_RL` unset) was probed and captured BEFORE installing anything
  - if uv sync fails on the torch wheel, the failure is recorded verbatim and downstream checks run --fake-only, stated as such
  - torch in the RL venv resolves from NVIDIA's JetPack 7 / CUDA 13 Jetson index FIRST (user decision), overriding the pyproject's pytorch-cu129 source via uv config only — the pinned clone's files stay unmodified, and the exact index URL and override mechanism are recorded in the verification record; the cu129 wheel is attempted only as a recorded fallback

### t2 — Preflight: re-probe runtime conditions, verify host class, stop vLLM with user go-ahead

- depends on: t1
- covers: c4, h4, c5, h5, c13, h12
- acceptance:
  - power mode, free memory, and co-tenant processes re-probed at run time and captured for the record header
  - 'microduck env doctor --json' reports `host_class` jetson-agx-orin; a mismatch is surfaced as a classifier defect, never patched mid-sanity
  - diffs of `microduck_cli`/env/hosts.py and `microduck_cli`/env/doctor.py are empty at run end; doctor failures were fixed in the environment only
  - vLLM stopped only after an explicit user go-ahead at that moment; the stop and its time captured for the record

### t3 — Checks 1-4 plus the live suite: fake body first, then MuJoCo sim

- depends on: t2
- covers: c2, c7, h6, c11, h10
- acceptance:
  - each check's output captured unchanged, with the CLI commit named per check
  - env up --sim yields a standing duck; monitor frames pasted verbatim
  - live suite run (`MICRODUCK_LIVE`=1 pytest -m live -n0, plus `MICRODUCK_LIVE_SIM`=1); summary line captured; the walking sentinel reports xfail — an XPASS is reported to the user as news, never absorbed

### t4 — Check 5: attempt the policy smoke under an explicit timeout, record the outcome verbatim

- depends on: t3
- covers: c2
- acceptance:
  - the smoke runs under an explicit timeout; pass, fail, or timeout is recorded verbatim — a torch failure is a finding, not a sanity failure
  - the HF Jobs half is dry-run only; nothing submitted, nothing billed

### t5 — Check 6: the gates on this box

- depends on: t1
- covers: c2
- acceptance:
  - pytest -n auto --cov, black/isort/flake8/bandit, teken cli doctor --strict, and markdownlint outcomes captured verbatim

### t6 — Write the verification record, restart vLLM, open the version-bumped PR

- depends on: t3, t4, t5
- covers: c1, h1, c2, h2, c8, h7, c10, h9, c11
- acceptance:
  - docs/verification/<run-date>-orin-sanity.md exists with a self-contained header table (host, L4T/CUDA, power mode, memory + co-tenants, toolchain, pins, CLI commits per check, daemon API)
  - checks map 1:1 to the Spark record's six; anything not attempted lands in an explicit NOT-verified section
  - vLLM restarted and verified serving; both transitions noted in the record
  - PR opened via the cicd skill with a version bump; CI green

## Risks

- [unknown_nonblocking] check 5 may hang rather than fail cleanly on an incompatible torch/GPU stack — the smoke runs under an explicit timeout (order 15 min; Spark took 59s) and a timeout is itself a recorded outcome (task t4)
- [unknown_nonblocking] the pytorch-cu129 aarch64 torch wheel has never been installed on L4T R39 — uv sync of the RL venv may fail, degrading checks 1-4 to the --fake body only (task t1)
