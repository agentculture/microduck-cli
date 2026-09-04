# On-box verification — simulation bring-up (2026-09-04)

Plan task t23 of
[`docs/plans/2026-09-03-microduck-cli-env-teach-operate-rules.md`](../plans/2026-09-03-microduck-cli-env-teach-operate-rules.md):
the six success-signal checks (spec claim c30) run on the development box against
the **real** daemon built from the pinned upstream commit, first with the
`--fake` body, then with the MuJoCo body. Outputs are pasted as produced; nothing
here was smoothed.

## The box and the pins

| | |
|---|---|
| host | DGX Spark, NVIDIA GB10, linux-aarch64, CUDA 13.0 (driver 580.126) |
| toolchain | Rust 1.93.1, uv, Python 3.12.12 |
| `pollen-robotics/microduck` | branch `sim-remote-io` @ `0cd676d6fbb6e90a762c84aa63abe7a02dbc9495` — built natively: robotd 0.10.0, robotctl, tofd, sounds |
| `pollen-robotics/microduck_rl` | branch `develop` @ `29e887ecfbf5d37144759e5a9f8a176dfb83d547` — `uv sync` done; onnxruntime 1.24.4 aarch64 wheel present |
| microduck-cli | `duck/integration` at the commits named per check below |
| daemon API | **16** (`hello` → `{"api_version": 16, "daemon_version": "0.10.0"}`) |

### API-16 limits, recorded verbatim (approved deviation d1)

On this pinned build `robot.policies`, `robot.skills`, `robot.loadPolicy`,
`robot.setSkill`, `robot.removeSkill`, `policy.*` and `account.*` answer
`{"code": -32601, "message": "unknown method \"robot.policies\""}`. The skills
list is read from `robot.subscribe`'s result (walk / stand / unavailable + per-skill
file names). `hello` requires `{"api_version": u32}`; `robot.subscribe`'s `hz`
must be an integer (a float is refused with `invalid type: floating point \`50.0\`,
expected u32`). The policy noun's channel verbs exit 2 on this build with the
`needs API >= 18` remediation.

## Check 1 — `env up --sim` yields a standing duck

CLI commit `420dc5c`. `MICRODUCK_CLONE`/`DUCK_SIM_RL` point at the two clones above.

```text
$ microduck env doctor
microduck-cli env doctor: healthy            # 13/13 checks ok (clone pins, cargo 1.93, daemons built,
                                             # RL venv + onnxruntime, state dir 37 bytes, port 7801 free,
                                             # host gb10 torch-source applies, hf signed in, wandb unset)
$ microduck env up --sim --headless --skip-build
waiting for duck-a to report healthy (/home/spark/.cache/duck-sim/duck-a.sock)...
microduck-cli env up: healthy (sim)
  duck-a: /home/spark/.cache/duck-sim/duck-a.sock
$ microduck duck init --apply --json
{... "summary": "init accepted: ramping to the home pose", "result": {"accepted": true}}
$ microduck duck monitor --frames 2 --json     # 8 s later
{'policy': 'held', 'fallen': False, 'gravity': [-0.028, -0.00004, -0.9996], 'z': 0.0687, 'loop': {'hz': 50.03, 'missed': 0}}
```

**Pass.** The body is MuJoCo (`body_server` from microduck_rl, port 7801) driven by
the real `robotd --sim`; the loop holds 50.03 Hz with 0 missed. Trunk height rose
to 0.118 m and `policy` went `held` → `stand` after enable (check 3).

## Check 2 — `duck health --json` reports healthy at 50 Hz

```text
$ microduck duck health --json
{"duck": "duck-a", "healthy": true, "degraded": false, "reason": null, "health": {"control_loop":
 {"target_hz": 50.0, "achieved_hz": null, "ticks": 21, "missed": 0, "last_tick_age_ms": 5}, ...}}
$ microduck duck health          # after enable
- loop     : target 50.0 Hz, achieved 50.030143259412114, 453 ticks, 0 missed
```

**Pass.** (`achieved_hz` is null in the daemon's first second, then 50.03.)

## Check 3 — `duck do roulade --apply` runs a skill in sim

```text
$ microduck duck enable --apply --json
{... "summary": "enable {\"on\": true}: enabled — driving", "result": {"accepted": true, "reason": "enabled — driving"}}
$ microduck duck do roulade --apply --json
{... "calls": ["robot.do {\"skill\": \"roulade\"}  (one-shot skill 'roulade')"], "result": {"accepted": true}}
$ microduck duck monitor --frames 2 --json     # 4 s later
{'policy': 'stand', 'fallen': False, 'z': 0.1181}
```

**Pass.** The skill was accepted by the daemon with the real ONNX policies loaded
(the generated params file names the clone's `policies/*.onnx`; ORT dlopened from
the RL venv). Whether the roll completed was not measured beyond "not fallen,
standing afterwards" — the sim ran headless.

## Check 4 — a one-rule overlay fires; `rules check` refuses a malformed file

```text
$ microduck rules check --rules /tmp/duck-rules-bad.toml       # carries fn = "lambda: 1"
## content
- react[0] (id='bad-rule') has unexpected field(s) ['fn']
exit 0                                                         # descriptive verb: content issues never hard-fail
$ microduck rules check --rules /tmp/duck-rules-test.toml --duck duck-a
## content
- ok
## actions (robot.subscribe (api 16))
- ok
$ microduck rules engine run --duck duck-a --rules /tmp/duck-rules-test.toml --apply --max-ticks 300 --json
{'ticks': 300, 'achieved_hz': 50.0, 'overruns': 0}
[SENSE stage=rule source=verify-look event=fired] look -> look-1
[SENSE stage=rule source=verify-look event=cooldown] dropped reason=cooldown: fired 0.020s ago, cooldown_s is 5.0
$ microduck duck monitor --frames 2 --json
{'head': [0.0, 0.0579, -0.0000023, 0.0], 'policy': 'stand'}
```

**Pass.** The overlay rule `verify-look` (when `fallen` is false → `look`
{x 0.5, y 0, z 0.1}, cooldown 5 s, duration 2 s) fired on the first tick, was
then held by its cooldown for the rest of the 6 s run (229 named `cooldown` drops,
none silent), and the head pitch target moved. Start sequence logged in order:
connect, hello, health, init, enable, armed.

## Check 5 — the smoke test runs locally, and the same command submits to HF Jobs

CLI commit `39af5ce` (the first attempt at `420dc5c` failed: the train lane did
not honour `DUCK_SIM_RL` and leaked `unexpected: FileNotFoundError` — fixed in
`39af5ce`, which is why this record exists).

```text
$ WANDB_MODE=offline microduck policy smoke Mjlab-Velocity-Flat-MicroDuck --json
argv: uv run train Mjlab-Velocity-Flat-MicroDuck --env.scene.num-envs 64 --agent.max_iterations 5
ok: true                                   real 0m58.6s on the GB10; wandb offline run recorded
$ cd $DUCK_SIM_RL && uv run train Mjlab-Velocity-Flat-MicroDuck --env.scene.num-envs 64 \
    --agent.max_iterations 5 --hf-jobs --dry-run --namespace orinachum --no-wandb
[src] building tarball -> src-20260904-002138.tar.gz (from /home/spark/git/microduck_rl)  HEAD=29e887e, 9.1 MB
[dry-run] would submit job:  flavor: l4x1, timeout: 12h  image: pytorch/pytorch:2.5.1-cuda12.4-cudnn9-runtime
  env: {'TRAIN_ARGS': 'Mjlab-Velocity-Flat-MicroDuck --env.scene.num-envs 64 --agent.max_iterations 5', ...}
exit 0
```

**Pass for the local half; dry run only for HF Jobs.** No job was submitted and
nothing was billed — the dry run proves the command shape and the tarball, not a
remote run. A real submission is an operator decision.

## Check 6 — the gates

```text
$ uv run teken cli doctor . --strict        healthy: 26/26 passed, 0 errors, 0 warnings
$ uv run black --check / isort --check-only / flake8 / bandit    all clean
$ uv run pytest -n auto --cov=microduck_cli   998 passed;  TOTAL 93% (gate 60)
$ python3 -c "..."                              dependencies = []
$ markdownlint-cli2 "**/*.md" ...
Summary: 0 error(s)
```

**Pass.**

## The fake-body path (check 1 on `--fake`, run first)

CLI commit `5ad8bb6`: `env up --fake` healthy; `duck version`, `health`,
`init` (dry-run then `--apply`), `enable`, `do roulade`, `move` (20 notifications
at 20 Hz then `robot.stop`), `rules list`, `rules check --duck`, `rules intent`
(over-limit refused verbatim), `rules engine run` (six steps in order),
`duck record` (hello, state, health records), `env down` (terminated, no leftovers).
That run found two defects, both fixed before the sim run: the engine reported
`5935 Hz achieved` (the metric measured work capacity, not cadence — now
`achieved_hz` is the wall-clock cadence and the old figure is `capacity_hz`), and
the intent payload named the yaw axis `wz` where the wire and the upstream docs say
`vyaw` (renamed).

## What was NOT verified

- **The unit suite (998 tests) runs against the in-process Python fake, not a
  real socket.** What runs against the real daemon is the opt-in live suite,
  `tests/live/test_live_cli.py` (`MICRODUCK_LIVE=1 uv run pytest -m live -n0`,
  plus `MICRODUCK_LIVE_SIM=1` for the MuJoCo body): 12 checks operating the CLI as
  subprocesses — doctor, version, health at 50 Hz, init dry-run then apply, enable
  and a skill, move and stop, `rules check` via `robot.subscribe`, the verbatim
  over-limit refusal, the six-step engine start at 50 Hz, pure-JSONL record,
  status/down, and the sim body standing the duck up. **12 passed, 0 failed** on
  this box at commit `25114a2` (2026-09-04 00:48 local). The optional
  `real-daemon` CI job (`.github/workflows/tests.yml`, gated on the
  `MICRODUCK_REAL_DAEMON` variable) runs that suite.
- **No physical duck.** Everything here is the `--fake` and MuJoCo bodies.
- **Multi-duck, the ether, cameras and ToF** in sim — upstream marks them
  "designed and measured but not built" on this branch; not attempted.
- **Jetson AGX Thor** — verified later the same day, all three tiers, in
  [`2026-09-04-thor-sanity.md`](2026-09-04-thor-sanity.md) (training needed a
  local torch-source override of the RL clone). **AGX Orin** — verified the
  same day in [`2026-09-04-orin-sanity.md`](2026-09-04-orin-sanity.md): checks
  1-4 pass, GPU training is not available there at this pin (sm_87).

Raw logs from the runs are kept outside the repo (session scratchpad); the
numbers above are copied from them unchanged.

## Walking in the MuJoCo body — not achieved at this pin (2026-09-04, later run)

Sampled at 25 Hz over the daemon's state stream during `duck move --vx 0.15`
(CLI commit `bd8b011`), and again with upstream's own `scripts/duck-sim drive`
command shape (`robot.move {vx 0.15, vy 0, vyaw 0}` at 10 Hz for 8 s):

```text
policy: walk   move.requested [0.15, 0, 0]   move.applied [0.15, 0, 0]   fallen: false
left-knee target over 97 walk frames:   min -0.09  max -0.05  rad
hip-pitch target over 97 walk frames:   min -0.36  max -0.33  rad
odom x: 0.065 -> 0.072 m over 4.4 s     (two screenshots 4 s apart: same tile)
sim real-time factor (upstream's probe): 1.00
```

The twist reaches the daemon and the walk network is selected, but its joint
targets are static — no gait — so the duck stands where it is. The earlier
"it walked" reading in this session was a duck that had toppled at `enable`,
thrashed under the recovery policy and slid 0.6 m; not locomotion.

Ruled out: the CLI's command shape (identical to upstream's `drive`), the
generated params (policy section identical to the launcher's), the keyframe
(the body defaults to `SIT` as upstream does), the real-time factor (1.00,
windowed and headless), and the viewer (same result headless).

Upstream's own launcher could not be used as a control: at this pin pair
`scripts/duck-sim` (microduck `0cd676d`) starts the body with `--cameras` and
`--frame-port`, which the pinned `microduck_rl` (`29e887e`) `body_server` does
not accept — the two pinned commits disagree with each other. Our `env up`
does not pass those flags and is the path that works.

**Status:** stand-up, skills, rules and every verb are verified on the sim
body; locomotion is not. `tests/live/test_live_cli.py::test_sim_body_walks_forward_on_move`
is kept as a non-strict `xfail` sentinel: an XPASS after a re-pin means walking
arrived and the mark should be dropped.
