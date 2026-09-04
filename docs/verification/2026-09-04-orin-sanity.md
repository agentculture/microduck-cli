# On-box verification — Jetson AGX Orin sanity (2026-09-04)

The six success-signal checks of
[`2026-09-04-sim-bringup.md`](2026-09-04-sim-bringup.md) (the DGX Spark
record), re-run on a Jetson AGX Orin — the host the Spark record's
"What was NOT verified" listed as *"not attempted; the host classifier only"*.
Planned as [`docs/plans/2026-09-04-orin-sanity.md`](../plans/2026-09-04-orin-sanity.md)
from the challenged spec
[`docs/specs/2026-09-04-orin-sanity.md`](../specs/2026-09-04-orin-sanity.md).
Outputs are pasted as produced; nothing here was smoothed.

## The box and the pins

| | |
|---|---|
| host | Jetson AGX Orin, L4T R39 (rev 2.0), GPU "Orin (nvgpu)" sm_87, CUDA 13.2 (driver 595.78), 12 cores, 61 GiB unified |
| power mode | MAXN (probed before and during the run) |
| toolchain | rustup → cargo 1.98.1 / rustc 1.98.1 (installed this run; box had none), uv 0.11.29, Python 3.12.3 |
| `pollen-robotics/microduck` | branch `sim-remote-io` @ `0cd676d6fbb6e90a762c84aa63abe7a02dbc9495` — built natively (debug): robotd 0.10.0, robotctl, tofd, sounds |
| `pollen-robotics/microduck_rl` | branch `develop` @ `29e887ecfbf5d37144759e5a9f8a176dfb83d547` — venv per the Orin recipe below; onnxruntime 1.24.4, mujoco 3.10.0 |
| microduck-cli | `main` @ `3c09fb0` (0.9.1) for every check |
| daemon API | **16** (`hello` → `{"api_version": 16, "daemon_version": "0.10.0"}`) |

### Runtime conditions (per spec claim c13)

The box is shared with the colleague backend's local model server: docker
container `model-gear-vllm-associate` (vLLM, Nemotron-3.5-Lightning-30B,
~50 GiB of the 61 GiB unified memory, up 9 days at probe time). Per the
recorded decision on the frame (q2), it was stopped with the operator's
explicit go-ahead at `09:11:49+03:00` (`docker stop model-gear-vllm-associate`,
memory: 53 Gi used → 2.8 Gi used / 58 Gi available), every check below ran
with the box otherwise idle, and it was restarted at `09:19:00+03:00`
(`docker start`, container healthy again afterwards). `model-gear-gateway`
and `prod-worker-1` (≤35 MiB each) stayed up throughout. Bring-up
(rustup, clones, venv, cargo build) ran *before* the stop, beside the
resident vLLM.

### The Orin RL-venv recipe (operator decision: Jetson index first)

The operator chose NVIDIA's JetPack-7-era / CUDA 13 Jetson index over the
pyproject's `pytorch-cu129` source. `https://pypi.jetson-ai-lab.io/jp7`
redirects into the SBSA tree; `sbsa/cu130` carries
`torch-2.9.1-cp312-linux_aarch64` — the exact pinned version. Mechanism
(no file in the pinned clone modified; `uv.lock` transiently rewritten and
`git restore`d after):

```text
UV_INDEX="pytorch-cu129=https://pypi.jetson-ai-lab.io/sbsa/cu130" uv sync
uv pip install nvidia-cublas nvidia-cuda-runtime nvidia-cuda-nvrtc nvidia-cufft \
    nvidia-curand nvidia-cusolver nvidia-cusparse nvidia-nvjitlink nvidia-nccl-cu13
uv pip install --prerelease=allow nvidia-cudnn-cu13 nvidia-cuda-cupti \
    nvidia-cusparselt-cu13 nvidia-nvtx nvidia-cufile nvidia-nvshmem-cu13 nvidia-cudss-cu13
uv pip install nvpl-lapack nvpl-blas
```

The SBSA wheel declares **no** nvidia deps (it expects JetPack's system CUDA;
this box has the driver only, no toolkit), hence the venv-local runtime libs.
The `*-cu13`-suffixed names for cublas/runtime/nvrtc/cudnn-unsuffixed are
0.0.1 PyPI placeholders whose sdists fail to build — the CUDA-13-era names
are unsuffixed (nccl keeps `-cu13`). NVPL and cudss are linker `DT_NEEDED`
deps, not torch preloads, so every torch invocation needs `LD_LIBRARY_PATH`
over `site-packages/nvidia/*/lib` + `nvpl/lib` (7 dirs). Result:
`torch 2.9.1, cuda-build 13.0, cuda.is_available: True` — but see check 5.

**Operational gotcha:** any bare `uv run` inside the clone re-locks toward
the pyproject's cu129 URL and starts a multi-GB re-download. Every uv call
in the clone must be `uv run --no-sync` or carry the same `UV_INDEX`.
`env up` is immune — `stack.py` invokes `.venv/bin/python -m … body_server`
directly, no uv.

## Bring-up (before-state, probed before installing anything)

`08:27:17+03:00`: no cargo/rustc, no clones, `MICRODUCK_CLONE`/`DUCK_SIM_RL`/
`MICRODUCK_RL_CLONE` unset, 1.4 T disk free, port 7801 free, headless
(no DISPLAY). rustup installed with the operator's prior approval; both
repos cloned as siblings (`~/git/microduck`, `~/git/microduck_rl`) so
doctor's fallback resolution finds them with no env vars. After bring-up:

```text
$ microduck env doctor
microduck-cli env doctor: healthy          # 13/13 ok — clone pins, cargo 1.98,
                                           # daemons built, RL venv + onnxruntime,
                                           # state dir 36 bytes, port free,
[ok] host_class: host class: jetson-agx-orin (NVIDIA Jetson AGX Orin); torch source applies: False
```

The live classifier verdict matches `env/hosts.py` exactly; neither
`hosts.py` nor `doctor.py` was touched (diffs empty at run end).

## Check 1 — `env up --sim` yields a standing duck

```text
$ microduck env up --sim --headless --skip-build
waiting for duck-a to report healthy (/home/orin/.cache/duck-sim/duck-a.sock)...
microduck-cli env up: healthy (sim)
$ microduck duck init --apply --json
{... "summary": "init accepted: ramping to the home pose", "result": {"accepted": true}}
$ microduck duck monitor --frames 2 --json     # 8 s later
{'policy': 'held', 'fallen': False, 'gravity': [-0.0277, -0.0000369, -0.9996], 'z': 0.0687, 'loop': {'hz': 49.999974, 'missed': 0}}
```

**Pass.** MuJoCo `body_server` (port 7801) driven by the real `robotd --sim`,
both first-ever runs on Orin. z 0.0687 — the same reading the Spark run
produced to four decimals; trunk rose to 0.1180 after enable (check 3).

## Check 2 — `duck health --json` reports healthy at 50 Hz

```text
$ microduck duck health --json
{"healthy": true, "degraded": false, "health": {"control_loop": {"target_hz": 50.0,
 "achieved_hz": 49.999974411777806, "ticks": 900, "missed": 0, "last_tick_age_ms": 17}, ...}}
```

**Pass.** 49.99997 Hz, 0 missed over 900 ticks.

## Check 3 — `duck do roulade --apply` runs a skill in sim

```text
$ microduck duck enable --apply --json
{... "summary": "enable {\"on\": true}: enabled — driving", "result": {"accepted": true}}
$ microduck duck do roulade --apply --json
{... "calls": ["robot.do {\"skill\": \"roulade\"}  (one-shot skill 'roulade')"], "result": {"accepted": true}}
$ microduck duck monitor --frames 1 --json     # 4 s later
{"policy": "stand", "fallen": false, "z": 0.1180, "hz": 49.99}
```

**Pass.** Real ONNX policies loaded through the RL venv's onnxruntime
(1.24.4 aarch64), headless. (Spark after-roulade z: 0.1181.)

## Check 4 — a one-rule overlay fires; `rules check` refuses a malformed file

```text
$ microduck rules check --rules duck-rules-bad.toml       # carries fn = "lambda: 1"
## content
- react[0] (id='bad-rule') has unexpected field(s) ['fn']
exit 0                                                    # descriptive verb: content issues never hard-fail
$ microduck rules check --rules duck-rules-test.toml --duck duck-a
## content
- ok
## actions (robot.subscribe (api 16))
- ok
$ microduck rules engine run --duck duck-a --rules duck-rules-test.toml --apply --max-ticks 300 --json
[SENSE stage=rule source=verify-look event=fired] look -> look-1
[SENSE stage=rule source=verify-look event=cooldown] dropped reason=cooldown: fired 0.020s ago, cooldown_s is 5.0
[SENSE stage=rule source=verify-look event=fired] look -> look-2        # at 5.000s, cooldown expiry
{"ticks": 300, "achieved_hz": 49.999419229744525, "overruns": 0,
 "steps": ["connect", "hello", "health", "init", "enable", "armed"]}
$ microduck duck monitor --frames 1 --json
{"head": [0.0, 0.05786974782527796, -2.33e-06, 0.0], "policy": "stand"}
```

**Pass.** `verify-look` (fallen is_false → look {x 0.5, z 0.1}, cooldown 5 s,
duration 2 s) fired on the first tick, was cooldown-held with a named drop
every tick (none silent), re-fired exactly at expiry, and the head pitch
target moved to 0.0579 — the same value the Spark run produced. Start
sequence logged in order: connect, hello, health, init, enable, armed.

## Check 5 — the policy smoke: **FAIL on this host, recorded as the finding it is**

Attempted per the operator's decision (frame q1: attempt, record honestly;
plan risk r1: explicit timeout). `WANDB_MODE=offline`, timeout 900 s — it
failed cleanly in 37 s, no hang:

```text
$ WANDB_MODE=offline microduck policy smoke Mjlab-Velocity-Flat-MicroDuck --json
argv: uv run train Mjlab-Velocity-Flat-MicroDuck --env.scene.num-envs 64 --agent.max_iterations 5
ok: false, returncode 1
Warp 1.12.0 initialized: CUDA Toolkit 12.9, Driver 13.2, "cuda:0": "Orin" (61 GiB, sm_87, mempool enabled)
UserWarning: Orin with CUDA capability sm_87 is not compatible with the current PyTorch installation.
The current PyTorch install supports CUDA capabilities sm_110 sm_121.
torch.AcceleratorError: CUDA error: no kernel image is available for execution on the device
```

**The finding (settles the plan's r2 / the spec's parked torch risk):** the
SBSA `cu130` torch wheel installs, imports, and initializes CUDA on Orin —
but ships kernels for sm_110/sm_121 (Thor-class / SBSA server GPUs) only,
not Orin's sm_87. The fallback tree was probed and is closed too:
`jp6/cu129` carries torch 2.9.1 only as cp310; cp312 tops out at 2.8.0,
while the pin requires torch==2.9.1 on Python 3.12. **No compatible torch
wheel exists for sm_87 + cp312 + torch==2.9.1 on the Jetson indexes — GPU
training on AGX Orin is not available at this pin.** Warp itself is fine on
sm_87. `env/hosts.py`'s `torch_source_applies=False` + "unverified on this
host class" remediation is thus confirmed as *correct and now verified* —
the correct verdict for Orin is "does not work", and the classifier already
refuses to claim otherwise.

The HF Jobs half (`--hf-jobs --dry-run`) was **not attempted**: `hf auth`
has no session on this box (`Token is required … no token found`, captured
verbatim). Nothing was submitted, nothing billed.

## Check 6 — the gates

Run by a subagent in a disposable worktree at the same commit `3c09fb0`:

```text
$ uv run pytest -n auto --cov=microduck_cli    1098 passed, 0 failed; TOTAL 92.80% (gate 60)
$ uv run black --check / isort --check-only / flake8 / bandit    all clean (13822 LOC, 0 findings)
$ uv run teken cli doctor . --strict           healthy: 26/26 passed, 0 errors, 0 warnings
$ python3 -c "..."                             dependencies = []
$ markdownlint-cli2                            not installed on this box (command not found) — not run
```

**Pass** for every gate present on the box; markdownlint is recorded as not
installed rather than silently skipped (CI still runs it).

## The fake-body path (run first, as on Spark)

`env up --fake` healthy; `duck version` (API 16, skew null), `health`,
`init` (dry-run on a pipe: *"No sockets opened, no calls sent"*, then
`--apply` accepted), `enable`, `do roulade`, `move` (20 intents at 20 Hz,
then `robot.stop`), `rules list` (3 shipped rules), `rules check --duck`
(content + actions ok via `robot.subscribe`), `rules intent move
--payload '{"vx": 9}' --apply` refused verbatim — `move.vx out of range:
9.0 (allowed [-0.3, 0.3] m/s)` with the one-registry remediation —
`rules engine run --max-ticks 300` (six steps in order, 300 ticks,
achieved_hz 49.9995, 0 overruns, capacity_hz 5535), `duck record --seconds 2`
(hello/state/health JSONL on stdout; pad/tof absences as named stderr drops),
`env down` (terminated, port 7801 free after).

## The live suite

```text
$ MICRODUCK_LIVE=1 MICRODUCK_LIVE_SIM=1 uv run pytest -m live -n0 -q
12 passed, 1 skipped, 1098 deselected in 25.84s     # walking test needs the sim body module-wide
$ MICRODUCK_LIVE=1 MICRODUCK_LIVE_BODY=sim MICRODUCK_LIVE_SIM=1 uv run pytest -m live -n0 -q
12 passed, 1098 deselected, 1 xfailed in 29.79s
```

**12 passed on both runs — the same count as Spark.** The walking sentinel
(`test_sim_body_walks_forward_on_move`) reports **xfailed**, as expected:
locomotion-not-achieved is a pin-level defect (see the Spark record's
"Walking … not achieved" section), now confirmed host-independent. No XPASS —
walking has not silently arrived.

## What was NOT verified

- **GPU training on this host** — check 5's finding above: impossible at
  this pin (sm_87 vs the available wheels), not merely unattempted.
- **The HF Jobs dry-run** — no `hf` session on this box; the command shape
  was verified on Spark only.
- **markdownlint** locally — not installed here; CI's lint job still gates it.
- **No physical duck; multi-duck, ether, cameras, ToF** — same standing
  limits as the Spark record.
- **Walking in the MuJoCo body** — still not achieved at this pin, on this
  host as on Spark; the xfail sentinel stands.

Raw logs live in the session scratchpad ledger (`orin-sanity/t*.log`); every
number above is copied from them unchanged.
