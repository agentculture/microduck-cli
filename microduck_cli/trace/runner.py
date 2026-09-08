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
import signal
import subprocess
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Mapping

from microduck_cli.trace.capture import DisplayEnv, ShotOutcome, capture_frame, pause_autolock
from microduck_cli.trace.events import Event, RunDir, append_event
from microduck_cli.trace.plan import Plan, Step, dump_plan

__all__ = [
    "CmdResult",
    "PlanResult",
    "Recorder",
    "TracedResult",
    "default_state_dir",
    "exec_one",
    "lane_for_command",
    "run_plan",
    "run_traced",
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
#: Grace between SIGTERM and SIGKILL for a timed-out command's process group.
_KILL_GRACE_S = 2.0
#: Hard bound on waiting for the stderr pump thread; it never blocks the run.
_PUMP_JOIN_S = 2.0
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
                # Its own process group, so a timeout can kill the whole tree:
                # ``proc`` is only the bash shell, and its descendants would
                # otherwise survive holding the stderr pipe open.
                start_new_session=True,
            )
            pump = threading.Thread(
                target=self._pump_stderr, args=(proc, lane, step, err_lines), daemon=True
            )
            pump.start()
            signal_used = ""
            try:
                rc = proc.wait(timeout=timeout_s)
            except subprocess.TimeoutExpired:
                signal_used = self._kill_group(proc)
                rc = -9
            self._join_pump(pump, proc)
        if signal_used:
            self.note(lane, f"timeout: killed after {timeout_s}s ({signal_used})", step=step)
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

    @staticmethod
    def _signal_group(proc: subprocess.Popen, sig: int) -> bool:
        """Signal *proc*'s whole process group; ``False`` when it is already gone."""
        try:
            os.killpg(os.getpgid(proc.pid), sig)
            return True
        except (ProcessLookupError, PermissionError, OSError):
            return False

    def _kill_group(self, proc: subprocess.Popen) -> str:
        """SIGTERM the group, then SIGKILL it if it outlives the grace period."""
        if not self._signal_group(proc, signal.SIGTERM):
            proc.kill()
        try:
            proc.wait(timeout=_KILL_GRACE_S)
            return "SIGTERM"
        except subprocess.TimeoutExpired:
            pass
        self._signal_group(proc, signal.SIGKILL)
        try:
            proc.wait(timeout=_KILL_GRACE_S)
        except subprocess.TimeoutExpired:
            pass
        return "SIGKILL"

    def _join_pump(self, pump: threading.Thread, proc: subprocess.Popen) -> None:
        """Join the stderr pump under a hard bound; a survivor holding the pipe is killed."""
        pump.join(timeout=_PUMP_JOIN_S)
        if not pump.is_alive():
            return
        self._signal_group(proc, signal.SIGKILL)
        try:
            if proc.stderr is not None:
                proc.stderr.close()
        except OSError:
            pass
        pump.join(timeout=_PUMP_JOIN_S)

    def _pump_stderr(
        self, proc: subprocess.Popen, lane: str, step: str | None, sink: list[str]
    ) -> None:
        assert proc.stderr is not None
        try:
            self._pump_lines(proc, lane, step, sink)
        except (ValueError, OSError):  # the pipe was closed under us by _join_pump
            return

    def _pump_lines(
        self, proc: subprocess.Popen, lane: str, step: str | None, sink: list[str]
    ) -> None:
        for raw in proc.stderr:  # type: ignore[union-attr]
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
        """Tail each ``path -> lane`` from its current size, 50 ms polling.

        A poll can land in the middle of a line the writer has not finished, so
        each file keeps a byte buffer: only ``\\n``-terminated lines are emitted
        and the unterminated tail waits for the next chunk. The tail is flushed
        as one last line by :meth:`stop_log_tail`.
        """
        self.stop_log_tail()
        self._tail_stop.clear()
        offsets = {p: (os.path.getsize(p) if os.path.exists(p) else 0) for p in paths}
        buffers = {p: b"" for p in paths}
        self._tail_thread = threading.Thread(
            target=self._tail_loop,
            args=(dict(paths), offsets, buffers),
            name="trace-logtail",
            daemon=True,
        )
        self._tail_thread.start()

    def stop_log_tail(self) -> None:
        if self._tail_thread is None:
            return
        self._tail_stop.set()
        self._tail_thread.join(timeout=1)
        self._tail_thread = None

    def _tail_loop(
        self, paths: dict[str, str], offsets: dict[str, int], buffers: dict[str, bytes]
    ) -> None:
        while True:
            for path, lane in paths.items():
                self._drain(path, lane, offsets, buffers)
            if self._tail_stop.wait(_LOG_POLL_S):
                for path, lane in paths.items():
                    self._drain(path, lane, offsets, buffers)
                    self._flush_tail(path, lane, buffers)
                return

    def _drain(
        self, path: str, lane: str, offsets: dict[str, int], buffers: dict[str, bytes]
    ) -> None:
        if not os.path.exists(path):
            return
        size = os.path.getsize(path)
        if size < offsets[path]:  # truncated / rotated: start over, drop the stale tail
            offsets[path] = 0
            buffers[path] = b""
        if size == offsets[path]:
            return
        with open(path, "rb") as handle:
            handle.seek(offsets[path])
            chunk = handle.read(size - offsets[path])
        offsets[path] += len(chunk)
        pieces = (buffers[path] + chunk).split(b"\n")
        buffers[path] = pieces.pop()  # the unterminated tail, if any
        for raw in pieces:
            self._emit_log_line(raw, lane)

    def _flush_tail(self, path: str, lane: str, buffers: dict[str, bytes]) -> None:
        """Emit whatever never got its newline, once, at the end of the tail."""
        tail = buffers.get(path, b"")
        if tail:
            buffers[path] = b""
            self._emit_log_line(tail, lane)

    def _emit_log_line(self, raw: bytes, lane: str) -> None:
        line = _ANSI_RE.sub("", raw.decode("utf-8", "replace")).strip()
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
    _start_tail(rec, state_dir)
    try:
        for step in plan.steps:
            _before_step(rec, step)
            last = _run_step_attempts(rec, step, result, max_retry_default)
            result.steps_run += 1
            result.results.append(last)
            if last.rc != 0:
                result.failures += 1
            _after_step(rec, step, shot, display_env)
    finally:
        rec.stop_log_tail()
    return result


@dataclass(frozen=True)
class TracedResult:
    """One :func:`run_traced` call: the plan's result plus what the run's policy did."""

    plan_result: PlanResult
    autolock_paused: bool
    headless: bool


def run_traced(
    plan: Plan,
    run_dir: RunDir,
    *,
    pause_autolock: bool,
    display_env: DisplayEnv | None,
    state_dir: str | None,
    shot: Callable[..., ShotOutcome] | None = capture_frame,
    pause: Callable[..., object] = pause_autolock,
) -> TracedResult:
    """Run *plan* under the session policy a traced run needs, and report both.

    Two decisions live here rather than in the caller, because getting either
    of them wrong is silent:

    * **Headless is derived, not passed twice.** ``display_env is None`` means
      no display was found (or ``--headless`` was asked for), so frame capture
      is switched off here — a *shot* callable can never be handed to
      :func:`run_plan` without a display to take it on.
    * **The autolock pause carries the display's environment.** ``gsettings``
      talks to the session bus, so pausing without ``display_env`` would
      silently target nothing while the run still reported "paused". *pause*
      is always called as ``pause(env=display_env)``.

    ``pause_autolock`` is the caller's already-granted consent (the flag plus,
    on a TTY, the operator's answer) — this function asks nobody anything.
    ``autolock_paused`` reports whether the pause context was entered.
    """
    headless = display_env is None
    kwargs = {
        "shot": None if headless else shot,
        "display_env": display_env,
        "state_dir": state_dir,
    }
    if not pause_autolock:
        result = run_plan(plan, run_dir, **kwargs)
        return TracedResult(plan_result=result, autolock_paused=False, headless=headless)
    with pause(env=display_env):
        result = run_plan(plan, run_dir, **kwargs)
    return TracedResult(plan_result=result, autolock_paused=True, headless=headless)


def _start_tail(rec: Recorder, state_dir: str | None) -> None:
    rec.start_log_tail(_log_paths(state_dir or default_state_dir()))


def _before_step(rec: Recorder, step: Step) -> None:
    """The step's note and its pre-sleep, in that order."""
    if step.note:
        rec.note("operator", step.note, step.step)
    if step.sleep_before:
        rec.sleep(step.sleep_before)


def _run_step_attempts(
    rec: Recorder, step: Step, result: PlanResult, max_retry_default: int
) -> CmdResult:
    """Run one step until it succeeds or its attempts run out; record every attempt."""
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
    return last


def _after_step(
    rec: Recorder,
    step: Step,
    shot: Callable[..., ShotOutcome] | None,
    display_env: DisplayEnv | None,
) -> None:
    """The step's post-sleep and its frame (or the note that says why there is none)."""
    if step.sleep_after:
        rec.sleep(step.sleep_after)
    if not step.shot:
        return
    if shot is None:
        rec.note("viewer", f"frame skipped (capture disabled): {step.shot}", step.step)
    else:
        rec.shot(step.step, step.label, step.shot, shot=shot, env=display_env)


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
