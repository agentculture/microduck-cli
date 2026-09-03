"""Bring the simulated duck stack up and down, one process at a time.

This reproduces — step by step, from its documented behaviour, not by copying
it — what ``scripts/duck-sim`` does in ``pollen-robotics/microduck`` at the
pinned commit (``docs/upstream-pins.md``):

1. **Always build.** ``cargo build --quiet -p robotd -p robotctl -p tof -p
   sounds`` in the clone, never "unless the binary exists": ``target/debug``
   is shared by every branch checked out in that tree, so a ``robotd`` that is
   present is not a ``robotd`` built from the branch you are on. cargo returns
   in a moment when nothing changed. ``skip_build=True`` opts out explicitly.
2. **Find the ONNX Runtime.** ``ort`` *dlopens* ``libonnxruntime`` and a
   laptop has no system one; the ``microduck_rl`` venv does. Upstream takes
   the first match of
   ``<rl>/.venv/lib/python*/site-packages/onnxruntime/capi/libonnxruntime.so.*``
   and exports it to the child as **``ORT_DYLIB_PATH``** — the environment
   variable the ``ort`` crate reads to decide which shared object to load.
   Without it every policy fails to load and the duck will not stand.
3. **Start the body, then the daemons.** In ``sim`` mode ``duck-body`` (the
   ``mjlab_microduck.sim.body_server`` console script) serves one TCP body per
   duck from ``port + i``, and each ``robotd --sim 127.0.0.1:<port+i>`` drives
   one. In ``fake`` mode there is no body at all: ``robotd --fake`` swaps in
   ``FakeIo`` and ``--sim`` is refused alongside it.
4. **One pidfile per process**, under the state directory.
5. **Wait for each control socket to appear**, with a timeout, rather than
   assuming the daemon came up.

Stopping is the part with the scar tissue, and it is reproduced deliberately.
Upstream's comment records a ``kill -TERM -<recycled pid>`` as root that took
out an unrelated login session, and a ``pkill -f robotd`` that matched the
shell about to start one. So :meth:`SimStack.down`:

* **never kills by name** — no ``pkill``, no pattern match against a process
  table, no process-group signal;
* deletes the pidfile **before** anything is signalled, so a stale file cannot
  be acted on twice;
* signals a pid only after ``/proc/<pid>/cmdline`` still contains the binary
  that pid was written for. A pid is not an identity: pids are recycled, and a
  pidfile from an earlier run may now name something else entirely. A mismatch
  is reported as skipped-stale, not signalled.

Every subprocess call goes through the injected ``runner``, and every ``/proc``
read through the injected ``proc_cmdline``, so the tests never start a process.
"""

from __future__ import annotations

import glob as _glob
import os
import signal
import subprocess  # nosec B404 - fixed argv only, never shell=True
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

from microduck_cli.cli._errors import EXIT_ENV_ERROR, EXIT_USER_ERROR, CliError
from microduck_cli.duck.addressing import duck_name, resolve
from microduck_cli.env.params import UPSTREAM_DUCK_SIM, UPSTREAM_SIMULATION_DOC, write_params

#: The environment variable the ``ort`` crate reads to locate libonnxruntime.
ORT_ENV_VAR = "ORT_DYLIB_PATH"

#: Where the ONNX Runtime shared object lives inside the RL repo's venv.
ORT_GLOB = ".venv/lib/python*/site-packages/onnxruntime/capi/libonnxruntime.so.*"

#: The crates ``duck-sim`` builds before starting anything.
CARGO_PACKAGES = ("robotd", "robotctl", "tof", "sounds")

#: Scene names without a ``/`` are one of the simulator's own scenes, and
#: resolve to this template inside the RL clone; anything with a ``/`` is a
#: path and is passed through verbatim.
SCENE_TEMPLATE = "src/mjlab_microduck/robot/microduck/scene_{name}.xml"

_DEFAULT_PORT = 7801
_DEFAULT_SOCKET_TIMEOUT_S = 30.0
_DEFAULT_BUILD_TIMEOUT_S = 900.0
_TERM_GRACE_S = 5.0
_POLL_INTERVAL_S = 0.2

#: Pidfile stem -> the string ``/proc/<pid>/cmdline`` must contain before that
#: pid is signalled. Anything not listed is a ``robotd`` (``<duck>.pid``),
#: which is how upstream's own pidfiles are named.
_MARKER_BY_STEM: dict[str, str] = {
    "body": "duck-body",
    "ether": "duck-ether",
}
_MARKER_BY_SUFFIX: tuple[tuple[str, str], ...] = (
    ("-tof", "tofd"),
    ("-media", "mediad"),
)
_DEFAULT_MARKER = "robotd"


class PopenLike(Protocol):
    """The slice of ``subprocess.Popen`` this module uses."""

    @property
    def pid(self) -> int:  # pragma: no cover - protocol declaration
        ...

    def poll(self) -> int | None:  # pragma: no cover - protocol declaration
        ...


#: ``runner(argv=…, cwd=…, env=…, stdout=…, stderr=…) -> PopenLike``.
Runner = Callable[..., PopenLike]
#: ``proc_cmdline(pid) -> "argv0 argv1 …"``, or ``None`` if the pid is gone.
ProcCmdline = Callable[[int], "str | None"]


def _listdir_sorted(path: str) -> list[str]:
    return sorted(os.listdir(path))


def expected_marker(stem: str) -> str:
    """The cmdline substring a pidfile named ``<stem>.pid`` must still match."""
    if stem in _MARKER_BY_STEM:
        return _MARKER_BY_STEM[stem]
    for suffix, marker in _MARKER_BY_SUFFIX:
        if stem.endswith(suffix):
            return marker
    return _DEFAULT_MARKER


def default_runner(
    *,
    argv: Sequence[str],
    cwd: str | None = None,
    env: Mapping[str, str] | None = None,
    stdout: str | None = None,
    stderr: str | None = None,
) -> PopenLike:
    """Production ``runner``: a detached child with its output on disk.

    ``start_new_session=True`` is ``setsid``: the child gets its own session
    and process group, so a Ctrl-C in this terminal does not reach it and it
    outlives the CLI invocation that started it.
    """
    out = open(stdout, "ab") if stdout else None  # noqa: SIM115 - owned by the child
    err = open(stderr, "ab") if stderr else (subprocess.STDOUT if out else None)
    return subprocess.Popen(  # nosec B603 - fixed argv, shell=False
        list(argv),
        cwd=cwd,
        env=dict(env) if env is not None else None,
        stdout=out,
        stderr=err,
        stdin=subprocess.DEVNULL,
        start_new_session=True,
    )


def default_proc_cmdline(pid: int) -> str | None:
    """Read ``/proc/<pid>/cmdline`` as a space-joined string, or ``None``."""
    try:
        raw = Path(f"/proc/{pid}/cmdline").read_bytes()
    except OSError:
        return None
    return raw.replace(b"\0", b" ").decode("utf-8", "replace").strip()


@dataclass(frozen=True)
class StartedProcess:
    """One process this stack started, and how to identify it later."""

    name: str
    pid: int
    marker: str
    argv: list[str]
    pidfile: str
    log: str
    socket: str | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            "name": self.name,
            "pid": self.pid,
            "marker": self.marker,
            "argv": list(self.argv),
            "pidfile": self.pidfile,
            "log": self.log,
            "socket": self.socket,
        }


@dataclass(frozen=True)
class UpReport:
    """What :meth:`SimStack.up` started."""

    mode: str
    ducks: int
    port: int
    state_dir: str
    ort_path: str
    built: bool
    processes: list[StartedProcess] = field(default_factory=list)
    params: list[dict[str, object]] = field(default_factory=list)
    commands: list[list[str]] = field(default_factory=list)

    def to_dict(self) -> dict[str, object]:
        return {
            "mode": self.mode,
            "ducks": self.ducks,
            "port": self.port,
            "state_dir": self.state_dir,
            "ort_path": self.ort_path,
            "built": self.built,
            "processes": [p.to_dict() for p in self.processes],
            "params": list(self.params),
            "commands": [list(c) for c in self.commands],
        }


@dataclass(frozen=True)
class StopResult:
    """One pidfile's fate in :meth:`SimStack.down`."""

    name: str
    pid: int | None
    marker: str
    #: ``"terminated"``, ``"killed"``, ``"stale"``, ``"gone"`` or ``"unreadable"``.
    outcome: str
    detail: str = ""

    def to_dict(self) -> dict[str, object]:
        return {
            "name": self.name,
            "pid": self.pid,
            "marker": self.marker,
            "outcome": self.outcome,
            "detail": self.detail,
        }


@dataclass
class SimStack:
    """Lifecycle for one simulated (or faked) duck stack under a state dir.

    Every side effect is injected: ``runner`` starts processes,
    ``proc_cmdline`` reads ``/proc``, and ``exists``/``listdir``/``glob``,
    ``kill``, ``sleep`` and ``monotonic`` cover the rest. The defaults are the
    real thing; the tests pass fakes.
    """

    clone: str
    rl: str
    state_dir: str
    runner: Runner = default_runner
    proc_cmdline: ProcCmdline = default_proc_cmdline
    exists: Callable[[str], bool] = os.path.exists
    listdir: Callable[[str], list[str]] = _listdir_sorted
    glob: Callable[[str], list[str]] = _glob.glob
    kill: Callable[[int, int], None] = os.kill
    sleep: Callable[[float], None] = time.sleep
    monotonic: Callable[[], float] = time.monotonic
    base_env: Mapping[str, str] = field(default_factory=lambda: dict(os.environ))

    # -- helpers ---------------------------------------------------------

    def _path(self, *parts: str) -> str:
        return os.path.join(self.state_dir, *parts)

    def _address(self, name: str):
        """Socket paths for ``name``, from ``microduck_cli.duck.addressing``.

        Going through ``resolve`` is what applies the ~108-byte ``sun_path``
        limit to the paths *before* a daemon is told to bind them — the third
        of the three things upstream says nobody can be expected to guess.
        """
        return resolve(
            name,
            socket=self._path(f"{name}.sock"),
            env={"DUCK_SIM_STATE": self.state_dir},
            listdir=lambda _path: [],
        )

    def find_ort(self) -> str:
        """The ``libonnxruntime`` the policies will be loaded through."""
        matches = sorted(self.glob(os.path.join(self.rl, ORT_GLOB)))
        if not matches:
            raise CliError(
                code=EXIT_ENV_ERROR,
                message=(
                    f"no onnxruntime shared object under {self.rl}/.venv — the ONNX "
                    "policies cannot be loaded without one"
                ),
                remediation=(
                    f"run `uv sync` in the microduck_rl clone at {self.rl}; `ort` dlopens "
                    f"libonnxruntime and a laptop has no system one, so {ORT_ENV_VAR} must "
                    f"point into that venv — see {UPSTREAM_DUCK_SIM}"
                ),
            )
        return matches[0]

    def _child_env(self, name: str, ort: str) -> dict[str, str]:
        env = dict(self.base_env)
        env.update(
            {
                # Its own identity, or every duck on this laptop shares a chorale
                # id and drops the others' beacons as its own reflection.
                "DUCK_IDENTITY": name,
                "DUCK_RUNTIME_DIR": self.state_dir,
                ORT_ENV_VAR: ort,
                "RUST_LOG": self.base_env.get("RUST_LOG", "info"),
            }
        )
        return env

    def _body_env(self) -> dict[str, str]:
        """The body is a Python process, not a daemon: no identity, no ORT."""
        env = dict(self.base_env)
        env["DUCK_RUNTIME_DIR"] = self.state_dir
        return env

    def _write_pidfile(self, stem: str, pid: int) -> str:
        path = self._path(f"{stem}.pid")
        Path(path).write_text(f"{pid}\n", encoding="utf-8")
        return path

    def _start(
        self,
        *,
        stem: str,
        argv: Sequence[str],
        cwd: str,
        env: Mapping[str, str],
        marker: str,
        socket: str | None = None,
    ) -> StartedProcess:
        log = self._path(f"{stem}.log")
        try:
            proc = self.runner(argv=list(argv), cwd=cwd, env=dict(env), stdout=log, stderr=log)
        except OSError as exc:
            raise CliError(
                code=EXIT_ENV_ERROR,
                message=f"could not start {stem}: {' '.join(argv)}: {exc}",
                remediation=(
                    f"check the binary exists and is executable; see {UPSTREAM_SIMULATION_DOC}"
                ),
            ) from exc
        pidfile = self._write_pidfile(stem, proc.pid)
        return StartedProcess(
            name=stem,
            pid=proc.pid,
            marker=marker,
            argv=list(argv),
            pidfile=pidfile,
            log=log,
            socket=socket,
        )

    def _wait_for_exit(self, proc: PopenLike, what: str, timeout: float) -> int:
        deadline = self.monotonic() + timeout
        while True:
            status = proc.poll()
            if status is not None:
                return status
            if self.monotonic() >= deadline:
                raise CliError(
                    code=EXIT_ENV_ERROR,
                    message=f"{what} did not finish within {timeout:g}s",
                    remediation=f"run it by hand in {self.clone} and read the output",
                )
            self.sleep(_POLL_INTERVAL_S)

    def build(self, *, timeout: float = _DEFAULT_BUILD_TIMEOUT_S) -> list[str]:
        """``cargo build --quiet -p robotd -p robotctl -p tof -p sounds``.

        Always, never "unless the binary exists" — see the module docstring.
        """
        argv = ["cargo", "build", "--quiet"]
        for package in CARGO_PACKAGES:
            argv += ["-p", package]
        log = self._path("cargo-build.log")
        try:
            proc = self.runner(
                argv=list(argv), cwd=self.clone, env=dict(self.base_env), stdout=log, stderr=log
            )
        except OSError as exc:
            raise CliError(
                code=EXIT_ENV_ERROR,
                message=f"could not run cargo in {self.clone}: {exc}",
                remediation=(
                    "install a Rust toolchain (cargo >= 1.89) and point --clone at the "
                    f"pollen-robotics/microduck checkout — see {UPSTREAM_SIMULATION_DOC}"
                ),
            ) from exc
        status = self._wait_for_exit(proc, " ".join(argv), timeout)
        if status != 0:
            raise CliError(
                code=EXIT_ENV_ERROR,
                message=f"`{' '.join(argv)}` failed in {self.clone} (exit {status})",
                remediation=f"read {log}, then run the same command by hand in {self.clone}",
            )
        return argv

    def _scene_arg(self, scene: str) -> str:
        if "/" in scene:
            return scene
        return os.path.join(self.rl, SCENE_TEMPLATE.format(name=scene))

    def _wait_for_socket(self, path: str, log: str, timeout: float) -> None:
        deadline = self.monotonic() + timeout
        while True:
            if self.exists(path):
                return
            if self.monotonic() >= deadline:
                raise CliError(
                    code=EXIT_ENV_ERROR,
                    message=f"socket {path} did not appear within {timeout:g}s",
                    remediation=(
                        f"read {log} for why the daemon exited, then see "
                        f"{UPSTREAM_SIMULATION_DOC}"
                    ),
                )
            self.sleep(_POLL_INTERVAL_S)

    # -- lifecycle -------------------------------------------------------

    def up(
        self,
        *,
        mode: str = "fake",
        ducks: int = 1,
        port: int = _DEFAULT_PORT,
        scene: str | None = None,
        headless: bool = False,
        skip_build: bool = False,
        timeout: float = _DEFAULT_SOCKET_TIMEOUT_S,
        build_timeout: float = _DEFAULT_BUILD_TIMEOUT_S,
    ) -> UpReport:
        """Build, locate the ONNX Runtime, start the stack, wait for sockets."""
        if mode not in ("fake", "sim"):
            raise CliError(
                code=EXIT_USER_ERROR,
                message=f"unknown mode {mode!r}; expected 'fake' or 'sim'",
                remediation="pass mode='fake' (no simulator) or mode='sim' (MuJoCo body)",
            )
        if ducks < 1:
            raise CliError(
                code=EXIT_USER_ERROR,
                message=f"ducks must be at least 1, got {ducks}",
                remediation="ask for one or more ducks",
            )
        if mode == "fake" and ducks > 1:
            # `--fake` has no body to address, so N fake ducks would all be the
            # same robot-made-of-nothing; upstream only ever fakes one.
            raise CliError(
                code=EXIT_USER_ERROR,
                message="mode='fake' starts a single duck; use mode='sim' for more than one",
                remediation="pass ducks=1 with mode='fake', or mode='sim' for a MuJoCo body",
            )
        if scene is not None and mode != "sim":
            raise CliError(
                code=EXIT_USER_ERROR,
                message="--scene only means something with a simulated body (mode='sim')",
                remediation="drop --scene, or use mode='sim'",
            )

        Path(self.state_dir).mkdir(parents=True, exist_ok=True)

        commands: list[list[str]] = []
        built = False
        if not skip_build:
            commands.append(self.build(timeout=build_timeout))
            built = True

        ort = self.find_ort()
        processes: list[StartedProcess] = []

        if mode == "sim":
            body_argv = ["uv", "run", "duck-body", "--port", str(port)]
            if ducks != 1:
                body_argv += ["--ducks", str(ducks)]
            if headless:
                body_argv.append("--headless")
            if scene:
                body_argv += ["--scene", self._scene_arg(scene)]
            commands.append(list(body_argv))
            processes.append(
                self._start(
                    stem="body",
                    argv=body_argv,
                    cwd=self.rl,
                    env=self._body_env(),
                    marker=expected_marker("body"),
                )
            )

        params_reports: list[dict[str, object]] = []
        robotd = os.path.join(self.clone, "target", "debug", "robotd")
        for index in range(ducks):
            name = duck_name(index)
            address = self._address(name)
            params_path, report = write_params(
                self.clone, duck=name, state_dir=self.state_dir, exists=self.exists
            )
            params_reports.append(report.to_dict())

            argv = [robotd]
            if mode == "sim":
                argv += ["--sim", f"127.0.0.1:{port + index}"]
            else:
                argv.append("--fake")
            argv += ["--socket", address.socket_path, "--params", params_path]
            commands.append(list(argv))
            processes.append(
                self._start(
                    stem=name,
                    argv=argv,
                    cwd=self.clone,
                    env=self._child_env(name, ort),
                    marker=expected_marker(name),
                    socket=address.socket_path,
                )
            )

        for process in processes:
            if process.socket:
                self._wait_for_socket(process.socket, process.log, timeout)

        return UpReport(
            mode=mode,
            ducks=ducks,
            port=port,
            state_dir=self.state_dir,
            ort_path=ort,
            built=built,
            processes=processes,
            params=params_reports,
            commands=commands,
        )

    # -- stopping --------------------------------------------------------

    def _pidfile_stems(self) -> list[str]:
        """Pidfile stems in the state dir, ducks first and the body last.

        Order matters: a ``robotd`` whose body vanished first logs a torrent of
        connection errors on its way out.
        """
        try:
            entries = self.listdir(self.state_dir)
        except OSError:
            return []
        stems = sorted(e[: -len(".pid")] for e in entries if e.endswith(".pid"))
        last = ("body", "ether")
        return [s for s in stems if s not in last] + [s for s in stems if s in last]

    def _read_pid(self, path: str) -> int | None:
        try:
            raw = Path(path).read_text(encoding="utf-8")
        except OSError:
            return None
        digits = "".join(ch for ch in raw if ch.isdigit())
        if not digits:
            return None
        pid = int(digits)
        # 0 is "this process group" and 1 is init; neither is ever ours, and
        # signalling either is exactly the accident this guard exists for.
        return pid if pid > 1 else None

    def _still_alive(self, pid: int, marker: str) -> bool:
        cmdline = self.proc_cmdline(pid)
        return cmdline is not None and marker in cmdline

    def _terminate(self, pid: int, marker: str, name: str) -> StopResult:
        self.kill(pid, signal.SIGTERM)
        deadline = self.monotonic() + _TERM_GRACE_S
        while self._still_alive(pid, marker):
            if self.monotonic() >= deadline:
                self.kill(pid, signal.SIGKILL)
                return StopResult(
                    name=name,
                    pid=pid,
                    marker=marker,
                    outcome="killed",
                    detail=f"still running {_TERM_GRACE_S:g}s after SIGTERM; sent SIGKILL",
                )
            self.sleep(_POLL_INTERVAL_S)
        return StopResult(name=name, pid=pid, marker=marker, outcome="terminated")

    def down(self) -> list[StopResult]:
        """Stop everything this stack's pidfiles name — by pid, never by name.

        For each pidfile: read the pid, **delete the pidfile first** so a stale
        file cannot be acted on twice, and signal only if
        ``/proc/<pid>/cmdline`` still contains the binary that pid was written
        for. A pid whose cmdline no longer matches is reported as ``stale`` and
        is never signalled — pids are recycled, and signalling a recycled one
        has taken out an unrelated login session upstream.
        """
        results: list[StopResult] = []
        for stem in self._pidfile_stems():
            pidfile = self._path(f"{stem}.pid")
            marker = expected_marker(stem)
            pid = self._read_pid(pidfile)

            # Removed before anything is signalled.
            try:
                os.unlink(pidfile)
            except OSError:
                pass

            if pid is None:
                results.append(
                    StopResult(
                        name=stem,
                        pid=None,
                        marker=marker,
                        outcome="unreadable",
                        detail=f"{pidfile} held no usable pid; removed",
                    )
                )
                continue

            cmdline = self.proc_cmdline(pid)
            if cmdline is None:
                results.append(
                    StopResult(
                        name=stem,
                        pid=pid,
                        marker=marker,
                        outcome="gone",
                        detail="no /proc entry; the process had already exited",
                    )
                )
                continue
            if marker not in cmdline:
                results.append(
                    StopResult(
                        name=stem,
                        pid=pid,
                        marker=marker,
                        outcome="stale",
                        detail=(
                            f"pid {pid} is no longer {stem} (cmdline does not contain "
                            f"{marker!r}); removed the pidfile without signalling it"
                        ),
                    )
                )
                continue

            results.append(self._terminate(pid, marker, stem))
        return results

    def status(self) -> dict[str, object]:
        """Which tracked pids are alive, and which control sockets exist."""
        processes: list[dict[str, object]] = []
        for stem in self._pidfile_stems():
            marker = expected_marker(stem)
            pid = self._read_pid(self._path(f"{stem}.pid"))
            cmdline = self.proc_cmdline(pid) if pid is not None else None
            alive = cmdline is not None and marker in cmdline
            processes.append(
                {
                    "name": stem,
                    "pid": pid,
                    "marker": marker,
                    "alive": alive,
                    "stale": pid is not None and cmdline is not None and not alive,
                }
            )
        try:
            entries = self.listdir(self.state_dir)
        except OSError:
            entries = []
        sockets = sorted(self._path(e) for e in entries if e.endswith(".sock"))
        return {
            "state_dir": self.state_dir,
            "processes": processes,
            "sockets": sockets,
        }
