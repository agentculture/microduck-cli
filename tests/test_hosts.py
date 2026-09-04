"""Tests for microduck_cli.env.hosts (host classification, t8)."""

from __future__ import annotations

import pytest

from microduck_cli.env.hosts import HostProbe, classify, default_probe

# --- fixtures for each of the five documented host classes -----------------

GB10_PROBE = HostProbe(
    machine="aarch64",
    gpu_name="NVIDIA GB10",
    tegra_release=None,
    cuda_version="12.9",
    env={},
)

JETSON_THOR_PROBE = HostProbe(
    machine="aarch64",
    gpu_name="NVIDIA Thor",
    tegra_release="# R38 (release), REVISION: 1.0, GCID: 12345, BOARD: t264\n",
    cuda_version="12.6",
    env={},
)

JETSON_AGX_ORIN_PROBE = HostProbe(
    machine="aarch64",
    gpu_name="Orin (nvgpu)",
    tegra_release="# R35 (release), REVISION: 4.1, GCID: 33958178, BOARD: t186ref\n",
    cuda_version="11.4",
    env={},
)

X86_64_PROBE = HostProbe(
    machine="x86_64",
    gpu_name="NVIDIA GeForce RTX 4090",
    tegra_release=None,
    cuda_version="12.4",
    env={},
)

HF_JOBS_PROBE = HostProbe(
    machine="x86_64",
    gpu_name="NVIDIA A100-SXM4-80GB",
    tegra_release=None,
    cuda_version="12.4",
    env={"HF_JOB_ID": "job-abc123"},
)


@pytest.mark.parametrize(
    ("probe", "expected_class", "expected_torch_source_applies"),
    [
        (GB10_PROBE, "gb10", True),
        (JETSON_THOR_PROBE, "jetson-thor", False),
        (JETSON_AGX_ORIN_PROBE, "jetson-agx-orin", False),
        (X86_64_PROBE, "x86_64", False),
        (HF_JOBS_PROBE, "hf-jobs", False),
    ],
)
def test_classify_known_host_classes(probe, expected_class, expected_torch_source_applies):
    info = classify(probe)
    assert info.host_class == expected_class
    assert info.torch_source_applies is expected_torch_source_applies
    assert info.display_name


def test_gb10_torch_source_applies_and_no_remediation():
    info = classify(GB10_PROBE)
    assert info.torch_source_applies is True
    assert info.remediation is None


def test_jetson_orin_carries_unverified_remediation():
    info = classify(JETSON_AGX_ORIN_PROBE)
    assert info.remediation is not None
    assert "unverified" in info.remediation


def test_jetson_thor_carries_the_verified_override_remediation():
    """Thor was verified on-box (docs/verification/2026-09-04-thor-sanity.md),
    but only with a local override of the RL clone — so the source verdict
    stays False and the remediation names the override, not 'unverified'."""
    info = classify(JETSON_THOR_PROBE)
    assert info.torch_source_applies is False
    assert info.remediation is not None
    assert "unverified" not in info.remediation
    assert "sbsa/cu130" in info.remediation
    assert "2026-09-04-thor-sanity.md" in info.remediation


def test_aarch64_non_jetson_torch_source_applies():
    probe = HostProbe(machine="aarch64", gpu_name="NVIDIA A100", tegra_release=None, env={})
    info = classify(probe)
    assert info.host_class == "aarch64-other"
    assert info.torch_source_applies is True
    assert info.remediation is None


def test_hf_jobs_space_id_marker_also_detected():
    probe = HostProbe(machine="x86_64", gpu_name=None, tegra_release=None, env={"SPACE_ID": "s1"})
    info = classify(probe)
    assert info.host_class == "hf-jobs"


def test_hf_jobs_takes_priority_over_arch_and_gpu():
    # Even an aarch64 GB10-looking box is classified hf-jobs if the env
    # markers are present -- HF Jobs/Spaces detection wins.
    probe = HostProbe(
        machine="aarch64",
        gpu_name="NVIDIA GB10",
        tegra_release=None,
        env={"HF_JOB_ID": "job-1"},
    )
    info = classify(probe)
    assert info.host_class == "hf-jobs"


# --- unknown host: never raises, always yields a remediation ---------------


def test_unknown_host_all_fields_none_never_raises():
    probe = HostProbe(machine="", gpu_name=None, tegra_release=None, cuda_version=None, env={})
    info = classify(probe)
    assert info.host_class == "unknown"
    assert info.torch_source_applies is False
    assert info.remediation is not None
    assert info.remediation != ""


def test_unknown_host_unrecognized_machine():
    probe = HostProbe(machine="riscv64", gpu_name=None, tegra_release=None, env={})
    info = classify(probe)
    assert info.host_class == "unknown"
    assert info.remediation is not None


def test_classify_never_raises_on_malformed_probe():
    # env deliberately not a dict subclass with .get misbehaving would be
    # caught by classify()'s own try/except; a plain empty mapping is enough
    # to exercise the "never raise" contract end to end.
    probe = HostProbe(machine="unknown-arch", gpu_name="", tegra_release="", env={})
    info = classify(probe)
    assert info.host_class == "unknown"
    assert isinstance(info.remediation, str)


# --- default_probe(): gathers from the real system, never raises -----------


def test_default_probe_never_raises_and_classifies():
    probe = default_probe()
    assert isinstance(probe, HostProbe)
    assert isinstance(probe.machine, str)
    # Whatever this CI box actually is, classification must complete.
    info = classify(probe)
    assert info.host_class in {
        "gb10",
        "jetson-thor",
        "jetson-agx-orin",
        "aarch64-other",
        "x86_64",
        "hf-jobs",
        "unknown",
    }
