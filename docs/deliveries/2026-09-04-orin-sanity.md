# Delivery Summary — orin-sanity

plan: `orin-sanity` · run: `complete` · date: `2026-09-04`
baseline: `devague summary skeleton`

## Intent

Re-run the Spark sanity's six success-signal checks on a Jetson AGX Orin —
the host the Spark record's "What was NOT verified" named as not attempted —
and land the sibling verification record via a version-bumped PR. Executed
from the challenged spec `docs/specs/2026-09-04-orin-sanity.md` through the
confirmed six-task plan `docs/plans/2026-09-04-orin-sanity.md`, with the
approved implementation split: main agent serial for the box-mutating tasks,
one subagent worktree for the gates.

## Planned Work

Quoted verbatim from the `devague summary` skeleton:

- `t1` — Bring-up: rustup toolchain, clones at the pins, RL venv, env doctor to healthy
- `t2` — Preflight: re-probe runtime conditions, verify host class, stop vLLM with user go-ahead
- `t3` — Checks 1-4 plus the live suite: fake body first, then MuJoCo sim
- `t4` — Check 5: attempt the policy smoke under an explicit timeout, record the outcome verbatim
- `t5` — Check 6: the gates on this box
- `t6` — Write the verification record, restart vLLM, open the version-bumped PR

## Actual Delivery

| Plan task | Status | What actually landed |
|-----------|--------|----------------------|
| `t1` | delivered | rustup → cargo 1.98.1; both clones at the exact pins (`0cd676d6`, `29e887ec`); daemons built; RL venv per the Orin recipe (Jetson SBSA cu130 torch + venv-local nvidia/nvpl libs); `env doctor` healthy 13/13; before-state probed and captured first |
| `t2` | delivered | Runtime conditions re-probed (MAXN, memory, co-tenants); live `host_class: jetson-agx-orin` matches the untouched classifier; vLLM stopped at 09:11:49 on the operator's explicit go-ahead (53 Gi → 2.8 Gi used) |
| `t3` | delivered | Fake-body pass (all verbs, over-limit refusal verbatim, engine 300 ticks at 49.9995 Hz); sim checks 1–4 pass with values matching Spark to 3–4 decimals; live suite 12 passed, walking sentinel xfailed |
| `t4` | delivered | Smoke attempted under a 900 s timeout; failed cleanly in 37 s with the sm_87 finding recorded verbatim (see Drift for the HF half) |
| `t5` | delivered | Sonnet subagent in a disposable worktree: 1098 tests / 92.80 % coverage, teken 26/26 strict, lint stack clean; markdownlint recorded as not installed on the box |
| `t6` | delivered | `docs/verification/2026-09-04-orin-sanity.md` on PR #6 at 0.9.2; vLLM restarted and healthy; clones restored clean; CI green after one MD014 fix commit |

## Mid-work Decisions

No `/deviate` records were filed — no divergence rose to a plan-contract
change. Decisions made in-flight, captured here directly:

- The operator directed mid-run that the RL venv's torch come from NVIDIA's
  JetPack 7 / CUDA 13 Jetson index first; `pypi.jetson-ai-lab.io/jp7`
  redirects to the SBSA tree, so `sbsa/cu130` (carrying the exact pinned
  `torch-2.9.1-cp312`) was used, via `UV_INDEX` only — no clone file edited.
- The SBSA wheel declares no CUDA deps and the box has no CUDA toolkit;
  rather than an apt install (which honesty condition h3 reserves for
  explicit approval), the CUDA runtime landed venv-local: pip `nvidia-*`
  CUDA-13 wheels (unsuffixed names; the `-cu13` cublas/nvrtc names are
  placeholders) plus `nvpl-*`, with `LD_LIBRARY_PATH` over 7 lib dirs.
- A bare `uv run` in the clone hung re-locking toward cu129 (2-minute
  timeout); from then on every uv call in the clone used `--no-sync` or the
  exported `UV_INDEX`, and the transiently rewritten `uv.lock` was
  `git restore`d — recorded in the verification record as an operational
  gotcha.
- The jp6/cu129 fallback tree was probed after the sm_87 failure and found
  closed (torch 2.9.1 cp310-only), upgrading the check-5 outcome from "this
  wheel fails" to "no compatible wheel exists at this pin".
- The main repo's stale `uv.lock` (0.9.0 → 0.9.1) regenerated as a side
  effect of `uv run`; committed with the PR as a legitimate fix.
- CI's markdownlint caught MD014 in the new record's check-6 fence
  (all-`$` lines); fixed by moving outputs to their own lines, one
  follow-up commit on the same PR.

## Drift From Plan

| Plan item | Reason for divergence | Classification |
|-----------|-----------------------|----------------|
| `t3` | the live suite ran twice: with `MICRODUCK_LIVE_SIM=1` alone the walking sentinel is *skipped*, not exercised; re-run with `MICRODUCK_LIVE_BODY=sim` to make the sentinel report `xfailed` as the acceptance criterion required | acceptable |
| `t4` | the HF Jobs dry-run half was not attempted: no `hf auth` session exists on this box (error captured verbatim); nothing submitted or billed, command shape remains verified on Spark only | acceptable |
| `t5` | markdownlint-cli2 is not installed on the box, so that one gate ran only in CI (where it initially failed on the new record — see Mid-work Decisions — then passed) | acceptable |

Everything else delivered to contract; the task-by-task accounting above is
the backing for "no further drift". Check 5's FAIL is **not** drift — the
contract was attempt-and-record-verbatim, which is what happened.

## Evidence

All six validation evidence records (`e1`–`e6`) were filed via
`/validate-delivery` and confirmed by the operator:

- tests: `MICRODUCK_LIVE=1 MICRODUCK_LIVE_BODY=sim MICRODUCK_LIVE_SIM=1 pytest -m live -n0` — 12 passed, 1 xfailed (at `3c09fb0`, on-box)
- tests: `tests/test_hosts.py` — 14 passed at `e627cad`, including `test_gb10_torch_source_applies_and_no_remediation` (Spark/GB10 class unchanged)
- tests: `pytest -n auto --cov=microduck_cli` — 1098 passed, 92.80 % (subagent worktree, `3c09fb0`)
- gates: `teken cli doctor . --strict` — 26/26; black/isort/flake8/bandit clean
- run ledger: session scratchpad `orin-sanity/t*.log` (raw outputs; the record quotes them unchanged)
- commits: `3c09fb0..e627cad` on `docs/orin-sanity`
- PRs: #6 — lint, test ×2, version-check, test-publish, GitGuardian all pass; SonarCloud OK; 0 unresolved threads

## Delivery Claims

| Claim | Confidence | Evidence |
|-------|------------|----------|
| The simulation runs and is controlled on AGX Orin: standing duck, 50 Hz loop (0 missed), skills, rules engine driving the head | high | file `docs/verification/2026-09-04-orin-sanity.md` · live suite 12 passed at `3c09fb0` (e1) |
| Walking remains not-achieved at this pin, host-independently; no silent XPASS | high | `test_sim_body_walks_forward_on_move` xfailed (e2) |
| Spark support is kept, not replaced: zero code changes; GB10 classification and its torch-source verdict byte-identical | high | `tests/test_hosts.py` 14 passed at `e627cad` (e3) · empty `hosts.py`/`doctor.py` diffs |
| GPU training on AGX Orin is impossible at this pin (no sm_87 + cp312 + torch==2.9.1 wheel on any Jetson index) | high | check-5 traceback quoted in the record (e6) · jp6/cu129 probe |
| The record landed via a version-bumped PR with CI green | high | PR #6 at 0.9.2 (e5) |
| The box was left as found, plus artifacts: vLLM healthy again, clones clean at pins, worktree removed | high | `docker ps` healthy · `git status` clean in both clones (t6 wrap log) |
| The HF Jobs dry-run command shape works from this box | unverified | not attempted — no `hf` session; verified on Spark only |

## Remaining Work / Follow-up

- **Merge PR #6** — the final human gate; merging publishes 0.9.2 to PyPI.
- **Torch-on-Orin at the next re-pin** — the finding is pin-scoped: a future
  microduck_rl pin (torch ≥2.10, or a Jetson sm_87 cu13 wheel appearing)
  re-opens the question; the record's recipe section is the starting point.
  Consider a brief upstream issue on `microduck_rl` citing the record.
- **`hf auth login` on this box** (operator decision) if the HF Jobs lane
  should ever be driven from the Orin; until then that claim stays unverified.
- **markdownlint-cli2 locally** (optional) — installing it on the box would
  have caught MD014 before CI did.
