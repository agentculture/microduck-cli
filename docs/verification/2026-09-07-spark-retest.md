# On-box verification — DGX Spark re-test, following the tutorial (2026-09-07)

The first of the three re-tests planned in
[`docs/plans/2026-09-07-jetson-retest-and-ai-lab-tutorial.md`](../plans/2026-09-07-jetson-retest-and-ai-lab-tutorial.md)
(spec [`docs/specs/2026-09-07-jetson-retest-and-ai-lab-tutorial.md`](../specs/2026-09-07-jetson-retest-and-ai-lab-tutorial.md),
task t4). Unlike the 2026-09-04 records, this run did **not** drive the CLI from
its checkout: it followed the Jetson AI Lab tutorial draft (`microduck-on-jetson`)
step by step, on the **PyPI wheel** installed with `uv tool install`, with the
MuJoCo viewer **windowed** on the box's own display, and it fixed the tutorial
wherever the run showed it misled. Outputs are copied from the run logs
unchanged; excerpts are marked (`...`, `{...}`); nothing was reworded. Home
directories appear as they were printed, because that is what a reader sees.

## The box and the pins

| | |
|---|---|
| host | DGX Spark, NVIDIA GB10, linux-aarch64, 121 GB unified memory, GNOME on Xorg (`DISPLAY=:1`, seat0 session live) |
| CLI under test | **`microduck-cli==0.9.4` from PyPI** (`uv tool install`), `~/.local/bin/microduck`; the live suites ran from the repo checkout at `61a564c` (branch `docs/jetson-retest-and-ai-lab-tutorial`, code identical to 0.9.4 plus `docs/tools/`) |
| `pollen-robotics/microduck` | `sim-remote-io` @ `0cd676d6fbb6e90a762c84aa63abe7a02dbc9495` (unchanged since the pin; still the branch head on 2026-09-07) |
| `pollen-robotics/microduck_rl` | `develop` @ `29e887ecfbf5d37144759e5a9f8a176dfb83d547` (unchanged; PR #39 still open) |
| daemon API | 16 |
| shared box | `model-gear-vllm-primary` (Qwen3.8-27B), `model-gear-vllm-rerank`, eidetic and mesh containers resident; `free -g` "available" was 19 GB at pre-flight, 17 GB before the smoke |

## Step 1 — install the CLI from PyPI

```text
$ uv tool install microduck-cli==0.9.4
 + microduck-cli==0.9.4
Installed 2 executables: microduck, microduck-cli
$ microduck --version
microduck-cli 0.9.4
```

**Pass.** The tutorial's install line works as written.

## Steps 2–4 — clones, toolchain, RL venv (pre-existing on this box)

Both clones were already present at the pinned commits, the daemons built and
the RL venv synced from the 2026-09-04 run, so the `git clone`, `cargo build`
and `uv sync` lines were **not re-run**; their results were checked instead:

```text
$ git -C ~/git/microduck rev-parse HEAD
0cd676d6fbb6e90a762c84aa63abe7a02dbc9495
$ git -C ~/git/microduck_rl rev-parse HEAD
29e887ecfbf5d37144759e5a9f8a176dfb83d547
$ source ~/.cargo/env; cargo --version
cargo 1.93.1 (083ac5135 2025-12-15)
$ ~/git/microduck_rl/.venv/bin/python -c 'import torch, onnxruntime, mujoco; ...'
torch 2.9.1+cu129 cuda True | onnxruntime 1.24.4 | mujoco 3.10.0
```

The tutorial's two exports were set for every later command:

```text
$ export MICRODUCK_CLONE=~/git/microduck
$ export DUCK_SIM_RL=~/git/microduck_rl
```

## Step 5 — `env doctor` on the wheel: healthy, with two `[FAIL]` lines

```text
$ microduck env doctor
microduck-cli env doctor: healthy

[FAIL] microduck_pinned_commit: pinned commit for microduck is unknown (docs/upstream-pins.md unreadable or the row is missing)
  hint: check that docs/upstream-pins.md is present and its table is well-formed
[FAIL] rl_pinned_commit: pinned commit for microduck_rl is unknown (docs/upstream-pins.md unreadable or the row is missing)
  hint: check that docs/upstream-pins.md is present and its table is well-formed
...                                               (the other eleven checks: [ok])
exit 0
```

**Pass with a finding.** `env/doctor.py` reads the pins from
`<package>/../../docs/upstream-pins.md`, which a wheel does not ship, so both
pin checks degrade to *"pinned commit unknown"*. That check is `severity =
warning`, so the verdict stays `healthy` and the exit code `0` — but the line is
printed with the `[FAIL]` prefix, which contradicts the summary a reader sees
one line above. Every checkout-based run (all three 2026-09-04 records) had the
file and never met this. Filed as a CLI defect for its own PR: ship the pins
inside the package and render warnings as `[WARN]`. The tutorial now says what a
wheel reader will see at this step.

## Step 6 — `env up --sim`, windowed: the first attempt died with the screen lock

The first windowed attempt came up healthy in 1.4 s, `init` and `enable` were
accepted, and then `duck monitor --frames 5 --json` never returned. Six minutes
later the desktop showed the GNOME **lock screen** (idle-delay 300 s, lock
enabled), the body process was gone from `/proc` and `robotd` was logging
`bus write failed … Connection refused` against the body's port from
`00:55:39Z` on:

```text
$ microduck env up --sim --skip-build
waiting for duck-a to report healthy (/home/spark/.cache/duck-sim/duck-a.sock)...
microduck-cli env up: healthy (sim)
real    0m1.448s
$ microduck duck health
- loop     : target 50.0 Hz, achieved None, 19 ticks, 0 missed
- imu      : not ready
$ microduck env down --json
{... "body": {"outcome": "gone", "detail": "no /proc entry; the process had already exited"}, "body_port_still_listening": false}
```

body.log's last lines were `behind real time by 1.95s (x1) — fewer ducks, or
--headless` and the daemon connecting; no traceback. The reading is that the
viewer window was torn down when the session locked and the body exited with it.
Two things came out of this for the tutorial: the desktop session must be
**unlocked** and must not lock during a run, and `env up` returning `healthy`
does not mean the IMU is ready yet — give the body a few seconds before judging
`health`.

For the second attempt the session was unlocked (`loginctl unlock-session`) and,
for the duration of the run only, `idle-delay` was set to 0 and `lock-enabled`
to false; both were reverted afterwards (300 / true, 06:40:04+03:00).

## Step 6 (second attempt) and Step 7 — a standing duck in a window

```text
$ microduck env up --sim --skip-build
waiting for duck-a to report healthy (/home/spark/.cache/duck-sim/duck-a.sock)...
microduck-cli env up: healthy (sim)
$ xwininfo -root -tree | grep -i mujoco
     0x6000be "MuJoCo : scene": ("mutter-x11-frames" "mutter-x11-frames")  1468x1026+102+70  +102+70
        0x3c0000b "MuJoCo : scene": ("MuJoCo" "MuJoCo")  1440x960+14+49  +116+119
$ microduck duck health
# duck-a: healthy
- loop     : target 50.0 Hz, achieved 50.040757853855, 170 ticks, 0 missed
- imu      : ready
$ microduck duck init --apply
init accepted: ramping to the home pose
$ microduck duck enable --apply
enable {"on": true}: enabled — driving
$ microduck duck monitor --frames 5 --json          # last frame, condensed
{"loop": {"hz": 50.007954402504, "missed": 0}, "odom": {"position": [-0.0999, 0.0372, 0.11805866663491964], ...}, "policy": "stand", "safety": {"fallen": false, ...}}
$ gnome-screenshot -w -f microduck-spark-stand-1.png   # then 5 s later -2.png
$ microduck duck health
- loop     : target 50.0 Hz, achieved 49.9984078507004, 1141 ticks, 0 missed
```

**Pass.** Trunk height `0.1181` m under `stand`, the same figure as Spark,
Thor and Orin on 2026-09-04. The two window-only screenshots (1468×1026, the
MuJoCo viewer and nothing else) are the tutorial's images,
`public/images/tutorials/microduck-on-jetson/microduck-spark-stand-{1,2}.png`
in the Jetson AI Lab fork; the duck is in the same place in both.

## Step 8 — look, skills, move, quack, record

```text
$ microduck duck look --x 0.2 --y 0.4 --z -0.1 --apply
looking at (0.2, 0.4, -0.1); head {'head_pitch': 0.9883019891520989, 'head_roll': 0.0, 'head_yaw': 1.015663333146598, 'neck_pitch': 0.0}
$ microduck rules check --duck duck-a
## actions (robot.subscribe (api 16))
- ok
$ microduck duck do roulade --apply
do roulade (roulade): accepted
$ microduck duck move --vx 0.15 --duration 3 --apply
moved {"vx": 0.15, "vy": 0.0, "vyaw": 0.0} for 3s (60 intents at 20 Hz), then stopped
$ microduck duck quack
quack: duck-a (tag chirp)
$ microduck duck record --seconds 5 > senses.jsonl
[SENSE stage=record source=pad event=record-source-absent] dropped reason=record-source-absent: no link to padd (DUCK_PAD_SOCKET, /run/padd): pad.input is not recorded
[SENSE stage=record source=tof event=record-source-absent] dropped reason=record-source-absent: no link to tofd (<state>/<duck>-tof.sock): tof.stream is not recorded
recorded 174 records over 5.00s to - (health=10, hello=1, remote=5, state=158)
```

**Pass.** All exit 0; `senses.jsonl` has 174 lines and the first parses as
JSON. The two `record-source-absent` drops are expected in simulation (no pad,
no ToF daemon) and go to stderr, so stdout stays pure JSONL; the tutorial now
says so.

## Step 9 — the rules layer and the tick engine

With the tutorial's `my-rules.toml` (one `react` rule, `verify-look`):

```text
$ microduck rules check --rules ./my-rules.toml --duck duck-a
overlay: ./my-rules.toml
## content
- ok
## actions (robot.subscribe (api 16))
- ok
$ microduck rules engine run --duck duck-a --rules ./my-rules.toml --apply --max-ticks 300 --json
{"duck": "duck-a", ..., "steps": [connect, hello, health, init, enable, armed], "ticks": 300, "metrics": {"ticks": 300, "period_s": 0.02, "overruns": 0, "max_tick_ms": 1.5421799616888165, "mean_tick_ms": 0.31809479657871026, "achieved_hz": 49.99846343326588, "capacity_hz": 3143.7169383327437}}
--- stderr (first lines of 747):
[SENSE stage=start source=/home/spark/.cache/duck-sim/duck-a.sock event=connect] opening the robot control socket
[SENSE stage=start source=/home/spark/.cache/duck-sim/duck-a.sock event=hello] daemon api_version=16
[SENSE stage=start source=/home/spark/.cache/duck-sim/duck-a.sock event=health] healthy
[SENSE stage=start source=/home/spark/.cache/duck-sim/duck-a.sock event=init] robot.init
[SENSE stage=start source=/home/spark/.cache/duck-sim/duck-a.sock event=enable] enabled — driving
[SENSE stage=start source=/home/spark/.cache/duck-sim/duck-a.sock event=armed] 4 rule(s) loaded, idle registered
[SENSE stage=rule source=verify-look event=fired] look -> look-1
[SENSE stage=rule source=verify-look event=cooldown] dropped reason=cooldown: fired 0.020s ago, cooldown_s is 5.0
$ microduck rules intent stop
intent stop: admitted
```

Event tally on stderr: `fired` 2, `cooldown` 210, `inhibits` 528, one of each
start step, one `no-heartbeat` (no earlier engine; expected). **Pass.** The
tutorial's sample output block was replaced with these real lines (it had a
Python-dict rendering of the summary that the CLI does not print).

## Step 10 — the training smoke: failed on memory, then passed with an engine paused

Attempt 1, beside the resident vLLM engines (`free -g`: 17 GB available, 6 GB
swap in use):

```text
$ WANDB_MODE=offline microduck policy smoke Mjlab-Velocity-Flat-MicroDuck --json
{"argv": ["uv", "run", "train", "Mjlab-Velocity-Flat-MicroDuck", "--env.scene.num-envs", "64", "--agent.max_iterations", "5"], "cwd": "/home/spark/git/microduck_rl", "ok": false, "returncode": 1}
     "cuda:0"   : "NVIDIA GB10" (0 GiB, sm_121, mempool enabled)
Warp CUDA error 2: out of memory (in function wp_cuda_device_get_memory_info, /builds/omniverse/warp/warp/native/warp.cu:2142)
torch.AcceleratorError: CUDA error: out of memory
exit 1        elapsed 0:08.80, max RSS 1.1 GB
```

**Fail, honestly recorded.** Warp saw the GPU with **0 GiB** free: on a
unified-memory box the resident vLLM engines hold the device memory even when
`free -g` still shows headroom. Plan risk r4 ("Spark has ~19 GB available today
… may need a lobe pause with the user's go-ahead") came true.

Attempt 2, with the operator's explicit go-ahead to stop one engine for the
smoke and restart it afterwards:

```text
$ docker stop model-gear-vllm-primary   # 2026-09-07T06:34:22+03:00
$ free -g
Mem:             121          30          74           0          19          91
$ WANDB_MODE=offline microduck policy smoke Mjlab-Velocity-Flat-MicroDuck --json   # 06:34:37+03:00
{"argv": [...], "cwd": "/home/spark/git/microduck_rl", "ok": true, "returncode": 0}
[INFO] Training with: device=cuda:0, seed=42, rank=0
     "cuda:0"   : "NVIDIA GB10" (122 GiB, sm_121, mempool enabled)
                             Learning iteration 0/5
                         Iteration time: 1.27s
...
                             Learning iteration 4/5
exit 0        elapsed 0:16.29, max RSS 3.2 GB      (2026-09-04: real 0m58.6s on a cold warp cache)
$ docker start model-gear-vllm-primary   # 06:34:54+03:00
vllm-primary health: healthy after ~250s  # 06:38:55+03:00
```

**Pass.** Five iterations at 64 envs in 16 s (the kernel cache was warm from
2026-09-04). The engine was down for 4 min 33 s and came back healthy. The
tutorial's Spark tab now names the failure signature and the cause.

## Step 11 — tear-down

```text
$ microduck env down
microduck-cli env down: state dir /home/spark/.cache/duck-sim
  duck-a: terminated
  body: terminated
$ microduck env status
microduck-cli env status: state dir /home/spark/.cache/duck-sim
$ pgrep -af 'robotd|body_server'
(nothing)
```

**Pass.**

## The live suites (from the repo checkout at `61a564c`)

```text
$ MICRODUCK_LIVE=1 uv run pytest -m live -n0 -v tests/live               # fake body
======================== 11 passed, 2 skipped in 9.18s =========================
$ MICRODUCK_LIVE=1 MICRODUCK_LIVE_BODY=sim MICRODUCK_LIVE_SIM=1 MICRODUCK_LIVE_HEADLESS=0 uv run pytest -m live -n0 -v tests/live   # MuJoCo body, WINDOWED
test_sim_body_stands_the_duck_up PASSED
test_sim_body_walks_forward_on_move XFAIL
======================== 12 passed, 1 xfailed in 25.34s ========================
$ pgrep -af 'robotd|body_server'
(nothing)
```

**Pass.** The walking sentinel stayed `xfail` with its pinned reason; no XPASS,
so nothing about locomotion changed. `MICRODUCK_LIVE_HEADLESS=0` ran the body
with its window on `:1`.

## Check 6 — the gates, at `61a564c`

```text
black --check microduck_cli tests docs/tools        99 files would be left unchanged.
isort --check-only microduck_cli tests docs/tools   exit 0
flake8 microduck_cli tests docs/tools               exit 0
bandit -q -c pyproject.toml -r microduck_cli        exit 0 (two "Test in comment" warnings, as before)
teken cli doctor . --strict                         exit 0
pytest -n auto --cov=microduck_cli                  1112 passed;  TOTAL 93% (gate 60)
markdownlint-cli2 "**/*.md" ...                     Summary: 0 error(s)
dependencies = []
```

**Pass.**

## What changed because of this run

- **The tutorial** (fork branch `docs/microduck-on-jetson`): Step 5 says what a
  wheel install prints for the two pin checks; Step 6 says the session must be
  unlocked and not lock mid-run, and that `env up` returns before the IMU is
  ready; Step 8 names the two expected `record-source-absent` drops; Step 9's
  sample output is the real one; the Spark training tab carries the
  out-of-memory signature and its cause, and the 16 s figure beside the
  2026-09-04 one; a troubleshooting entry covers each. Spark's tabs are now
  "verified 2026-09-07, viewer window open" truthfully.
- **This repo:** `env/hosts.py`'s GB10 `verified` pointer and README's Spark
  row point here (task t6).
- **The command check.** `docs/tools/check_tutorial.py` greps every fenced
  `bash` line of the tutorial against the records, `docs/operating-the-duck.md`,
  the `operate-microduck` skill and the README, matching whole command entries
  (a `$ `-prompt line or a fenced `bash` line, comments stripped, `uv run` prefix
  ignored), never substrings: 26 checked command lines, 26 hit, 0 miss. Eight
  fences are marked `nocheck` because they hold provisioning or
  reader-specific placeholders no record quotes verbatim (`mkdir`/`git clone`/
  `git checkout` of the pins, the rustup one-liner, the Thor fork-branch clone,
  `docker ps`, the two RL-venv `cd` + `uv sync` fences, and the `export DISPLAY=<…>` /
  `gnome-screenshot` recipe). Two
  `env up` variants lost their trailing `#` comments so the lines match as
  written. Identity scan (`/home/`, the box hostname) over the tutorial and the
  image names: 0 hits.
- **A CLI defect filed** for its own PR (spec c10): the pins are read from a
  docs file the wheel does not ship, and a `warning`-severity check prints
  `[FAIL]` under a `healthy` verdict.

## What was NOT verified

- **Steps 2–4 as commands.** Clones, toolchain and venv pre-existed; the
  tutorial's `git clone` / `cargo build` / `uv sync` lines were checked by
  result, not re-run here (they were run from scratch on Thor and Orin on
  2026-09-04).
- **The smoke beside a running vLLM engine.** It fails on memory; the pass
  needed `model-gear-vllm-primary` stopped for four and a half minutes.
- **Walking**, unchanged at this pin pair.
- **Thor and Orin** — the operator's re-run, following this same tutorial, is
  the next task (t8); their 2026-09-04 records stand until then.
- **No physical duck.**

The complete logs (`00-preflight`, `01-install`, `02-steps2-5`,
`03-steps6-7`, `04-steps8-9`, `05-step10`, `05b-step10-attempt2`,
`06-step11`, `07-live-suites`, `08-gates`, `smoke.json`, `smoke2.json`,
`engine.stderr`, `senses.jsonl`, the two PNGs and a diagnostic desktop
capture) are kept in the session scratchpad outside the repo; every line above
is copied from them unchanged.
