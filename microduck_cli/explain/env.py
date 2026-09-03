"""Explain entries and verb list for the ``env`` noun.

Owned by the ``env`` noun task: adding an ``env`` verb means editing this
module (``VERBS`` + ``ENTRIES``), ``cli/_commands/env.py`` and
``tests/test_env.py`` — nothing else. :mod:`microduck_cli.explain.catalog`
merges ``ENTRIES`` into the global catalog and folds ``VERBS`` into the
canonical verb list that ``overview`` and ``learn`` render.

Each ``VERBS`` entry is ``"<command path> — <one line>"``; the em dash is the
separator the lockstep test splits on.
"""

from __future__ import annotations

#: The upstream page every `env` verb bringing up or tearing down the stack is
#: answerable to, at the ref pinned in ``docs/upstream-pins.md`` (``sim-remote-io``
#: @ ``0cd676d``).
_SIM_DOC_URL = (
    "https://github.com/pollen-robotics/microduck/blob/sim-remote-io/docs/design/simulation.md"
)

VERBS: list[str] = [
    "env overview — describe the environment noun (sim or real MicroDuck bring-up)",
    "env doctor — diagnose whether this box can run the sim/train lane, exit 2 when unhealthy",
    "env up — bring up the simulator (or fake) stack and wait for it to report healthy",
    "env down — stop the tracked simulator stack under the state directory",
    "env status — report which tracked processes are alive and whether their sockets answer",
    "env hosts — classify this host for the torch/CUDA training lane",
]

_ENV = f"""\
# microduck-cli env

Noun group for the MicroDuck *environment*: bringing up and doctoring the stack
the duck runs against — the simulator (`duck-body` + `robotd --sim`) or a fake
`robotd --fake` stand-in — plus the clones, build artifacts and state directory
that bring-up needs.

Every verb derives the microduck and `microduck_rl` clone paths, the ONNX
runtime path and the per-duck params file itself (`MICRODUCK_CLONE`,
`DUCK_SIM_RL`/`MICRODUCK_RL_CLONE`, or `../microduck` / `../microduck_rl`
beside this repo) — the operator never types a params file, an ORT path or a
socket path.

## Usage

    microduck-cli env
    microduck-cli env overview
    microduck-cli env doctor
    microduck-cli env up --fake
    microduck-cli env down
    microduck-cli env status
    microduck-cli env hosts

See also: `microduck-cli explain env doctor`, `microduck-cli explain env up`,
{_SIM_DOC_URL}
"""

_ENV_OVERVIEW = f"""\
# microduck-cli env overview

Read-only description of the `env` noun: what it holds (bring up / doctor the
sim or real environment) and which verbs exist. Descriptive, so it never
hard-fails — a stray positional argument is accepted and ignored.

## Usage

    microduck-cli env overview
    microduck-cli env overview --json

See also: {_SIM_DOC_URL}
"""

_ENV_DOCTOR = """\
# microduck-cli env doctor

Diagnoses whether this box can run the simulation/train lane: the upstream
clones at their pinned commits (`docs/upstream-pins.md`), a cargo toolchain
new enough to build `robotd`/`robotctl`/`tof`/`sounds`, the built daemons
themselves, the `microduck_rl` venv with `onnxruntime` installed, a state
directory short enough for unix sockets, a free `duck-body` port
(`DUCK_SIM_PORT`, default 7801), the host class, and whether the optional
Hugging Face / Weights & Biases / duck-PIN credentials are configured
(reported as set/unset only — never their values).

Delegates entirely to `microduck_cli.env.doctor`
(`default_probe()` + `diagnose()`); this verb only renders the report and
picks the exit code. Exits `2` when unhealthy, `0` otherwise.

## Usage

    microduck-cli env doctor
    microduck-cli env doctor --json

See also: https://github.com/pollen-robotics/microduck/blob/sim-remote-io/docs/design/simulation.md
"""  # noqa: E501

_ENV_UP = """\
# microduck-cli env up

Brings up the MicroDuck stack: builds the daemons (unless `--skip-build`),
locates `libonnxruntime` in the `microduck_rl` venv, starts `robotd --fake`
(one duck) or `duck-body` + one `robotd --sim` per duck, waits for each
control socket to appear, then polls `hello` + `robot.health` over each duck's
socket (through the one JSON-RPC client, `microduck_cli.ipc.client`) until it
reports healthy or a timeout expires — 60s for `--fake`, 120s for `--sim`.

Everything is derived from the resolved clone paths and the state directory:
no params file, ORT path or socket path is ever typed by the operator. When
`<microduck clone>/scripts/duck-sim` exists and `--upstream-launcher` is
passed, this shells out to it (with the `DUCK_SIM_*` environment mapped from
the flags below) instead of driving `microduck_cli.env.stack.SimStack`
directly.

## Flags

    --sim | --fake        which backend (default: --fake)
    --ducks N              how many ducks (--sim only; --fake starts exactly one)
    --port P                duck-body's base TCP port (default: 7801)
    --scene S               a built-in scene name, or a path (--sim only)
    --headless               run duck-body without a viewer (--sim only)
    --state DIR              override the state directory (default: DUCK_SIM_STATE
                              or ~/.cache/duck-sim)
    --skip-build              skip the cargo build step
    --upstream-launcher       shell out to scripts/duck-sim when the clone has it

## Usage

    microduck-cli env up --fake
    microduck-cli env up --sim --ducks 2 --scene apartment --headless
    microduck-cli env up --fake --json

On timeout, exits `2` naming the daemon's log file in the remediation — and
first stops whatever it had already started (by pid, through the same
identity-checked path as `env down`), so a failed bring-up never leaves stray
daemons, pidfiles or sockets behind. Cleanup is best effort and reported on
stderr; it never replaces the failure that caused it.

See also: https://github.com/pollen-robotics/microduck/blob/sim-remote-io/docs/design/simulation.md
"""  # noqa: E501

_ENV_DOWN = f"""\
# microduck-cli env down

Stops every process `microduck-cli env up` started under the state directory
(`microduck_cli.env.stack.SimStack.down`): reads each pidfile, deletes it
*before* signalling anything, and signals a pid only when
`/proc/<pid>/cmdline` still names the binary that pidfile was written for. A
pid whose cmdline no longer matches is reported as skipped-stale, never
signalled — pids are recycled, and signalling a recycled one has taken out an
unrelated login session upstream. Never kills by name (no `pkill`/`killall`).

After stopping, knocks on the `duck-body` TCP port with a plain
`connect_ex` and reports if something is still listening there.

## Usage

    microduck-cli env down
    microduck-cli env down --state /tmp/duck-sim
    microduck-cli env down --json

See also: {_SIM_DOC_URL}
"""

_ENV_STATUS = f"""\
# microduck-cli env status

Reports `microduck_cli.env.stack.SimStack.status()` — which tracked pids are
alive (by `/proc/<pid>/cmdline` marker match) or stale, and which
`*.sock`/`*-tof.sock` files exist under the state directory — plus, for each
control socket, whether it currently answers a `hello` handshake.

## Usage

    microduck-cli env status
    microduck-cli env status --state /tmp/duck-sim
    microduck-cli env status --json

See also: {_SIM_DOC_URL}
"""

_ENV_HOSTS = """\
# microduck-cli env hosts

Classifies this host (`microduck_cli.env.hosts.classify`) for the
`microduck_rl` training lane: architecture, GPU, whether it is a Jetson board,
an HF Jobs/Spaces runner, and whether `microduck_rl`'s `pytorch-cu129` torch
source applies here.

## Usage

    microduck-cli env hosts
    microduck-cli env hosts --json

See also: https://github.com/pollen-robotics/microduck_rl/blob/develop/README.md
"""

ENTRIES: dict[tuple[str, ...], str] = {
    ("env",): _ENV,
    ("env", "overview"): _ENV_OVERVIEW,
    ("env", "doctor"): _ENV_DOCTOR,
    ("env", "up"): _ENV_UP,
    ("env", "down"): _ENV_DOWN,
    ("env", "status"): _ENV_STATUS,
    ("env", "hosts"): _ENV_HOSTS,
}
