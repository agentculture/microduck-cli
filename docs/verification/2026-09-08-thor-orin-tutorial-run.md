# Jetson AGX Thor and AGX Orin, 2026-09-08: the tutorial's operator run

The Jetson AI Lab tutorial draft
([NVIDIA-AI-IOT/jetson-ai-lab#444](https://github.com/NVIDIA-AI-IOT/jetson-ai-lab/pull/444))
was followed by the operator on Jetson AGX Thor on the **PyPI 0.9.4 wheel**
(`~/.local/bin/microduck`, `uv tool install`), and its review comments asked
for numbers this record supplies: the Thor smoke on the wheel, and the
simulation's memory footprint on both Jetson boards. Everything below was
run over SSH by the agent at the operator's request, except the first smoke
attempt, which the operator ran and saved as `1.json`.

| | Jetson AGX Thor | Jetson AGX Orin |
|---|---|---|
| host | "NVIDIA Thor", JetPack 7 / L4T R38.2.2, kernel `6.8.12-tegra`, CUDA 13.0, 122 GB unified | "Orin (nvgpu)" sm_87, L4T R39, kernel `6.8.12-1021-tegra`, 61 GB unified |
| CLI | `microduck-cli 0.9.4` from PyPI | `microduck-cli 0.9.4` from PyPI |
| `microduck` clone | `0cd676d` (the pin) | `0cd676d` (the pin) |
| `microduck_rl` clone | `a30a9e4` (`fix/jetson-thor-torch-source`, [microduck_rl#39](https://github.com/pollen-robotics/microduck_rl/pull/39)) | `a30a9e4` (same branch) |
| shared box | the production stack (`prod-*`, `model-gear-*` vLLM engines) up throughout | `model-gear-*` and `prod-*` containers up throughout; 8 GB available |

## Thor: the smoke failed first, on a torch the clone had re-synced

The operator's first `policy smoke` (`1.json`, 01:31) failed in
`ManagerBasedRlEnv.__init__` with `torch.AcceleratorError: CUDA error: no
kernel image is available for execution on the device`, after torch's own
warning that the installed build supports `sm_80 sm_90 sm_100 sm_120` and
Thor is `sm_110`. The clone's reflog showed it had been checked out at
`a30a9e4` and then moved back to the pinned `29e887e`; the next `uv run
train` re-synced torch to the `2.9.1+cu129` wheel from PyTorch's index
(`Uninstalled 1 package … Installed 1 package` at the top of stderr), which
carries no Thor kernels. The three extra wheels the fix branch adds
(`nvpl-blas`, `nvpl-lapack`, `nvidia-cudss-cu13`) were still in the venv.

Fix: back to the tutorial's Thor tab.

```text
$ git checkout a30a9e4 && UV_HTTP_TIMEOUT=600 uv sync      # in ~/git/microduck_rl
$ .venv/bin/python -c "import torch"
ImportError: libnvpl_lapack_lp64_gomp.so.0: cannot open shared object file
$ .venv/bin/python -c "import mjlab_microduck.tasks, torch; print(torch.__version__, torch.cuda.get_arch_list(), torch.cuda.is_available())"
2.9.1 ['sm_110', 'sm_121'] True
```

The bare `import torch` failure is expected on this branch: `a30a9e4` does
not use `LD_LIBRARY_PATH`; `mjlab_microduck/_torch_libs.py` pre-loads the two
undeclared libraries when `mjlab_microduck.tasks` is imported, which every
train/play path does before torch. A hand probe must import the package
first. The 2026-09-04 Thor record used `LD_LIBRARY_PATH` because that module
did not exist yet.

## Thor: the smoke on the wheel, second attempt

`free -g` immediately before: `Mem: 122 total, 86 used, 36 available` (the
tutorial's floor is 20 GB).

```text
$ WANDB_MODE=offline timeout 30m microduck policy smoke Mjlab-Velocity-Flat-MicroDuck --json
Warp 1.12.0 initialized:  CUDA Toolkit 12.9, Driver 13.0
     "cuda:0"   : "NVIDIA Thor" (123 GiB, sm_101, mempool enabled)
...
                         Iteration time: 2.05s
                           Time elapsed: 00:00:09
{"argv": ["uv", "run", "train", "Mjlab-Velocity-Flat-MicroDuck", "--env.scene.num-envs", "64", "--agent.max_iterations", "5"], "ok": true, "returncode": 0, ...}
exit 0   elapsed 0:26.02   max RSS 2.8 GB
```

**Pass.** 26 s wall-clock on a warm Warp kernel cache (the 2026-09-04 cold
run took 144 s). `docker ps` afterwards: every `prod-*` and `model-gear-*`
container still `Up`, the three vLLM engines `(healthy)`. `free -g` after:
`36 available`, unchanged.

## Memory footprint of the simulation

RSS of the two processes `env up --sim` starts, read from `/proc/<pid>/status`
while the duck was standing (`duck init --apply`, `duck enable --apply`):

| process | Thor (windowed, up since the operator's run) | Orin (`--headless`, 3 s to healthy) |
|---|---|---|
| `duck-body` (`python -m mjlab_microduck.sim.body_server`) | 933 MB | 590 MB |
| `robotd --sim` | 34 MB | 41 MB |
| box `used` delta while up | not measured (already running) | +0.9 GB (`free -m`: 54355 → 55268 MB) |

So the simulation and the daemon together cost about one gigabyte, an order
of magnitude below the training smoke's 2.8 GB peak RSS and far below
Thor's 20 GB floor for the smoke. On Orin the stack ran and stood the duck
up with 8 GB available beside the production containers; `env down` stopped
both processes (`body: terminated`; the "something is still listening on
port 7801" warning cleared on the next check, nothing left running).

## Orin: the fix branch resolves torch without `UV_INDEX`

The Orin clone is also at `a30a9e4`, and its `uv.lock` resolves `torch`
2.9.1 from `pypi.jetson-ai-lab.io/sbsa/cu130` (the branch keys the SBSA
index on `'tegra' in platform_release`, which Orin's `6.8.12-1021-tegra`
kernel matches); `torch-2.9.1.dist-info` is what is installed. The tutorial's
Orin `UV_INDEX=…` line is only needed on the pinned `develop` commit. Nothing
changes for training: that torch has `sm_110`/`sm_121` kernels, not Orin's
`sm_87`, exactly as the 2026-09-04 Orin record found.

## What this changed

- The 0.9.6 `env doctor` fix in this repo (`PACKAGED_PINS`, `[WARN]`): Thor
  on the wheel hit the two `[FAIL] … unknown` lines the Spark re-test filed as
  `d5`, and the tutorial reviewer asked for it to just work.
- The tutorial: Step 2 tells Jetson readers to check out `a30a9e4` up front
  (the flip back to the pin is what broke the first smoke); the Thor and Orin
  tabs carry these numbers; Step 4's Spark tab warns about the sync's size.
