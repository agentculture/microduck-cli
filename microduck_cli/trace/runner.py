"""Record a session: run commands and tail daemon logs into one event stream.

:class:`Recorder` is the writer every mode shares. :func:`run_plan` drives a
:class:`~microduck_cli.trace.plan.Plan` through it step by step;
:func:`exec_one` traces a single arbitrary command into a run dir, so
exploratory or post-run work lands in the same ``events.jsonl`` as a tutorial
run. The renderer never learns which of the two produced a record.

Rules the recorder keeps (from the plan):

- A command runs **verbatim** through ``bash -c``; nothing is appended to it.
  The motion gate stays where it is: a plan line that moves the duck carries
  its own apply flag, and a line without one records the dry-run plan the CLI
  prints, which is exactly what happened.
- ``stdin`` is ``/dev/null`` — the recorder never opens a TTY, so no gated
  verb can prompt, and every gate decision is the plan author's.
- ``clock`` and ``sleep`` are injected; ``t`` is ``clock() - meta.t0_wall``.
"""

from __future__ import annotations

import os
import re
import shlex
import subprocess
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Mapping

from microduck_cli.trace.capture import DisplayEnv, ShotOutcome, capture_frame
from microduck_cli.trace.events import Event, RunDir, append_event
from microduck_cli.trace.plan import Plan, dump_plan

__all__ = [
    "CmdResult",
    "PlanResult",
    "Recorder",
    "default_state_dir",
    "exec_one",
    "lane_for_command",
    "run_plan",
]

_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")
#: ``2026-09-08T14:21:41.260362Z  INFO `` — the tracing-subscriber prefix robotd prints.
_ROBOTD_PREFIX_RE = re.compile(r"^\S+Z\s+(WARN|INFO|ERROR|DEBUG|TRACE)\s+")
_SENSE_EVENT_RE = re.compile(r"event=([a-z0-9-]+)")
_SENSE_STAGE_RE = re.compile(r"stage=([a-z0-9-]+)")
_SLUG_RE = re.compile(r"[^a-zA-Z0-9]+")
#: Same rule as ``plan.py``'s private ``_lane_for`` (kept private there, so
#: restated here rather than imported).
_OPERATOR_COMMANDS = frozenset({"free", "git", "pgrep", "cargo", "docker"})
_LOG_POLL_S = 0.05
_MAX_LABEL = 400
_DEFAULT_STATE_SUBDIR = os.path.join(".cache", "duck-sim")


def default_state_dir(environ: Mapping[str, str] = os.environ) -> str:
    """``DUCK_SIM_STATE`` when set, else ``~/.cache/duck-sim`` (from ``HOME``)."""
    explicit = environ.get("DUCK_SIM_STATE")
    if explicit:
        return explicit
    home = environ.get("HOME") or os.path.expanduser("~")
    return os.path.join(home, _DEFAULT_STATE_SUBDIR)


def lane_for_command(cmd: str) -> str:
    """``operator`` for box-side commands (free, git, pgrep, cargo, docker), else ``cli``."""
    first = cmd.split(" ", 1)[0] if cmd else ""
    return "operator" if first in _OPERATOR_COMMANDS else "cli"


@dataclass(frozen=True)
class CmdResult:
    """What one traced command left behind."""

    rc: int
    out_path: str
    err_path: str
    elapsed: float
    stdout_first: str


@dataclass
class PlanResult:
    """How a :func:`run_plan` call went."""

    steps_run: int = 0
    failures: int = 0
    retried: int = 0
    results: list[CmdResult] = field(default_factory=list)


class Recorder:
    """Append events for commands, notes, shots and tailed logs to one run dir."""

    def __init__(
        self,
        run_dir: RunDir,
        *,
        clock: Callable[[], float] = time.time,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.run_dir = run_dir
        self.clock = clock
        self.sleep = sleep
        run_dir.ensure()
        meta = run_dir.read_meta()
        if "t0_wall" not in meta:
            meta["t0_wall"] = clock()
            run_dir.write_meta(meta)
        self.t0 = float(meta["t0_wall"])
        self._lock = threading.Lock()
        self._tail_thread: threading.Thread | None = None
        self._tail_stop = threading.Event()

    # -- events ---------------------------------------------------------------

    def emit(self, lane: str, kind: str, label: str, *, step: str | None = None, **extra) -> Event:
        now = self.clock()
        event = Event(
            t=round(now - self.t0, 3),
            wall=now,
            lane=lane,
            kind=kind,
            label=label[:_MAX_LABEL] or "(empty)",
            step=step,
            extra=extra,
        )
        with self._lock:
            append_event(self.run_dir, event)
        return event

    def note(self, lane: str, label: str, step: str | None = None) -> Event:
        return self.emit(lane, "note", label, step=step)

    # -- commands -------------------------------------------------------------

    def _next_step_paths(self, label: str) -> tuple[Path, Path]:
        existing = list(self.run_dir.steps.glob("*.out"))
        slug = _SLUG_RE.sub("-", label)[:60].strip("-") or "cmd"
        base = self.run_dir.steps / f"{len(existing) + 1:02d}-{slug}"
        return base.with_suffix(".out"), base.with_suffix(".err")

    def run(
        self,
        step: str | None,
        lane: str,
        label: str,
        cmd: str,
        *,
        timeout_s: float | None = None,
        env: Mapping[str, str] | None = None,
    ) -> CmdResult:
        """Run *cmd* verbatim through ``bash -c``; stream stderr into events."""
        out_path, err_path = self._next_step_paths(label)
        child_env = dict(os.environ)
        if env:
            child_env.update(env)
        start = self.emit(lane, "cmd-start", label, step=step)
        err_lines: list[str] = []
        with out_path.open("wb") as out_handle, open(os.devnull, "rb") as devnull:
            proc = subprocess.Popen(  # nosec B602 - the plan's line is a shell line by design
                cmd,
                shell=True,
                executable="/bin/bash",
                stdin=devnull,
                stdout=out_handle,
                stderr=subprocess.PIPE,
                env=child_env,
            )
            pump = threading.Thread(
                target=self._pump_stderr, args=(proc, lane, step, err_lines), daemon=True
            )
            pump.start()
            timed_out = False
            try:
                rc = proc.wait(timeout=timeout_s)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()
                rc = -9
                timed_out = True
            pump.join(timeout=5)
        if timed_out:
            self.note(lane, f"timeout: killed after {timeout_s}s", step=step)
        err_path.write_text("\n".join(err_lines) + ("\n" if err_lines else ""), encoding="utf-8")
        out_text = out_path.read_text(encoding="utf-8", errors="replace")
        first = next((line for line in out_text.splitlines() if line.strip()), "")
        end = self.emit(
            lane,
            "cmd-end",
            label,
            step=step,
            rc=rc,
            elapsed=round(self.clock() - start.wall, 3),
            stdout_first=first[:300],
            stdout_lines=len(out_text.splitlines()),
            stderr_lines=len(err_lines),
            out=str(out_path.relative_to(self.run_dir.path)),
        )
        return CmdResult(
            rc=rc,
            out_path=str(out_path),
            err_path=str(err_path),
            elapsed=end.extra["elapsed"],
            stdout_first=first,
        )

    def _pump_stderr(
        self, proc: subprocess.Popen, lane: str, step: str | None, sink: list[str]
    ) -> None:
        assert proc.stderr is not None
        for raw in proc.stderr:
            line = _ANSI_RE.sub("", raw.decode("utf-8", "replace").rstrip("\n"))
            sink.append(line)
            if not line.strip():
                continue
            extra: dict[str, object] = {}
            event_lane = lane
            if line.startswith("[SENSE "):
                event_lane = "engine"
                ev = _SENSE_EVENT_RE.search(line)
                stage = _SENSE_STAGE_RE.search(line)
                extra["ev"] = ev.group(1) if ev else None
                extra["stage"] = stage.group(1) if stage else None
            self.emit(event_lane, "stderr", line, step=step, **extra)

    # -- shots ----------------------------------------------------------------

    def shot(
        self,
        step: str | None,
        label: str,
        name: str,
        *,
        shot: Callable[..., ShotOutcome] = capture_frame,
        env: DisplayEnv | None = None,
    ) -> ShotOutcome:
        """Take one viewer frame; a failed capture becomes a viewer note, never an error."""
        outcome = shot(self.run_dir, name, env=env)
        if outcome.ok and outcome.file:
            extra: dict[str, object] = {
                "file": outcome.file,
                "window": bool(outcome.window),
                "size": outcome.size,
            }
            if outcome.geometry is not None:
                extra["geometry"] = outcome.geometry
            self.emit("viewer", "shot", label, step=step, **extra)
        else:
            self.note("viewer", f"no frame ({outcome.reason or 'capture failed'}): {label}", step)
        return outcome

    # -- log tail -------------------------------------------------------------

    def start_log_tail(self, paths: Mapping[str, str]) -> None:
        """Tail each ``path -> lane`` from its current size, 50 ms polling."""
        self.stop_log_tail()
        self._tail_stop.clear()
        offsets = {p: (os.path.getsize(p) if os.path.exists(p) else 0) for p in paths}
        self._tail_thread = threading.Thread(
            target=self._tail_loop, args=(dict(paths), offsets), name="trace-logtail", daemon=True
        )
        self._tail_thread.start()

    def stop_log_tail(self) -> None:
        if self._tail_thread is None:
            return
        self._tail_stop.set()
        self._tail_thread.join(timeout=1)
        self._tail_thread = None

    def _tail_loop(self, paths: dict[str, str], offsets: dict[str, int]) -> None:
        while True:
            for path, lane in paths.items():
                self._drain(path, lane, offsets)
            if self._tail_stop.wait(_LOG_POLL_S):
                for path, lane in paths.items():
                    self._drain(path, lane, offsets)
                return

    def _drain(self, path: str, lane: str, offsets: dict[str, int]) -> None:
        if not os.path.exists(path):
            return
        size = os.path.getsize(path)
        if size < offsets[path]:
            offsets[path] = 0
        if size == offsets[path]:
            return
        with open(path, "rb") as handle:
            handle.seek(offsets[path])
            chunk = handle.read(size - offsets[path])
        offsets[path] = size
        for raw in chunk.decode("utf-8", "replace").splitlines():
            line = _ANSI_RE.sub("", raw).strip()
            if lane == "robotd":
                line = _ROBOTD_PREFIX_RE.sub("", line)
            if line:
                self.emit(lane, "log", line)


def _log_paths(state_dir: str) -> dict[str, str]:
    return {
        os.path.join(state_dir, "duck-a.log"): "robotd",
        os.path.join(state_dir, "body.log"): "body",
    }


def run_plan(
    plan: Plan,
    run_dir: RunDir,
    *,
    recorder: Recorder | None = None,
    shot: Callable[..., ShotOutcome] | None = capture_frame,
    display_env: DisplayEnv | None = None,
    state_dir: str | None = None,
    max_retry_default: int = 0,
) -> PlanResult:
    """Execute every step of *plan* in order, recording each attempt.

    A step with ``retry=N`` is re-run up to N more times while it exits
    non-zero; every attempt is its own ``cmd-start``/``cmd-end`` pair, so a
    first failure stays on the record beside its retry. ``shot=None`` disables
    frame capture (the step's ``shot`` name is then noted, not taken).
    """
    rec = recorder or Recorder(run_dir)
    rec.run_dir.ensure()
    (rec.run_dir.path / "plan.toml").write_text(dump_plan(plan), encoding="utf-8")
    result = PlanResult()
    rec.start_log_tail(_log_paths(state_dir or default_state_dir()))
    try:
        for step in plan.steps:
            if step.note:
                rec.note("operator", step.note, step.step)
            if step.sleep_before:
                rec.sleep(step.sleep_before)
            attempts = 1 + max(step.retry, max_retry_default)
            last: CmdResult | None = None
            for attempt in range(attempts):
                if attempt:
                    rec.note("operator", f"retry {attempt}/{attempts - 1}: {step.label}", step.step)
                    result.retried += 1
                last = rec.run(step.step, step.lane, step.label, step.cmd, timeout_s=step.timeout_s)
                if last.rc == 0:
                    break
            assert last is not None
            result.steps_run += 1
            result.results.append(last)
            if last.rc != 0:
                result.failures += 1
            if step.sleep_after:
                rec.sleep(step.sleep_after)
            if step.shot:
                if shot is None:
                    rec.note("viewer", f"frame skipped (capture disabled): {step.shot}", step.step)
                else:
                    rec.shot(step.step, step.label, step.shot, shot=shot, env=display_env)
    finally:
        rec.stop_log_tail()
    return result


def exec_one(
    argv: list[str],
    run_dir: RunDir,
    *,
    step: str = "exec",
    lane: str | None = None,
    recorder: Recorder | None = None,
    clock: Callable[[], float] = time.time,
    timeout_s: float | None = None,
) -> CmdResult:
    """Trace one arbitrary command into *run_dir* (created, with meta, if absent)."""
    rec = recorder or Recorder(run_dir, clock=clock)
    cmd = shlex.join(argv)
    return rec.run(step, lane or lane_for_command(cmd), cmd, cmd, timeout_s=timeout_s)
