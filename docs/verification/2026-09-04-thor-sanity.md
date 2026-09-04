# On-box verification — Jetson AGX Thor (2026-09-04)

The second box after the DGX Spark. Plan `thor-sanity`
([`docs/plans/2026-09-04-thor-sanity.md`](../plans/2026-09-04-thor-sanity.md),
spec [`docs/specs/2026-09-04-thor-sanity.md`](../specs/2026-09-04-thor-sanity.md)):
the same six checks recorded for Spark in
[`2026-09-04-sim-bringup.md`](2026-09-04-sim-bringup.md), run **headless** on a
Jetson AGX Thor at the **same upstream pins**, in three tiers whose
prerequisites differ — the `--fake` body, the MuJoCo body, and the train smoke.
Outputs are pasted as produced; nothing here was smoothed.

## The box and the pins

| | |
|---|---|
| host | NVIDIA Jetson AGX Thor (`nvidia-smi`: "NVIDIA Thor", compute capability 11.0), JetPack 7 / L4T R38.2.2, driver 580.00, CUDA 13.0, Ubuntu 24.04.3, aarch64, 14 cores, 122 GB unified memory, no graphical session (`DISPLAY` unset) |
| toolchain | rustup → cargo 1.98.1 (installed for this run), uv 0.12.5, system Python 3.12.3 |
| `pollen-robotics/microduck` | `sim-remote-io` @ `0cd676d6fbb6e90a762c84aa63abe7a02dbc9495` — built natively: robotd, robotctl, tofd, sounds (`target/debug`) |
| `pollen-robotics/microduck_rl` | `develop` @ `29e887ecfbf5d37144759e5a9f8a176dfb83d547` — `uv sync` with the **local override** described under tier 3 |
| microduck-cli | `feat/thor-sanity` @ `2b00480` for every run below |
| daemon API | 16, as on Spark |
| shared box | lobes' vLLM engines stayed up throughout; `free -g` "available" was 42 GB before provisioning, 45 GB before tier 2, 46 GB before tier 3 (deviation d2: they were not lowered, the headroom sufficed) |

### Starting state — `env doctor` before anything was installed

```text
$ microduck env doctor
microduck-cli env doctor: unhealthy

[FAIL] microduck_clone_present: microduck clone not found
[FAIL] microduck_pinned_commit: microduck clone not present; cannot verify the pinned commit
[FAIL] cargo_version: cargo not found ('cargo --version' failed)
[FAIL] daemons_built: missing built binaries: robotd, robotctl, tofd, sounds
[FAIL] rl_clone_present: microduck_rl clone not found
[FAIL] rl_pinned_commit: microduck_rl clone not present; cannot verify the pinned commit
[FAIL] rl_venv_with_onnxruntime: RL venv (.venv) not found under the microduck_rl clone
[ok] state_dir_length / body_port_free
[info] host_class: jetson-thor (NVIDIA Jetson AGX Thor); torch source applies: False
       remediation: torch/warp training path is unverified on this host class: ...
[info] hf_auth: not signed in;  WANDB_API_KEY: unset;  DUCK_PIN: unset
```

Seven provisioning errors, none of them a CLI defect; the classifier named the
board correctly. Provisioning (rustup, the two clones at their pins under
`~/git`, `cargo build -p robotd -p robotctl -p tof -p sounds`) left one failure,
the RL venv — and one gotcha: `env doctor` only sees cargo when rustup's
`~/.cargo/bin` is on `PATH`, which a non-login shell does not get for free.

## Tier 0 — the RL venv, and why the fake body needs it too

**The `--fake` body is not venv-free** (deviation d1): `env up --fake` exits 2
with `no onnxruntime shared object under microduck_rl/.venv — the ONNX policies
cannot be loaded without one`, because robotd dlopens `libonnxruntime` from the
RL venv even with the fake body. So every tier waits for the sync.

**Upstream's torch source does not fit Thor as written.** At the pin,
`microduck_rl` routes torch to `download.pytorch.org/whl/cu129` on any
linux-aarch64 host. CUDA 12.9 predates Thor's `sm_110`, and the user's choice
(spec q4) was to start with NVIDIA's Jetson index. The `jp7/cu130` index that
name suggests is **empty**; Thor on JetPack 7 uses the SBSA userspace and the
live index is `pypi.jetson-ai-lab.io/sbsa/cu130`, which hosts a native
`torch-2.9.1-cp312-cp312-linux_aarch64.whl` (exactly upstream's `==2.9.1` pin)
and passes `warp-lang` through from PyPI (`warp_lang-1.12.0 manylinux_2_34
aarch64`, bundling a CUDA 12.9 toolkit). The local, uncommitted override of the
clone's `pyproject.toml`, pasted here so it is reproducible:

```diff
-  { index = "pytorch-cu129", marker = "sys_platform == 'linux' and platform_machine == 'aarch64'" },
+  { index = "jetson-sbsa-cu130", marker = "sys_platform == 'linux' and platform_machine == 'aarch64'" },
+
+[[tool.uv.index]]
+name = "jetson-sbsa-cu130"
+url = "https://pypi.jetson-ai-lab.io/sbsa/cu130"
+explicit = true
```

plus, in `[project.dependencies]`, three wheels the SBSA torch links but does
not declare — found one `ImportError` at a time:

```diff
+    "nvpl-blas ; sys_platform == 'linux' and platform_machine == 'aarch64'",
+    "nvpl-lapack ; sys_platform == 'linux' and platform_machine == 'aarch64'",
+    "nvidia-cudss-cu13 ; sys_platform == 'linux' and platform_machine == 'aarch64'",
```

```text
$ UV_HTTP_TIMEOUT=600 uv sync            # in the microduck_rl clone
 + torch==2.9.1        (source = { registry = "https://pypi.jetson-ai-lab.io/sbsa/cu130" })
 + warp-lang==1.12.0   + mujoco==3.10.0   + mujoco-warp==3.8.1   + onnxruntime==1.24.4
 + nvpl-blas==0.6.0    + nvpl-lapack==0.4.0.1    + nvidia-cudss-cu13==0.8.0.10
uv sync exit 0
$ python -c "import torch"      # before the three extra wheels
ImportError: libnvpl_lapack_lp64_gomp.so.0: cannot open shared object file
ImportError: libcudss.so.0: cannot open shared object file            # after nvpl, before cudss
```

Their `.so` files land under `site-packages/nvpl/lib` and
`site-packages/nvidia/cu13/lib`, which torch's RPATH does not cover, so every
run below exports
`LD_LIBRARY_PATH=<venv>/lib/python3.12/site-packages/nvpl/lib:<venv>/lib/python3.12/site-packages/nvidia/cu13/lib`
(the train lane inherits it through the subprocess environment). With that:

```text
torch 2.9.1 cuda 13.0 available True
device NVIDIA Thor (11, 0)
matmul ok 1073741824.0 in 0.2s
Warp 1.12.0 initialized:
   CUDA Toolkit 12.9, Driver 13.0
   Devices:
     "cuda:0"   : "NVIDIA Thor" (123 GiB, sm_101, mempool enabled)
Module __main__ 9ad1ee1 load on device 'cuda:0' took 498.89 ms  (compiled)
warp kernel ok 2.0 compiled+ran in 0.6s
$ microduck env doctor
microduck-cli env doctor: healthy           # 13/13
```

warp's bundled 12.9 nvrtc reports Thor as `sm_101` (its CUDA-12 name) and
compiles for it; the arch-mismatch failure the challenge pass expected
(spec c21) did not occur. **The cu129 fallback attempt was therefore never
run** — nothing here says whether upstream's own routing works on Thor.

## Check 1 — `env up --sim` yields a standing duck

```text
$ microduck env up --sim --headless --skip-build --json
waiting for duck-a to report healthy (/home/thor/.cache/duck-sim/duck-a.sock)...
{"mode": "sim", "state_dir": "/home/thor/.cache/duck-sim", "ducks": [{"name": "duck-a", ... "healthy": true}], "healthy": true}
$ microduck duck init --apply --json ; sleep 4 ; microduck duck enable --apply --json ; sleep 4
$ microduck duck monitor --frames 2 --json      # last frame
{"policy": "stand", "fallen": false, "z": 0.1181, "loop": {"hz": 50.025, "missed": 0}}
$ microduck env down --json
{... "stopped": [{"name": "duck-a", "outcome": "terminated"}, {"name": "body", "outcome": "recycled"}], "body_port_still_listening": true}
```

**Pass.** Trunk height 0.1181 m under `stand` — the same figure Spark recorded.
`env down` reported the body's port still listening at the instant it
returned; `ss -ltnp` and `pgrep` a few seconds later showed nothing bound and
no daemon left, so it is a race in the report, not a leftover process.

## Check 2 — `duck health --json` reports healthy at 50 Hz

```text
$ microduck duck health           # after enable, MuJoCo body
- loop     : target 50.0 Hz, achieved 49.976421663220705, 461 ticks, 0 missed
```

**Pass.** (`test_health_is_healthy_at_50_hz` passed in both live runs below.)

## Check 3 — `duck do <skill> --apply` runs a skill in sim

Covered by `test_enable_then_a_skill_is_accepted` in both live runs
(enable → `robot.do`, accepted by the daemon with the real ONNX policies loaded
from the venv's onnxruntime 1.24.4). **Pass.** As on Spark, whether the roll
completed was not measured beyond "accepted, not fallen" — the sim ran
headless.

## Check 4 — the rules layer

Covered by `test_rules_check_reads_skills_from_subscribe_on_api16`,
`test_an_over_limit_intent_is_refused_verbatim_with_no_engine` and
`test_engine_run_starts_in_order_and_holds_50_hz` in both live runs: `rules
check` reads the skill list from `robot.subscribe` (API 16), the over-limit
intent is refused with the registry's verbatim text, and the engine starts in
order (connect, hello, health, init, enable, armed) holding 50 Hz. **Pass.**

### Tier 1 — the fake body, the whole live suite

```text
$ MICRODUCK_LIVE=1 MICRODUCK_CLONE=~/git/microduck DUCK_SIM_RL=~/git/microduck_rl \
    uv run pytest -m live -n0 -v tests/live
test_env_doctor_is_healthy_on_this_box PASSED
test_version_is_the_pinned_daemon PASSED
test_health_is_healthy_at_50_hz PASSED
test_init_dry_runs_on_a_pipe_then_applies PASSED
test_enable_then_a_skill_is_accepted PASSED
test_move_refreshes_the_deadman_then_stops PASSED
test_rules_check_reads_skills_from_subscribe_on_api16 PASSED
test_an_over_limit_intent_is_refused_verbatim_with_no_engine PASSED
test_engine_run_starts_in_order_and_holds_50_hz PASSED
test_record_writes_pure_jsonl PASSED
test_env_status_and_down_leave_nothing_tracked PASSED
test_sim_body_stands_the_duck_up SKIPPED
test_sim_body_walks_forward_on_move SKIPPED
======================== 11 passed, 2 skipped in 11.19s ========================
```

**Pass** — 11/11 at CLI `2b00480`; `pgrep robotd` empty afterwards.

### Tier 2 — the MuJoCo body, headless

`free -g` immediately before: `Mem: 122 total, 77 used, 45 available`.

```text
$ MICRODUCK_LIVE=1 MICRODUCK_LIVE_BODY=sim MICRODUCK_LIVE_SIM=1 ... uv run pytest -m live -n0 -v tests/live
(the eleven above) PASSED
test_sim_body_stands_the_duck_up PASSED
test_sim_body_walks_forward_on_move XFAIL
======================== 12 passed, 1 xfailed in 26.65s ========================
```

**Pass** — 12/12 at CLI `2b00480`, no daemon left. The walking sentinel stayed
`xfail` with its Spark reason (the walk policy engages but outputs a static
pose at this pin); no XPASS, so nothing about locomotion changed on Thor.

## Check 5 — the smoke test runs locally, and the same command submits to HF Jobs

### Tier 3 — the 64-env smoke, attempt 1 (Jetson `sbsa/cu130` venv)

`free -g` immediately before: `Mem: 122 total, 76 used, 46 available`. Run under
`timeout 30m` because `policy smoke` has no local wall-clock bound.

```text
$ WANDB_MODE=offline timeout 30m microduck policy smoke Mjlab-Velocity-Flat-MicroDuck --json
{"argv": ["uv", "run", "train", "Mjlab-Velocity-Flat-MicroDuck", "--env.scene.num-envs", "64",
          "--agent.max_iterations", "5"],
 "cwd": "/home/thor/git/microduck_rl", "ok": true, "returncode": 0}
exit 0 elapsed 144s                                  (Spark: real 0m58.6s)
```

From the trainer's own output, captured in the payload:

```text
[INFO] Training with: device=cuda:0, seed=42, rank=0
Warp 1.12.0 initialized:  CUDA Toolkit 12.9, Driver 13.0
     "cuda:0"   : "NVIDIA Thor" (123 GiB, sm_101, mempool enabled)
Module mujoco_warp._src.smooth 95a0b8b load on device 'cuda:0' took 5620.23 ms  (compiled)
Module ccd_kernel_builder__locals__ccd_kernel_079dfa94 079dfa9 load on device 'cuda:0' took 24839.38 ms  (compiled)
Module mujoco_warp._src.sensor 7fc0378 load on device 'cuda:0' took 5574.82 ms  (compiled)
... (every mujoco_warp module compiled fresh — the kernel cache was empty)
Exception ignored in: <_io.TextIOWrapper name='<stdout>' mode='w' encoding='utf-8'>
BrokenPipeError: [Errno 32] Broken pipe
```

**Pass.** `ok: true`, five iterations at 64 envs; the extra 85 s over Spark is
first-run warp compilation on a cold cache (the ccd kernel alone took 25 s).
The trailing `BrokenPipeError` is the trainer's own interpreter shutdown
complaining after the CLI had already collected its output and exit code 0;
it is pasted because it was there, not because it changed the result. The
cu129 fallback (attempt 2) was not run — see tier 0.

### The HF Jobs half — dry run

```text
$ cd ~/git/microduck_rl && uv run train Mjlab-Velocity-Flat-MicroDuck --env.scene.num-envs 64 \
    --agent.max_iterations 5 --hf-jobs --dry-run --namespace orinachum --no-wandb
[hf] namespace: orinachum
[src] building tarball -> src-20260904-083623.tar.gz (from /home/thor/git/microduck_rl)
[src] HEAD=29e887e, 9.1 MB
[dry-run] would submit job:  flavor: l4x1, timeout: 12h  image: pytorch/pytorch:2.5.1-cuda12.4-cudnn9-runtime
  secrets:   {'HF_TOKEN': '***'}
exit 0
```

**Pass for the dry run only.** No job was submitted, nothing billed. The `hf`
CLI is not installed on Thor and no `hf auth` session exists; the dry run
needed neither (plan risk r4 resolved: the token in the environment sufficed
for the tarball step, and submission was not attempted).

## Check 6 — the gates

At the PR's head, on Thor:

```text
black --check microduck_cli tests        97 files would be left unchanged.
isort --check-only microduck_cli tests   (clean)
flake8 microduck_cli tests               exit 0
bandit -q -c pyproject.toml -r microduck_cli    exit 0 (three "Test in comment" warnings, as on Spark)
teken cli doctor . --strict              healthy: 26/26 passed, 0 errors, 0 warnings
python3 -c "..."                         dependencies = []
pytest -n auto --cov=microduck_cli       1099 passed;  TOTAL 93% (gate 60)
markdownlint-cli2 "**/*.md" ...          0 error(s)   (markdownlint-cli2@0.13.0 via npx; node 18 on this box)
```

**Pass.**

## What changed in the repo because of this run

- `env/hosts.py`: the `jetson-thor` remediation now states the override recipe
  above instead of "unverified"; `torch_source_applies` stays `False` because
  upstream's `pytorch-cu129` source was never exercised on Thor. AGX Orin keeps
  the unverified text. `tests/test_hosts.py` pins both. One line inside
  `_classify()` changed to name the new constant (deviation d3); no condition
  or verdict did. At the user's request the same treatment covers Spark:
  `HostInfo.verified` now points GB10 at the Spark record and Thor at this one
  (Orin and every other class stay `None`), and `env doctor` appends
  `verified on-box: …` to its `host_class` line.
- The Spark record's Thor bullet links here.

## What was NOT verified

- **Upstream's own torch routing on Thor.** Every tier ran on the local
  `sbsa/cu130` override; the `cu129` path was never tried here (plan risk r1
  stays open on that half). The override is uncommitted in the clone and is
  the finding to carry upstream: the SBSA torch wheel omits its NVPL and
  cuDSS dependencies, and `microduck_rl`'s aarch64 marker cannot tell a Thor
  from a GB10.
- **A real HF Jobs submission**, as on Spark.
- **Walking**, unchanged: still `xfail` at this pin pair.
- **AGX Orin** — not attempted; the host classifier only.
- **No physical duck.** The `--fake` and MuJoCo bodies only.
- **The self-hosted GitHub runner on this box** (`~/git/actions-runner`) was
  not paused; whether any other repo's job overlapped the 144 s smoke was not
  checked (plan risk, non-blocking).

Plan risk t8/t15 of the September 3 plan ("Jetson Thor / AGX Orin torch+warp
path unverified") is resolved for Thor — **with the override**, not as
shipped — and stays open for Orin.

Raw logs (doctor before/after, cargo build, both `uv sync` passes, the probe,
both live-suite logs, the smoke payload, the dry run, the gates) are kept in
the session scratchpad outside the repo; the numbers above are copied from
them unchanged.
