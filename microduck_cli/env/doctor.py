"""Environment doctor for the MicroDuck sim/train lane.

Diagnoses whether this box is ready to run the simulation stack (t13) and
the training lane (t14): the upstream clones checked out at the pinned
commits recorded in ``docs/upstream-pins.md``, a cargo toolchain new enough
to build ``robotd``/``robotctl``/``tof``/``sounds``, the built daemons
themselves, the ``microduck_rl`` virtualenv with ``onnxruntime`` installed,
a state directory short enough for unix sockets, a free port for
``duck-body``, the host class (t8), and whether the optional Hugging
Face / Weights & Biases / duck-PIN credentials are configured — reported as
set/unset only, never their values.

Mirrors the rubric-shaped report the in-package ``microduck doctor``
(``microduck_cli/cli/_commands/doctor.py``) already emits:
``{healthy, checks: [{id, passed, severity, message, remediation}]}``. A
later task (t20's ``env doctor`` verb) renders this report and exits 2 when
``healthy`` is False — this module never exits or prints on its own.

Every input this module reasons about is carried on :class:`EnvProbe`, an
injected, side-effect-free snapshot; :func:`diagnose` never touches the
filesystem, network, or a subprocess. :func:`default_probe` is the only
function here that gathers a probe from the real system, and every lookup
it performs is individually wrapped so a missing tool, missing file, or any
other failure degrades to ``None``/``False`` rather than raising — this
must succeed even on a bare box with nothing installed.

Secrets never reach this module as values. ``EnvProbe.secrets`` is a
mapping of secret name -> whether it is *set*, and ``hf_auth_user`` is only
ever the account name ``hf auth whoami`` prints, never a token. There is no
field on :class:`EnvProbe` capable of carrying a secret value, by
construction — a check that wanted to print one would have nothing to
print.
"""

from __future__ import annotations

import os
import re
import socket
import subprocess  # nosec B404 - only used with fixed argv, never shell=True
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

from microduck_cli.duck.addressing import DEFAULT_STATE_DIR, SOCKET_PATH_BYTE_LIMIT
from microduck_cli.env.hosts import HostInfo
from microduck_cli.env.hosts import classify as classify_host
from microduck_cli.env.hosts import default_probe as default_host_probe

# --- upstream doc pointers ---------------------------------------------
# Stable, branch-pinned URLs (not commit-pinned) so a remediation string
# stays valid even as the pinned commit in docs/upstream-pins.md moves on
# a deliberate re-pin. See docs/upstream-pins.md for the exact commit this
# CLI is validated against.
_MICRODUCK_SIM_DOC_URL = (
    "https://github.com/pollen-robotics/microduck/blob/sim-remote-io/docs/design/simulation.md"
)
_MICRODUCK_DUCK_SIM_URL = (
    "https://github.com/pollen-robotics/microduck/blob/sim-remote-io/scripts/duck-sim"
)
_MICRODUCK_RL_README_URL = (
    "https://github.com/pollen-robotics/microduck_rl/blob/develop/README.md"  # noqa: E501
)
_MICRODUCK_RL_HF_README_URL = (
    "https://github.com/pollen-robotics/microduck_rl/blob/develop/scripts/hf/README.md"
)

_MIN_CARGO_VERSION = (1, 89)

# duck-body's default port, matching upstream's DUCK_SIM_PORT (env/stack.py's
# _DEFAULT_PORT) — overridden by DUCK_SIM_PORT itself, or by injecting
# `body_port_free` directly on an EnvProbe.
DEFAULT_BODY_PORT = 7801

_DAEMON_BINARIES = ("robotd", "robotctl", "tofd", "sounds")

_SECRET_NAMES = ("HF_TOKEN", "WANDB_API_KEY", "DUCK_PIN")


@dataclass(frozen=True)
class EnvProbe:
    """Injected inputs used to diagnose the environment.

    Every field is plain data, never a live call — :func:`default_probe` is
    the only place that touches the real system. Tests build ``EnvProbe``
    fixtures directly with no mocking required.
    """

    microduck_clone: str | None = None
    microduck_clone_commit: str | None = None
    microduck_pinned_commit: str | None = None

    rl_clone: str | None = None
    rl_clone_commit: str | None = None
    rl_pinned_commit: str | None = None

    cargo_version: str | None = None  # raw `cargo --version` output

    # binary name -> built under <microduck_clone>/target/debug/<name>
    built_binaries: Mapping[str, bool] = field(default_factory=dict)

    rl_venv_present: bool = False
    rl_onnxruntime_path: str | None = None  # libonnxruntime.so.* found under the RL venv

    state_dir: str | None = None
    body_port_free: bool | None = None

    host: HostInfo | None = None

    # secret name -> set/unset ONLY. Never the value: nothing on EnvProbe
    # can carry a secret's contents, so a check has nothing to leak.
    secrets: Mapping[str, bool] = field(default_factory=dict)

    hf_auth_user: str | None = None  # `hf auth whoami` — the account name only


def _safe_run(argv: list[str], timeout: float = 5) -> str | None:
    """Run a probe command, returning stripped stdout or None on any failure."""
    try:
        result = subprocess.run(  # nosec B603 - fixed argv, no shell, no user input
            argv,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except (OSError, ValueError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    output = result.stdout.strip()
    return output or None


def _git_head(clone: str) -> str | None:
    return _safe_run(["git", "-C", clone, "rev-parse", "HEAD"])


def _default_state_dir(env: Mapping[str, str]) -> str:
    """Mirror duck/addressing.py's DUCK_SIM_STATE resolution (read-only, no I/O)."""
    raw = env.get("DUCK_SIM_STATE") or DEFAULT_STATE_DIR
    if raw.startswith("~"):
        home = env.get("HOME")
        if home:
            return home + raw[1:]
        return os.path.expanduser(raw)
    return raw


def _parse_pins(pins_path: Path) -> dict[str, str]:
    """Parse the `| repo | ref | commit | ... |` table in docs/upstream-pins.md.

    Only rows whose repo cell is an exact single-backtick token (e.g.
    `` `pollen-robotics/microduck` ``) are matched, so the duck-ipc-proto
    sub-row (whose repo cell also names a file) is skipped — the first
    matching row per repo wins.
    """
    pins: dict[str, str] = {}
    try:
        text = pins_path.read_text(encoding="utf-8")
    except OSError:
        return pins

    for line in text.splitlines():
        stripped = line.strip()
        if not stripped.startswith("|"):
            continue
        cells = [c.strip() for c in stripped.strip("|").split("|")]
        if len(cells) < 3:
            continue
        repo_match = re.fullmatch(r"`([^`]+)`", cells[0])
        if not repo_match:
            continue
        repo = repo_match.group(1)
        if repo in pins:
            continue
        commit_match = re.search(r"`([0-9a-f]{7,40})`", cells[2])
        if not commit_match:
            continue
        pins[repo] = commit_match.group(1)
    return pins


def _find_onnxruntime(rl_clone: str) -> str | None:
    venv = Path(rl_clone) / ".venv"
    if not venv.is_dir():
        return None
    try:
        matches = sorted(venv.rglob("libonnxruntime.so*"))
    except OSError:
        return None
    return str(matches[0]) if matches else None


def _port_free(port: int) -> bool | None:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            probe.settimeout(0.2)
            result = probe.connect_ex(("127.0.0.1", port))
    except OSError:
        return None
    # connect_ex returns 0 on a successful connect, i.e. something is
    # listening — the port is NOT free.
    return result != 0


def _repo_root() -> Path:
    """This checkout's root, from this module's own location (env/doctor.py -> repo)."""
    return Path(__file__).resolve().parents[2]


def resolve_clone_paths(env: Mapping[str, str]) -> tuple[str | None, str | None]:
    """Resolve the microduck and microduck_rl clone directories.

    Precedence, one lookup shared by `default_probe` and `env up`/`env doctor` (the
    ``cli/_commands/env.py`` verb) so neither operator ever has to name a clone by
    hand:

    * microduck clone: ``MICRODUCK_CLONE``, else ``../microduck`` beside this repo.
    * microduck_rl clone: ``DUCK_SIM_RL`` (upstream's own env knob for the RL clone),
      else ``MICRODUCK_RL_CLONE``, else ``../microduck_rl`` beside this repo.

    A candidate that is not an existing directory is skipped in favour of the next
    one; the result is `None` when nothing resolves.
    """
    sibling = _repo_root().parent

    def _first_existing_dir(candidates: list[str | None]) -> str | None:
        for candidate in candidates:
            if candidate and Path(candidate).is_dir():
                return candidate
        return None

    microduck_clone = _first_existing_dir([env.get("MICRODUCK_CLONE"), str(sibling / "microduck")])
    rl_clone = _first_existing_dir(
        [env.get("DUCK_SIM_RL"), env.get("MICRODUCK_RL_CLONE"), str(sibling / "microduck_rl")]
    )
    return microduck_clone, rl_clone


def _probe_environ() -> dict[str, str]:
    try:
        return dict(os.environ)
    except OSError:  # pragma: no cover - os.environ access does not realistically fail
        return {}


def _probe_built_binaries(microduck_clone: str | None) -> dict[str, bool]:
    """Which daemon binaries are built in the clone's ``target/debug``."""
    built: dict[str, bool] = {}
    if not microduck_clone:
        return built
    target_debug = Path(microduck_clone) / "target" / "debug"
    for name in _DAEMON_BINARIES:
        try:
            built[name] = (target_debug / name).is_file()
        except OSError:
            built[name] = False
    return built


def _probe_rl_venv(rl_clone: str | None) -> tuple[bool, str | None]:
    """``(venv present, onnxruntime path)`` for the RL clone; onnxruntime only if the venv is."""
    if not rl_clone:
        return False, None
    try:
        present = (Path(rl_clone) / ".venv").is_dir()
    except OSError:
        present = False
    return present, _find_onnxruntime(rl_clone) if present else None


def _probe_body_port_free(env: Mapping[str, str]) -> bool:
    try:
        body_port = int(env.get("DUCK_SIM_PORT") or DEFAULT_BODY_PORT)
    except ValueError:
        body_port = DEFAULT_BODY_PORT
    return _port_free(body_port)


def _probe_hf_auth_user() -> str | None:
    user = _safe_run(["hf", "auth", "whoami"])
    if not user:
        return user
    return user.splitlines()[0].strip() or None


def default_probe() -> EnvProbe:
    """Gather an `EnvProbe` from the real system.

    Every lookup is wrapped so a missing tool, missing clone, or any other
    failure yields `None`/`False` rather than raising — this must succeed
    even on a bare box with nothing installed. The per-fact wrapping lives in the
    ``_probe_*`` helpers above; this function only composes them.
    """
    env = _probe_environ()

    pins_path = Path(__file__).resolve().parents[2] / "docs" / "upstream-pins.md"
    pins = _parse_pins(pins_path)

    microduck_clone, rl_clone = resolve_clone_paths(env)
    microduck_clone_commit = _git_head(microduck_clone) if microduck_clone else None
    rl_clone_commit = _git_head(rl_clone) if rl_clone else None

    cargo_version = _safe_run(["cargo", "--version"])
    built_binaries = _probe_built_binaries(microduck_clone)
    rl_venv_present, rl_onnxruntime_path = _probe_rl_venv(rl_clone)
    state_dir = _default_state_dir(env)
    body_port_free = _probe_body_port_free(env)
    host = classify_host(default_host_probe())
    secrets = {name: bool(env.get(name)) for name in _SECRET_NAMES}
    hf_auth_user = _probe_hf_auth_user()

    return EnvProbe(
        microduck_clone=microduck_clone,
        microduck_clone_commit=microduck_clone_commit,
        microduck_pinned_commit=pins.get("pollen-robotics/microduck"),
        rl_clone=rl_clone,
        rl_clone_commit=rl_clone_commit,
        rl_pinned_commit=pins.get("pollen-robotics/microduck_rl"),
        cargo_version=cargo_version,
        built_binaries=built_binaries,
        rl_venv_present=rl_venv_present,
        rl_onnxruntime_path=rl_onnxruntime_path,
        state_dir=state_dir,
        body_port_free=body_port_free,
        host=host,
        secrets=secrets,
        hf_auth_user=hf_auth_user,
    )


def _check(
    check_id: str,
    passed: bool,
    severity: str,
    message: str,
    remediation: str = "",
) -> dict[str, object]:
    return {
        "id": check_id,
        "passed": passed,
        "severity": severity,
        "message": message,
        "remediation": remediation if not passed else "",
    }


def _clone_present_check(check_id: str, label: str, clone: str | None, doc_url: str) -> dict:
    passed = bool(clone)
    message = f"{label} clone present at {clone}" if passed else f"{label} clone not found"
    remediation = (
        f"clone {label} locally (pinned commit in docs/upstream-pins.md) and set its path "
        f"— see {doc_url}"
    )
    return _check(check_id, passed, "error", message, remediation)


def _pinned_commit_check(
    check_id: str,
    label: str,
    clone: str | None,
    clone_commit: str | None,
    pinned_commit: str | None,
    doc_url: str,
) -> dict:
    if not clone:
        return _check(
            check_id,
            False,
            "error",
            f"{label} clone not present; cannot verify the pinned commit",
            f"clone {label} at the commit pinned in docs/upstream-pins.md — see {doc_url}",
        )
    if not pinned_commit:
        return _check(
            check_id,
            False,
            "warning",
            f"pinned commit for {label} is unknown (docs/upstream-pins.md unreadable or the "
            "row is missing)",
            "check that docs/upstream-pins.md is present and its table is well-formed",
        )
    if not clone_commit:
        return _check(
            check_id,
            False,
            "warning",
            f"{label} clone commit could not be determined (not a git checkout?)",
            f"verify {clone} is a git checkout of the pinned commit {pinned_commit}",
        )
    a, b = clone_commit.strip().lower(), pinned_commit.strip().lower()
    matches = a == b or a.startswith(b) or b.startswith(a)
    if matches:
        return _check(
            check_id,
            True,
            "info",
            f"{label} clone is at the pinned commit {pinned_commit}",
        )
    return _check(
        check_id,
        False,
        "warning",
        f"{label} clone is at {clone_commit}, pinned commit is {pinned_commit}",
        f"git -C {clone} checkout {pinned_commit} — see {doc_url}",
    )


_DIGITS = "0123456789"


def _trailing_digits(text: str) -> str:
    start = len(text)
    while start > 0 and text[start - 1] in _DIGITS:
        start -= 1
    return text[start:]


def _leading_digits(text: str) -> str:
    end = 0
    while end < len(text) and text[end] in _DIGITS:
        end += 1
    return text[:end]


def _first_major_minor(text: str) -> tuple[int, int] | None:
    """The first ``<digits>.<digits>`` in *text*, or ``None``.

    Scanned rather than matched: an unanchored ``(\\d+)\\.(\\d+)`` retries from
    every position and backtracks super-linearly on digit-heavy input, whereas
    splitting once on ``.`` and reading the digits either side of each split is a
    single linear pass.
    """
    parts = text.split(".")
    for left, right in zip(parts, parts[1:]):
        major, minor = _trailing_digits(left), _leading_digits(right)
        if major and minor:
            return int(major), int(minor)
    return None


def _cargo_version_check(cargo_version: str | None) -> dict:
    if not cargo_version:
        return _check(
            "cargo_version",
            False,
            "error",
            "cargo not found ('cargo --version' failed)",
            f"install a rust toolchain (rustup) with cargo >= "
            f"{_MIN_CARGO_VERSION[0]}.{_MIN_CARGO_VERSION[1]} — see {_MICRODUCK_DUCK_SIM_URL}",
        )
    found = _first_major_minor(cargo_version)
    if found is None:
        return _check(
            "cargo_version",
            False,
            "error",
            f"could not parse a cargo version from {cargo_version!r}",
            f"install a rust toolchain (rustup) with cargo >= "
            f"{_MIN_CARGO_VERSION[0]}.{_MIN_CARGO_VERSION[1]} — see {_MICRODUCK_DUCK_SIM_URL}",
        )
    passed = found >= _MIN_CARGO_VERSION
    required = f"{_MIN_CARGO_VERSION[0]}.{_MIN_CARGO_VERSION[1]}"
    message = f"cargo {found[0]}.{found[1]} ({'>=' if passed else '<'} {required} required)"
    remediation = f"rustup update — need cargo >= {required} — see {_MICRODUCK_DUCK_SIM_URL}"
    return _check("cargo_version", passed, "error", message, remediation)


def _daemons_built_check(built_binaries: Mapping[str, bool]) -> dict:
    missing = [name for name in _DAEMON_BINARIES if not built_binaries.get(name)]
    passed = not missing
    message = (
        "all daemons built (robotd, robotctl, tofd, sounds)"
        if passed
        else f"missing built binaries: {', '.join(missing)}"
    )
    remediation = (
        "cargo build -p robotd -p robotctl -p tof -p sounds in the microduck clone — see "
        f"{_MICRODUCK_DUCK_SIM_URL}"
    )
    return _check("daemons_built", passed, "error", message, remediation)


def _rl_venv_check(rl_venv_present: bool, onnxruntime_path: str | None) -> dict:
    passed = rl_venv_present and bool(onnxruntime_path)
    if passed:
        message = f"RL venv present with onnxruntime at {onnxruntime_path}"
    elif not rl_venv_present:
        message = "RL venv (.venv) not found under the microduck_rl clone"
    else:
        message = "RL venv present but libonnxruntime.so.* not found under it"
    remediation = (
        "uv sync the microduck_rl clone so onnxruntime installs its bundled "
        f"libonnxruntime — see {_MICRODUCK_RL_README_URL}"
    )
    return _check("rl_venv_with_onnxruntime", passed, "error", message, remediation)


def _state_dir_length_check(state_dir: str | None) -> dict:
    if not state_dir:
        return _check(
            "state_dir_length",
            False,
            "error",
            "state dir not configured",
            f"set DUCK_SIM_STATE to a writable directory — see {_MICRODUCK_DUCK_SIM_URL}",
        )
    sample = os.path.join(state_dir, "duck.sock")
    length = len(sample.encode("utf-8"))
    passed = length < SOCKET_PATH_BYTE_LIMIT
    message = (
        f"state dir {state_dir} yields socket paths of {length} bytes "
        f"(limit {SOCKET_PATH_BYTE_LIMIT})"
    )
    remediation = (
        f"set DUCK_SIM_STATE to a shorter directory (limit {SOCKET_PATH_BYTE_LIMIT} bytes) — "
        f"see {_MICRODUCK_DUCK_SIM_URL}"
    )
    return _check("state_dir_length", passed, "error", message, remediation)


def _body_port_free_check(body_port_free: bool | None) -> dict:
    if body_port_free is None:
        return _check(
            "body_port_free",
            False,
            "error",
            "could not determine whether the duck-body port is free",
            f"check nothing else is bound to the duck-body port — see {_MICRODUCK_RL_README_URL}",
        )
    message = "duck-body port is free" if body_port_free else "duck-body port is already in use"
    remediation = (
        f"stop whatever is bound to the duck-body port, or choose a different one — see "
        f"{_MICRODUCK_RL_README_URL}"
    )
    return _check("body_port_free", body_port_free, "error", message, remediation)


def _host_class_check(host: HostInfo | None) -> dict:
    if host is None:
        return _check(
            "host_class",
            False,
            "info",
            "host not classified",
            "",
        )
    message = (
        f"host class: {host.host_class} ({host.display_name}); "
        f"torch source applies: {host.torch_source_applies}"
    )
    remediation = "" if host.torch_source_applies else (host.remediation or "")
    return {
        "id": "host_class",
        "passed": True,
        "severity": "info",
        "message": message,
        "remediation": remediation,
    }


def _secret_check(check_id: str, label: str, secret_set: bool) -> dict:
    message = f"{label}: set" if secret_set else f"{label}: unset"
    return _check(check_id, True, "info", message, "")


def _hf_auth_check(hf_auth_user: str | None) -> dict:
    if hf_auth_user:
        message = f"hf auth: signed in as {hf_auth_user}"
    else:
        message = "hf auth: not signed in ('hf auth whoami' found no session)"
    return _check("hf_auth", True, "info", message, "")


def diagnose(probe: EnvProbe) -> dict[str, object]:
    """Diagnose `probe` into a rubric-shaped report.

    Pure: reads only `probe`'s fields, never touches the filesystem,
    network, or a subprocess. `healthy` is True iff every `severity ==
    "error"` check passed — `warning`/`info` checks are always surfaced but
    never block health.
    """
    checks: list[dict[str, object]] = [
        _clone_present_check(
            "microduck_clone_present", "microduck", probe.microduck_clone, _MICRODUCK_SIM_DOC_URL
        ),
        _pinned_commit_check(
            "microduck_pinned_commit",
            "microduck",
            probe.microduck_clone,
            probe.microduck_clone_commit,
            probe.microduck_pinned_commit,
            _MICRODUCK_SIM_DOC_URL,
        ),
        _cargo_version_check(probe.cargo_version),
        _daemons_built_check(probe.built_binaries),
        _clone_present_check(
            "rl_clone_present", "microduck_rl", probe.rl_clone, _MICRODUCK_RL_README_URL
        ),
        _pinned_commit_check(
            "rl_pinned_commit",
            "microduck_rl",
            probe.rl_clone,
            probe.rl_clone_commit,
            probe.rl_pinned_commit,
            _MICRODUCK_RL_README_URL,
        ),
        _rl_venv_check(probe.rl_venv_present, probe.rl_onnxruntime_path),
        _state_dir_length_check(probe.state_dir),
        _body_port_free_check(probe.body_port_free),
        _host_class_check(probe.host),
        _hf_auth_check(probe.hf_auth_user),
        _secret_check("wandb_key", "WANDB_API_KEY", probe.secrets.get("WANDB_API_KEY", False)),
        _secret_check("duck_pin", "DUCK_PIN", probe.secrets.get("DUCK_PIN", False)),
    ]

    healthy = all(c["passed"] for c in checks if c["severity"] == "error")
    return {"healthy": healthy, "checks": checks}


def render_text(report: Mapping[str, object]) -> str:
    """Render `report` the way `microduck-cli doctor`'s text output looks.

    ``"[ok] id: message"`` / ``"[FAIL] id: message"``, with a ``"  hint:
    remediation"`` line under any failing check that carries one.
    """
    status = "healthy" if report["healthy"] else "unhealthy"
    lines = [f"microduck-cli env doctor: {status}", ""]
    for check in report["checks"]:  # type: ignore[union-attr]
        mark = "ok" if check["passed"] else "FAIL"
        lines.append(f"[{mark}] {check['id']}: {check['message']}")
        if not check["passed"] and check["remediation"]:
            lines.append(f"  hint: {check['remediation']}")
    return "\n".join(lines)
