# orin-sanity

> microduck-cli passes the on-box sanity checks on Jetson AGX Orin, recorded as a verification doc beside the Spark one
> instruction: bring the box up (rustup, clone both repos at the docs/upstream-pins.md commits, uv sync the RL venv, set `MICRODUCK_CLONE`/`DUCK_SIM_RL`), run env doctor until healthy, then execute the six checks of docs/verification/2026-09-04-sim-bringup.md in order (fake body first, then sim), run the live suite, and write the record with outputs pasted unchanged

## Audience

- the operator of this box plus future agents and reviewers who need microduck-cli's verified-on-Orin status; mesh peers reading docs/verification/
  - instruction: write the record self-contained like the Spark one: a header table (host, L4T/CUDA, toolchain, pins, CLI commits, daemon API) so a reader needs no session context

## Before → After

- Before: the Spark record names 'Jetson Thor / AGX Orin — not attempted; the host classifier only' as an open gap, and this box is bare: no rust toolchain, no microduck/`microduck_rl` clones, `MICRODUCK_CLONE`/`DUCK_SIM_RL` unset
  - instruction: record the bare-box starting state (no rust, no clones, unset env vars) in the record so the bring-up steps are reproducible
- After: a dated record docs/verification/<date>-orin-sanity.md exists beside the Spark one, recording the six checks as run on this Orin with outputs pasted verbatim — including check 5's torch-on-L4T outcome, pass or fail — and lands via a version-bumped PR
  - instruction: name the file with the run date, link docs/upstream-pins.md, open the PR with a version bump via the version-bump + cicd skills

## Requirements

- re-run the Spark sanity's six success-signal checks on this Jetson AGX Orin and land a sibling record in docs/verification/ — the Spark record's 'What was NOT verified' names 'Jetson Thor / AGX Orin — not attempted; the host classifier only' as the gap
  - instruction: run the six checks in the Spark record's order (fake body first, then MuJoCo sim), paste each check's output unchanged into docs/verification/<date>-orin-sanity.md with the CLI commit named per check
  - honesty: the Orin checks map 1:1 to the Spark record's six checks; anything not attempted lands in an explicit NOT-verified section, never silently omitted
- bring the box up first: this Orin has no cargo/rustc, no microduck/`microduck_rl` clones, and `MICRODUCK_CLONE`/`DUCK_SIM_RL` unset — install rustup (env doctor requires cargo, stack.py remediation says >= 1.89; Spark used 1.93.1), clone both repos at the pins in docs/upstream-pins.md, uv sync the RL venv; user approved installing rust
  - instruction: install rustup (stable toolchain, cargo >= 1.89), clone pollen-robotics/microduck at 0cd676d (branch sim-remote-io) and `microduck_rl` at 29e887e (branch develop) beside this repo or set `MICRODUCK_CLONE`/`DUCK_SIM_RL`, uv sync the RL clone, then iterate 'microduck env doctor' to healthy
  - honesty: bring-up uses exactly the pinned commits from docs/upstream-pins.md and rustup for the toolchain; no system-wide installs beyond the rust toolchain the user approved
- the record's header states the box's runtime conditions the tick-rate signals depend on: power mode (probed today: MAXN), memory pressure at run time, and what else was running — on Jetson unified memory the GPU budget is the system budget
  - honesty: the header's runtime-conditions values (power mode, memory, co-tenants) are probed again at run time on the sanity box, never copied forward from this challenge pass's probes

## Honesty conditions

- every number and output line in the Orin record is pasted from an actual run on this box; nothing is smoothed, and any check that fails or cannot run is recorded verbatim as such
- if the live doctor does NOT report jetson-agx-orin on this box, that is a classifier defect to surface to the user — never patched silently mid-sanity
- doctor failures on this box are fixed by changing the environment, never by editing the checks
- an XPASS on the walking sentinel is reported to the user as news (walking arrived at this pin), never silently absorbed
- the record must be trustworthy to a reader who did not watch the run — no reference to unavailable scratchpad logs as load-bearing evidence
- the before-state is recorded as probed on this box, not assumed
- the record is written only from actual runs at named CLI commits, per-check, as the Spark doc does
- success signals are pasted outputs, never paraphrased summaries
- if uv sync fails on the torch wheel, the record states the failure verbatim and the sim checks are reported as blocked-by-venv (falling back to the --fake body only), never silently narrowed

## Success signals

- env up --sim yields a standing duck on this box, the live suite passes (`MICRODUCK_LIVE`=1, walking sentinel still xfail), the gates (check 6) are green, and CI passes on the record PR
  - instruction: quote the standing-duck monitor frames, the live-suite summary line, and the check-6 gate outputs verbatim in the record

## Scope / boundaries

- `microduck_cli`/env/hosts.py needs no change: classify() already returns jetson-agx-orin for this box (`tegra_release` present, 'orin' in the gpu-name haystack) with `torch_source_applies`=False and the unverified remediation — the sanity run confirms the live verdict, it does not touch the classifier
  - instruction: verify via 'microduck env doctor --json' that this box reports `host_class` jetson-agx-orin with the unverified-torch remediation; the diff of `microduck_cli`/env/hosts.py stays empty
- `microduck_cli`/env/doctor.py needs no change: the `host_class` check is info-only and always passes (`torch_source_applies`=False just attaches the remediation text) — on this box doctor will fail on `cargo_version` and the clone-pin checks until bring-up is done, which is the tool doing its job
  - instruction: drive bring-up from doctor output; the diff of `microduck_cli`/env/doctor.py stays empty
- the walking xfail sentinel stays expected-fail on Orin: locomotion-not-achieved is a pin-level defect (walk network selected but joint targets static, ruled out CLI/params/keyframe/RTF on Spark), not host-level — an Orin walk failure is NOT a regression, and an XPASS would mean the pin question needs revisiting, not the Orin run
  - instruction: run '`MICRODUCK_LIVE`=1 uv run pytest -m live -n0' (plus `MICRODUCK_LIVE_SIM`=1 for the sim body); the walking sentinel must report xfail, and its result is quoted in the record

## Non-goals

- no re-pin: the sanity runs against the exact pins in docs/upstream-pins.md (microduck sim-remote-io 0cd676d, `microduck_rl` develop 29e887e, API 16) — re-pinning is its own deliberate PR with its own re-verification

## Assumptions

- uv sync of the `microduck_rl` clone must succeed for checks 1-4 as well, not just check 5: `body_server` (MuJoCo) and onnxruntime live in that venv, and uv sync will attempt the pytorch-cu129 aarch64 torch wheel on this box — a torch install failure would block the sim body, not only the smoke

## Scope exploration

- `s1` — `docs/verification/2026-09-04-sim-bringup.md`: the Spark sanity is the template: six checks (env up --sim standing duck, health 50Hz, duck do skill, rules overlay + refusal, policy smoke local+HF dry-run, the gates) plus the fake-body pass and the live suite; its NOT-verified list explicitly names AGX Orin as not attempted
  - seeds: `c2`
- `s2` — `this box (Jetson AGX Orin, L4T R39, CUDA 13.2, driver 595.78)`: probed read-only: /etc/`nv_tegra_release` R39 present, GPU 'Orin (nvgpu)', uv + python 3.12.3 present, but no rust toolchain and no upstream clones — bring-up is a prerequisite, and the user approved installing rust
  - seeds: `c3`
- `s3` — `microduck_cli/env/hosts.py`: jetson-agx-orin branch exists and matches this box's probe (gpu 'Orin (nvgpu)', `nv_tegra_release` R39); `torch_source_applies`=False by design — Jetson gets JetPack-index torch, not pytorch-cu129
  - seeds: `c4`
- `s4` — `microduck_cli/env/doctor.py`: `host_class` check is severity=info, passed=True regardless of torch verdict (lines 540-560); the real Orin blockers doctor will report are `cargo_version` and the clone checks — `MICRODUCK_CLONE` else ../microduck sibling, `DUCK_SIM_RL`/`MICRODUCK_RL_CLONE` else ../`microduck_rl` sibling
  - seeds: `c5`
- `s5` — `docs/upstream-pins.md`: pins are validated-against commits; the doc itself says re-pin deliberately (one PR, all rows) and re-run on-box verification when you do — so the Orin sanity holds the pins fixed
  - seeds: `c6`
- `s6` — `tests/live/test_live_cli.py`: the live suite (`MICRODUCK_LIVE`=1, -m live -n0; `MICRODUCK_LIVE_SIM`=1 for the MuJoCo body) is the runnable half of the sanity — 12 checks passed on Spark; `test_sim_body_walks_forward_on_move` is a non-strict xfail sentinel by design
  - seeds: `c7`
- `s7` — `microduck_cli/train/lane.py + microduck_rl [tool.uv.sources] (read at the pin via hosts.py docstring)`: the train lane resolves the RL clone via `DUCK_SIM_RL`/`MICRODUCK_RL_CLONE` and shells to uv run train there; whether that venv's torch actually runs on Orin is exactly the t8/t15 `unknown_nonblocking` risk the classifier remediation names — seeded question q1 (include check 5 or record it not-attempted)
- `s8` — `challenge pass / adjacent-systems lens: microduck_rl pyproject at pin + train/lane.py`: the RL venv is a shared dependency of the sim checks and the smoke; torch install failure contaminates checks 1-4, not just 5 — seeded the venv assumption
  - seeds: `c12`
- `s9` — `challenge pass / operations lens: this box (nvpmodel, free, ps)`: probed: MAXN power mode, 12 cores, 1.4T disk free, port 7801 free, headless (no DISPLAY, matches the Spark recipe) — but 53Gi/61Gi RAM held by a vLLM engine (colleague backend), ~7.7Gi available
  - seeds: `c13`
- `s10` — `challenge pass / failure-mode lens: policy smoke on unverified torch`: a hang is likelier than a clean ImportError on a bad GPU stack; Spark's smoke took 58.6s — parked the timeout-discipline residual for the plan leg
- `s11` — `challenge pass / reversibility lens: rustup + clones`: clean: rustup self uninstall reverses the toolchain, clones are plain directories; g++ is absent but rust links via cc — if any build step wants g++ an apt install is surfaced to the user first, not done silently
- `s12` — `challenge pass / security lens: bring-up downloads`: clean pass: clones are pinned commits from pollen-robotics, wheels from PyPI/PyTorch indexes over uv, rustup from the official installer; no credentials touched (HF/wandb stay unset unless check 5 needs the dry-run namespace)

## Open parks

- [unknown_nonblocking] MuJoCo `body_server` + onnxruntime aarch64 wheels installed and ran on Spark (GB10, also linux-aarch64) but have never been exercised on Orin's L4T userspace; likely fine (plain aarch64 CPU wheels, ORT dlopened from the RL venv) but only the live run tells
- [unknown_nonblocking] check 5 may hang rather than fail cleanly on an incompatible torch/GPU stack — the run needs a timeout discipline (lands plan-side as a risk/task once /spec-to-plan seeds the plan)
