# thor-sanity

> microduck-cli passes its on-box sanity on Jetson AGX Thor: the same six checks recorded for DGX Spark in docs/verification/2026-09-04-sim-bringup.md run headless on Thor at the same upstream pins, the result is recorded verbatim in a second verification record, and env doctor's jetson-thor 'training path unverified' remediation is either retired or kept with evidence
> instruction: Provision per the decision, then run the six Spark checks headless on Thor in tier order (fake live suite, sim live suite with `MICRODUCK_LIVE_SIM`=1, policy smoke), paste outputs into docs/verification/<date>-thor-sanity.md, adjust hosts.py's remediation to the smoke result, update the Spark record's Thor bullet, bump the version

## Audience

- The operator of this Thor box and any agent that reads env doctor's `host_class` remediation before trusting the train lane on a Jetson; secondarily the reviewer comparing the Thor record to the Spark one

## Before → After

- Before: Only DGX Spark is verified on-box; on Thor the CLI classifies the host but env doctor tells every user the training path is unverified, and nobody has run even the --fake body there
- After: docs/verification carries a Thor record beside the Spark one with all three tiers' outcomes pasted verbatim, env doctor's jetson-thor remediation matches the evidence, and the live suite's pass/fail count on Thor is on record at a named commit

## Why it matters

- Thor is the second real box in this workspace and the closest to the duck's own aarch64 target; a sanity there turns the plan's t8/t15 `unknown_nonblocking` risk into a fact either way, so the train lane's Jetson claim stops being a guess

## Requirements

- The sanity runs in three tiers because their prerequisites differ: (1) --fake body needs only cargo-built robotd; (2) --sim body adds the `microduck_rl` venv (mujoco/warp/torch on Thor); (3) the train smoke (check 5) adds a working CUDA torch. Each tier is recorded pass/fail on its own so a torch failure does not hide a passing fake/sim result
  - honesty: The Thor record has one pass/fail line per tier, and a failing smoke tier appears alongside a passing fake and sim tier rather than replacing them
- The repeatable runner is tests/live/`test_live_cli.py`: `MICRODUCK_LIVE`=1 uv run pytest -m live -n0 for the fake body, `MICRODUCK_LIVE_BODY`=sim `MICRODUCK_LIVE_SIM`=1 for MuJoCo, always --headless. No new test code is needed for Thor; the record cites the pass/fail count as the Spark record did (12 passed at 25114a2)
  - honesty: The record cites the live suite's exact pytest summary line (N passed, N xfailed) on Thor with the CLI commit hash, for the fake run and the sim run separately
- A new record docs/verification/<date>-thor-sanity.md mirrors the Spark record's shape (box-and-pins table, one section per check, outputs pasted verbatim, a 'What was NOT verified' section), and the Spark record's 'Jetson Thor / AGX Orin training — not attempted' bullet is updated to point at it
  - honesty: docs/verification/<date>-thor-sanity.md exists with the box-and-pins table, one section per check, and a 'What was NOT verified' section; the Spark record's 'Jetson Thor / AGX Orin training — not attempted' bullet links to it
- env/hosts.py's `_UNVERIFIED_REMEDIATION` for jetson-thor is the claim the sanity tests: if the train smoke passes on Thor, the remediation text and its docstring change to say Thor is verified (tests/`test_hosts.py` asserts the verdict); if it fails, the text stays and the record carries the failure. Either way the version bumps and CHANGELOG gets an entry
  - honesty: After the run, env doctor on Thor prints a `host_class` remediation consistent with the record: empty (with tests/`test_hosts.py` updated) if the smoke passed, the unverified text if it failed; pyproject version differs from origin/main
- Tier 3 (policy smoke) is run under an explicit wall-clock bound chosen by the operator (e.g. the coreutils timeout command, 30 min) because policy smoke has no local timeout (train/lane.py only passes --timeout to the HF Jobs path), and the record states elapsed time beside Spark's 58.6 s so a JIT-bound Thor run is visible as a number, not a hang
  - honesty: The record's tier-3 section has an elapsed-time line and, on a timeout, says so verbatim rather than reporting a pass or an unexplained failure

## Honesty conditions

- The six checks in the Thor record carry the same headings as the Spark record and each ends in Pass or a verbatim failure
- The pre-provisioning env doctor --json output (7 provisioning errors, `host_class` jetson-thor) is pasted in the record as the starting state
- git diff of the sanity PR leaves docs/upstream-pins.md untouched
- classify() and `_classify`() in env/hosts.py are byte-identical before and after; only `_UNVERIFIED_REMEDIATION` and the module docstring may differ
- .claude/skills/operate-microduck/SKILL.md is untouched in the sanity PR
- .github/workflows/tests.yml is untouched in the sanity PR
- The record's opening table names the box, the pins and the CLI commit so an operator can reproduce it, and env doctor's remediation text is quoted in the record
- The record pastes the pre-provisioning env doctor output showing jetson-thor with the unverified remediation and 7 provisioning errors
- docs/verification lists two records, and grep for 'jetson-thor' in env/hosts.py shows text consistent with the Thor record's smoke outcome
- The Thor record cites plan risk t8/t15 by name and states which way it resolved
- Each item of the success signal maps to a pasted line in the Thor record: the doctor healthy count, the two pytest summaries, the smoke's ok/failure line with elapsed time, the six gate outputs, and the version/CHANGELOG diff
- Because Thor's GPU shares system RAM (nvidia-smi reports N/A for GPU memory; the two VLLM::EngineCore processes are the top RSS holders at ~2.7 and ~2.2 GB with ~80 GB used overall), the record states free RAM from 'free -g' immediately before tier 2 and tier 3 start, after lobes is lowered
- If tier 3 fails, the record pastes the first exception verbatim and names which library raised it (warp/nvrtc, torch, mjlab); the cause is called an arch mismatch only if the error text says so
- grep -E '`hf_`\[A-Za-z0-9\]{20,}|`WANDB_API_KEY`=' on the record returns nothing at PR time

## Success signals

- On Thor at the same pins: env doctor healthy (13/13) after provisioning; the fake-body live suite passes; the sim live suite stands the duck up headless at 50 Hz; policy smoke returns ok true or a verbatim failure; the six gates pass; CHANGELOG and version bumped
  - instruction: Verify each item from the record: doctor's healthy line, the two pytest summary lines, the smoke's ok/argv/elapsed lines, the six gate outputs, and git diff of pyproject/CHANGELOG

## Scope / boundaries

- No re-pin: docs/upstream-pins.md stays at microduck 0cd676d / `microduck_rl` 29e887e so the Thor result is comparable to Spark's. A re-pin is its own PR with both boxes re-verified
- hosts.py classification logic is not changed by the sanity: it already returns jetson-thor on this box (verified live via env doctor --json). Only the remediation string and docstring may change, per the evidence
- operate-microduck SKILL.md's screenshot recipe is not exercised or edited: Thor has no graphical session (DISPLAY unset, user confirmed headless); --headless is the documented path and is already in the skill
- The CI real-daemon job (.github/workflows/tests.yml, ubuntu-latest `x86_64`, gated on `MICRODUCK_REAL_DAEMON`) is untouched: it never runs on a Jetson and Thor evidence is an on-box record, not a CI job
- The Thor record is hand-pasted markdown, outside the CLI's no-secrets guard (tests/`test_no_secrets_in_output.py` covers CLI output only), and this shell exports `HF_TOKEN` and `HUGGINGFACE_TOKEN`; before commit the record is grepped for token prefixes (`hf_`, wandb) and any env dump, and env doctor output is pasted only in its set/unset form

## Non-goals

- Achieving walking in the MuJoCo body is not a Thor goal: Spark recorded 'locomotion not achieved at this pin' with the CLI, params, keyframe and real-time factor ruled out; `test_sim_body_walks_forward_on_move` stays a non-strict xfail and an XPASS on Thor would be a re-pin signal, not a Thor fix
- No CLI code path is added for Jetson-specific torch wiring: `microduck_rl`'s \[tool.uv.sources\] torch marker (`sys_platform` linux + aarch64, index pytorch-cu129, torch==2.9.1, warp-lang 1.12.0) is upstream's and matches Thor as written; if it does not work here the finding goes upstream (communicate skill), not into microduck-cli
- jetson-thor-cli (sibling repo, the Thor ops agent) is not a dependency or a participant: dependencies = \[\] stays, and nothing from that repo is imported or cited in the sanity

## Assumptions

- Thor is a bare box today: env doctor on it reports 7 errors, all provisioning (no cargo/rustup, no microduck or `microduck_rl` clone, no RL venv); the CLI itself ran and classified the host as jetson-thor (L4T R38.2.2, driver 580.00, CUDA 13.0, Python 3.12.3, uv 0.12.5). Provisioning (rustup, two clones at the pinned commits beside the repo, uv sync) is the operator's first step, not a CLI change
- Thor's GPU is compute capability 11.0 (nvidia-smi --query-gpu=`compute_cap` on this box). CUDA 12.9 has no `sm_110` target (Thor was `sm_101` in 12.x and became `sm_110` in CUDA 13.0), so the cu129 torch 2.9.1 wheel (cp312 aarch64 exists on download.pytorch.org/whl/cu129) carries no Thor SASS and must PTX-JIT through the 580 driver, and warp-lang 1.12.0's bundled 12.9 nvrtc may refuse the device arch outright. The smoke tier is therefore more likely to fail at warp device init than in torch, and that is the expected shape of a tier-3 failure

## Scope exploration

- `s1` — `this box (hostname thor): uname, nvidia-smi, /etc/nv_tegra_release, 'uv run microduck env doctor --json'`: NVIDIA Thor, L4T R38.2.2, driver 580.00, CUDA 13.0, aarch64, Python 3.12.3, uv 0.12.5, headless (DISPLAY unset). env doctor: healthy=false, 7 errors all provisioning (cargo missing, both clones missing, RL venv missing); `host_class` info check says jetson-thor, torch source applies False with the unverified remediation. 122 GB RAM with 81 GB already in use, 262 GB disk free
  - seeds: `c2`, `c8`
- `s2` — `docs/verification/2026-09-04-sim-bringup.md (the Spark record)`: Six checks: env up --sim standing duck, health 50 Hz, do roulade, one-rule overlay + rules check refusal, policy smoke local + HF dry-run, the gates. Closing bullet: 'Jetson Thor / AGX Orin training — not attempted; the host classifier only'. Walking is recorded as not achieved at this pin with the causes ruled out
  - seeds: `c3`, `c5`, `c11`
- `s3` — `tests/live/test_live_cli.py module docstring + .github/workflows/tests.yml real-daemon job`: The live suite is the opt-in operator-shaped runner (`MICRODUCK_LIVE`=1, -m live -n0; `MICRODUCK_LIVE_BODY`=sim / `MICRODUCK_LIVE_SIM`=1 for MuJoCo, headless). CI's real-daemon job is ubuntu-latest, cargo-gated, and skipped unless `MICRODUCK_REAL_DAEMON` is set; it is not a Jetson surface
  - seeds: `c4`, `c10`
- `s4` — `microduck_cli/env/hosts.py + tests/test_hosts.py`: classify() names jetson-thor from 'thor' in nvidia-smi name or `nv_tegra_release`; `_UNVERIFIED_REMEDIATION` is attached because pytorch-cu129 'has never been exercised against a Jetson's CUDA/cuDNN stack' (plan risk t8/t15 `unknown_nonblocking`). The test file pins the verdict `torch_source_applies`=False for `JETSON_THOR_PROBE`
  - seeds: `c6`, `c8`
- `s5` — `microduck_rl pyproject.toml at pin 29e887e (fetched via gh api, not cloned)`: requires-python >=3.12,<3.13; torch==2.9.1 routed to <https://download.pytorch.org/whl/cu129> when `sys_platform`==linux and `platform_machine`==aarch64 (so the marker fires on Thor too); warp-lang==1.12.0 bundles a CUDA 12.9 toolkit; onnxruntime>=1.24.4 (aarch64 wheel exists on PyPI). Whether the cu129 torch and warp wheels run on Thor's Blackwell GPU under a CUDA 13.0 driver is exactly the unverified claim
  - seeds: `c3`, `c12`
- `s6` — `docs/upstream-pins.md`: Three rows pinned 2026-09-02; the file itself says re-pin deliberately in one PR and re-run the on-box verification (t23) when you do. A Thor sanity at a different pin would not be comparable to Spark's
  - seeds: `c7`
- `s7` — `.claude/skills/operate-microduck/SKILL.md 'Open the simulation' + 'Watch it'`: --headless is already the documented no-window path with --skip-build for prebuilt daemons; the screenshot recipe needs a display owner's DISPLAY/XAUTHORITY which Thor does not have (user: 'thor runs headless')
  - seeds: `c9`
- `s8` — `~/git/jetson-thor-cli README + CLAUDE.md hard constraint dependencies = []`: jetson-thor-cli is the Thor ops agent (status, provisioning) on the same template; microduck-cli's zero-dep constraint and the 'no code from siblings' non-goal in the spec mean it stays a neighbour, not a participant
  - seeds: `c13`
- `s9` — `microduck_rl README.md + AGENTS.md at pin 29e887e, microduck CONTRIBUTING.md at pin 0cd676d (gh api, read-only)`: README: the only ARM note is `UV_HTTP_TIMEOUT`=600 for the first sync; AGENTS.md repeats the cu129 routing rationale and its test tests/`test_aarch64_cuda_torch.py`; CONTRIBUTING: Rust 1.89+ stable, aarch64 Linux is the robot's own target so a native Thor build needs no cross-compilation. No Blackwell or Jetson-specific instruction exists upstream
  - seeds: `c12`
- `s10` — `challenge pass / GPU-architecture lens: nvidia-smi compute_cap, download.pytorch.org/whl/cu129 index, pypi warp-lang 1.12.0 files`: `compute_cap` 11.0; cu129 cp312 aarch64 torch wheel present; warp 1.12.0 has a `manylinux_2_34` aarch64 wheel bundling a CUDA 12.9 toolkit. cu129 predates `sm_110`, so tier 3's outcome hinges on PTX JIT and nvrtc arch support — recorded as the expected failure shape, not a conclusion
  - seeds: `c21`
- `s11` — `challenge pass / observability lens: microduck_cli/train/lane.py smoke() + cli/_commands/policy.py --timeout`: no wall-clock bound on the local smoke; a first-run PTX JIT or a hung warp init on an unknown arch would block indefinitely and leave no number
  - seeds: `c22`
- `s12` — `challenge pass / adjacent-systems lens: ps rss, nvidia-smi compute-apps, free -g, ~/git/actions-runner`: lobes' vLLM engines hold the memory on a unified-memory board, so lowering lobes frees GPU and RAM together; a self-hosted GitHub runner also lives on this box (~/git/actions-runner) and could see slower jobs during tier 2/3 — residual, not a blocker, since tests.yml here runs on ubuntu-latest
  - seeds: `c20`
- `s13` — `challenge pass / security lens: env (HF_TOKEN, HUGGINGFACE_TOKEN set), tests/test_no_secrets_in_output.py, the Spark record's pasted doctor lines`: CLI output is guarded; the pasted markdown record is not — seeded a pre-commit grep boundary
  - seeds: `c23`
- `s14` — `challenge pass / hidden-dependencies lens: dpkg (build-essential, pkg-config, cmake, libasound2-dev, libudev-dev, libssl-dev), /etc/os-release, uv python list`: Ubuntu 24.04.3; the native toolchain deps the Spark build needed are already present; libdbus-1-dev is absent but configd/btd are not in the build set (cargo build -p robotd -p robotctl -p tof -p sounds); Python 3.12.3 satisfies `microduck_rl`'s >=3.12,<3.13. Clean pass — residual: robotd's exact -sys crates were not enumerated, so a missing -dev package surfaces at cargo build, cheaply
- `s15` — `challenge pass / lifecycle + reversibility lens: tests/live/test_live_cli.py (tempfile state dirs), env/stack.py (env down, pid-by-cmdline), rustup + ~/git clones`: the live suite isolates its state dir; a dead daemon is cleaned by env down, never kill-by-name; provisioning is reversible (rustup self uninstall, rm the two clones and their venv/target). Clean pass — residual: a uv sync killed mid-download leaves a partial venv that a re-run repairs, not a hazard
- `s16` — `challenge pass / concurrency lens: DUCK_SIM_PORT 7801 (free per doctor), ~/.cache/duck-sim (36-byte socket paths), pgrep robotd (none)`: single operator user on the box, no daemon running, port free; one duck stack per box is the existing constraint. Clean pass
- `s17` — `challenge pass / migration lens`: no schema, store or persisted format changes in this idea; not applicable
- `s18` — `challenge pass / GPU-architecture lens, follow-up: pypi.jetson-ai-lab.io/jp7/cu130 (devpi) probed by curl from this box`: the jp7/cu130 index exists (its navigation lists jp7 > cu130 > torch) but its +simple listings returned no wheel filenames to curl from here, so the exact torch/warp versions available for JetPack 7 are unread; uv sync against it is the probe, and the record pastes what resolved

## Decisions

- Provisioning is part of the sanity, done on the box outside the repo: rustup (cargo >= 1.89), clones of microduck at 0cd676d and `microduck_rl` at 29e887e under ~/git beside this repo, `UV_HTTP_TIMEOUT`=600 uv sync for the RL venv. The system CUDA 13.0 install is not touched; the cu129 torch runtime lives inside the venv
- lobes is lowered by the operator for the duration of the sim and smoke runs and raised afterwards; the record states the free-RAM figure at run time
- Tier 3 order on Thor, per the user: first the JetPack 7 / CUDA 13 Jetson index (pypi.jetson-ai-lab.io/jp7/cu130) through a local uncommitted override of the RL clone's torch source, then upstream's cu129 routing as the recorded fallback. Each attempt gets its own section in the record, and the override text is pasted there so it is reproducible

## Open parks

- [unknown_nonblocking] Whether the MuJoCo `body_server` (mujoco-warp via mjlab 1.3.0) runs headless on Thor at the same real-time factor Spark recorded (1.00) — the sim tier's cadence assertions in the live suite depend on it
- [unknown_nonblocking] The hf CLI is not installed on Thor and 'hf auth whoami' finds no session, though `HF_TOKEN` is set in the environment; check 5's --hf-jobs --dry-run may need one or the other, undecided until tried
- [unknown_nonblocking] Whether other repos' CI jobs on the self-hosted runner at ~/git/actions-runner overlap the sanity window; not checked, and only matters if a job fails on timing while the sim or smoke runs
- [unknown_nonblocking] Which torch version and which warp build the JetPack 7 / CUDA 13 index resolves for cp312 on Thor, and whether they satisfy mjlab 1.3.0 / `rsl_rl` and upstream's torch==2.9.1 pin (the override may need to relax the pin locally); unreadable from here, learned at uv sync time

## Resolved vagueness

- [unknown_blocking] Whether torch 2.9.1 from the cu129 index and warp-lang 1.12.0 actually initialise CUDA on Thor (Blackwell-class Jetson, CUDA 13.0 driver, L4T R38) — no clone or venv exists yet to probe it, and no memory record covers it; decidable only by running uv sync and the smoke — resolved: Not decidable before the run and does not need to be: tier 3 of the sanity (the 64-env smoke) IS the probe, and per the user's q1 decision a failing smoke is a valid recorded outcome, not a spec blocker. Carried as the smoke tier's expected-uncertain result
