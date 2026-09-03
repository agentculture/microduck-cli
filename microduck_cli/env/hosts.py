"""Host detection for the MicroDuck train lane.

Classifies the machine running `microduck` (env/train commands, t14/t15) so
the train lane can pick the torch/CUDA wiring appropriate to the host.

The torch-source verdict is not invented here — it mirrors
`microduck_rl`'s `pyproject.toml` `[tool.uv.sources]` torch entry, read at
the pinned commit (`docs/upstream-pins.md`,
`pollen-robotics/microduck_rl@29e887ecfbf5d37144759e5a9f8a176dfb83d547`):

    # On linux-aarch64 (DGX Spark / GB10) PyPI's torch wheel is CPU-ONLY...
    # Route torch to PyTorch's CUDA index there.
    torch = [
      { index = "pytorch-cu129",
        marker = "sys_platform == 'linux' and platform_machine == 'aarch64'" },
    ]

That marker matches on `sys_platform`/`platform_machine` alone, so it
literally also matches a Jetson board (also `linux`/`aarch64`). We narrow it
here on purpose: Jetson boards get their torch wheel from NVIDIA's JetPack
index (a build tied to the board's L4T/JetPack release), not from
`pytorch-cu129` — that index has never been exercised against a Jetson's
CUDA/cuDNN stack. So `torch_source_applies` is True only for the GB10 /
"aarch64-other" case the upstream marker was actually written for: a
linux-aarch64 host that is *not* Jetson. Jetson and unknown hosts get
`torch_source_applies=False` plus a remediation saying the path is
unverified there (tracked as the `t8`/`t15` `unknown_nonblocking` risk in
the plan).
"""

from __future__ import annotations

import os
import subprocess  # nosec B404 - only used with fixed argv, never shell=True
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

_TEGRA_RELEASE_PATH = Path("/etc/nv_tegra_release")

# HF Jobs and HF Spaces both stamp environment markers into the runtime when
# code executes on Hugging Face infrastructure. microduck_rl's --hf-jobs
# training path (t14) can be launched under either surface, so both markers
# are treated as "this host is hf-jobs" for classification purposes:
#   - HF_JOB_ID: set by `hf jobs run` for a scheduled/one-off Job.
#   - SPACE_ID: set inside a Space's runtime (interactive or Docker-based).
# Either one being present (non-empty) is sufficient; we don't need to tell
# Jobs and Spaces apart here since the torch-source verdict is the same for
# both (x86_64, PyPI-resolved CUDA wheel, untouched by the aarch64 marker).
_HF_JOBS_ENV_MARKERS = ("HF_JOB_ID", "SPACE_ID")

_UNVERIFIED_REMEDIATION = (
    "torch/warp training path is unverified on this host class: "
    "microduck_rl's [tool.uv.sources] torch index (pytorch-cu129) targets "
    "linux-aarch64 non-Jetson hosts only (pyproject.toml at the pinned "
    "microduck_rl commit, docs/upstream-pins.md) — confirm training "
    "actually works here before relying on it."
)


@dataclass(frozen=True)
class HostProbe:
    """Injected inputs used to classify the current host.

    Every field is plain data, never a live call — `default_probe()` is the
    only place that touches the real system. Tests build `HostProbe`
    fixtures directly with no mocking required.
    """

    machine: str  # `uname -m`, e.g. "aarch64", "x86_64"
    gpu_name: str | None = None  # `nvidia-smi --query-gpu=name --format=csv,noheader`
    tegra_release: str | None = None  # contents of /etc/nv_tegra_release, if present
    cuda_version: str | None = None  # best-effort, parsed from `nvidia-smi`
    env: Mapping[str, str] = field(default_factory=dict)  # HF Jobs/Spaces markers live here


@dataclass(frozen=True)
class HostInfo:
    """Classification result for a probed host."""

    host_class: str
    display_name: str
    torch_source_applies: bool
    remediation: str | None = None


def _safe_run(argv: list[str]) -> str | None:
    """Run a probe command, returning stripped stdout or None on any failure."""
    try:
        result = subprocess.run(  # nosec B603 - fixed argv, no shell, no user input
            argv,
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (OSError, ValueError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    output = result.stdout.strip()
    return output or None


def _detect_cuda_version() -> str | None:
    output = _safe_run(["nvidia-smi"])
    if not output:
        return None
    marker = "CUDA Version:"
    for line in output.splitlines():
        idx = line.find(marker)
        if idx == -1:
            continue
        rest = line[idx + len(marker) :].strip()
        if not rest:
            continue
        return rest.split()[0].rstrip("|").strip()
    return None


def default_probe() -> HostProbe:
    """Gather a `HostProbe` from the real system.

    Every lookup is wrapped so a missing tool, missing file, or any other
    failure yields `None` rather than raising — this must succeed even on a
    bare box with no GPU tooling installed.
    """
    try:
        machine = os.uname().machine
    except (AttributeError, OSError):
        machine = "unknown"

    gpu_name = _safe_run(["nvidia-smi", "--query-gpu=name", "--format=csv,noheader"])
    if gpu_name:
        gpu_name = gpu_name.splitlines()[0].strip()

    tegra_release: str | None = None
    try:
        if _TEGRA_RELEASE_PATH.is_file():
            text = _TEGRA_RELEASE_PATH.read_text(encoding="utf-8", errors="replace").strip()
            tegra_release = text or None
    except OSError:
        tegra_release = None

    cuda_version = _detect_cuda_version()

    try:
        env: Mapping[str, str] = dict(os.environ)
    except OSError:  # pragma: no cover - os.environ access does not realistically fail
        env = {}

    return HostProbe(
        machine=machine,
        gpu_name=gpu_name,
        tegra_release=tegra_release,
        cuda_version=cuda_version,
        env=env,
    )


def classify(probe: HostProbe) -> HostInfo:
    """Classify `probe` into a `HostInfo`. Never raises.

    Any unexpected shape in the injected probe (missing/odd fields) falls
    through to the `unknown` class with a remediation, rather than an
    exception reaching the caller.
    """
    try:
        return _classify(probe)
    except Exception:  # noqa: BLE001 - classification must never raise
        return HostInfo(
            host_class="unknown",
            display_name="Unknown host",
            torch_source_applies=False,
            remediation=_UNVERIFIED_REMEDIATION,
        )


def _classify(probe: HostProbe) -> HostInfo:
    machine = (probe.machine or "").strip().lower()
    gpu_name = probe.gpu_name or ""
    tegra_release = probe.tegra_release or ""
    env = probe.env or {}

    is_aarch64 = machine in ("aarch64", "arm64")
    is_x86_64 = machine in ("x86_64", "amd64")

    # hf-jobs markers take priority over arch/GPU probing: HF Jobs/Spaces
    # runners are conventionally x86_64 CUDA boxes, orthogonal to the
    # aarch64/Jetson distinctions below.
    if any(env.get(marker) for marker in _HF_JOBS_ENV_MARKERS):
        return HostInfo(
            host_class="hf-jobs",
            display_name="Hugging Face Jobs",
            torch_source_applies=False,
        )

    if "GB10" in gpu_name:
        return HostInfo(
            host_class="gb10",
            display_name="NVIDIA GB10 (DGX Spark)",
            torch_source_applies=True,
        )

    is_jetson = bool(tegra_release) or "tegra" in gpu_name.lower()
    if is_jetson:
        haystack = f"{gpu_name} {tegra_release}".lower()
        if "thor" in haystack:
            return HostInfo(
                host_class="jetson-thor",
                display_name="NVIDIA Jetson AGX Thor",
                torch_source_applies=False,
                remediation=_UNVERIFIED_REMEDIATION,
            )
        if "orin" in haystack:
            return HostInfo(
                host_class="jetson-agx-orin",
                display_name="NVIDIA Jetson AGX Orin",
                torch_source_applies=False,
                remediation=_UNVERIFIED_REMEDIATION,
            )
        # A Jetson signal fired (nv_tegra_release present, or GPU name says
        # "tegra") but the chip itself is not one we name explicitly.
        return HostInfo(
            host_class="aarch64-other",
            display_name="aarch64 host (unidentified Jetson)",
            torch_source_applies=False,
            remediation=_UNVERIFIED_REMEDIATION,
        )

    if is_aarch64:
        # linux-aarch64, non-Jetson: exactly the host class
        # microduck_rl's pytorch-cu129 marker targets.
        return HostInfo(
            host_class="aarch64-other",
            display_name="aarch64 host (non-Jetson)",
            torch_source_applies=True,
        )

    if is_x86_64:
        return HostInfo(
            host_class="x86_64",
            display_name="x86_64 host",
            torch_source_applies=False,
        )

    return HostInfo(
        host_class="unknown",
        display_name="Unknown host",
        torch_source_applies=False,
        remediation=_UNVERIFIED_REMEDIATION,
    )
