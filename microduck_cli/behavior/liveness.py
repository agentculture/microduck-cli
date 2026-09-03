"""Is an engine driving this duck right now? — the heartbeat, and the refusal on it.

One duck has one control socket, and robotd applies intents last-writer-wins with
no arbitration between clients. Two engines against the same duck is therefore
not "degraded", it is two authors fighting over every channel. This module is how
a second engine finds out *before* it opens a socket.

**A heartbeat, not a flag file.** A flag cannot expire: a ``SIGKILL``ed engine
leaves it on disk forever and locks the operator out of their own duck until they
find and delete it. :class:`Heartbeat` republishes ``state.json`` under the duck's
state directory (the same directory
:mod:`microduck_cli.duck.addressing` resolves the sockets in) every N ticks, so
liveness is a question about a *stamp*, and a dead engine stops answering it
within :data:`DEFAULT_STALE_AFTER_S`.

**Two independent facts, both required.** :func:`refuse_if_engine_live` refuses
only when the heartbeat is FRESH *and* its pid is still alive. Either alone is
not proof: a fresh stamp with a dead pid is the last beat of an engine that died
milliseconds ago, and a live pid with a stale stamp is a wedged or unrelated
process. Requiring both is what keeps the guard from stranding an operator.

**Reading never raises.** A truncated write, a hand-edited file, an unreadable
directory: every one of those is treated as *absent* — no evidence of an engine —
and reported as a named ``senselog`` drop rather than propagated. The guard exists
to stop a KNOWN collision, never to fail closed on its own bookkeeping. A stale
file is likewise not cleaned up specially: the next writer overwrites it, and
because writes are atomic (temp file + ``os.replace``) a reader never sees half of
one.

Pure standard library; the only in-package imports are the CLI's error type and
:mod:`microduck_cli.behavior.senselog`.
"""

from __future__ import annotations

import json
import math
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from microduck_cli.behavior import senselog
from microduck_cli.cli._errors import EXIT_USER_ERROR, CliError

#: The heartbeat file's name inside the state directory.
STATE_FILENAME = "state.json"

#: How old a heartbeat may be and still count as live. At the default 25 Hz
#: republish rate (``hz / 2``) this is many hundreds of missed beats — long
#: enough that a briefly stalled engine is not declared dead, short enough that a
#: killed one stops blocking the operator almost immediately.
DEFAULT_STALE_AFTER_S = 2.0

#: How far into the future a stamp may sit and still count as live. A live writer
#: rounds its stamp and can land a hair ahead of the reader; a stamp far ahead is
#: instead a file that outlived a monotonic-clock reset (a reboot), which is
#: stale, not live.
SKEW_TOLERANCE_S = 1.0

STAGE = "liveness"


def state_path(state_dir: str | os.PathLike[str]) -> Path:
    """The heartbeat file for *state_dir*."""
    return Path(state_dir) / STATE_FILENAME


@dataclass(frozen=True)
class EngineState:
    """A parsed heartbeat. Every field may be ``None`` — the file is not a schema."""

    pid: int | None
    started_at: float | None
    last_beat: float | None
    last_beat_wall: float | None
    tick: int | None
    hz: float | None
    achieved_hz: float | None
    overruns: int | None
    raw: dict


def _as_number(value) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value) if math.isfinite(float(value)) else None


def _as_int(value) -> int | None:
    number = _as_number(value)
    return None if number is None else int(number)


def read_state(state_dir: str | os.PathLike[str], *, report: bool = True) -> EngineState | None:
    """Parse the heartbeat under *state_dir*; ``None`` when there is no usable one.

    Never raises. A missing file, an unreadable directory, malformed JSON or a
    JSON document that is not an object all mean the same thing to the caller —
    no evidence of an engine — and each is named on the sense log (as
    ``no-heartbeat`` or ``corrupt-heartbeat``) so "we ignored it" is visible
    instead of silent.
    """
    path = state_path(state_dir)
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        if report:
            senselog.drop(STAGE, str(path), "no-heartbeat", f"{type(exc).__name__}: {exc}")
        return None
    try:
        data = json.loads(text)
    except ValueError as exc:
        if report:
            senselog.drop(STAGE, str(path), "corrupt-heartbeat", f"unparseable JSON: {exc}")
        return None
    if not isinstance(data, dict):
        if report:
            senselog.drop(STAGE, str(path), "corrupt-heartbeat", "top level is not an object")
        return None
    return EngineState(
        pid=_as_int(data.get("pid")),
        started_at=_as_number(data.get("started_at")),
        last_beat=_as_number(data.get("last_beat")),
        last_beat_wall=_as_number(data.get("last_beat_wall")),
        tick=_as_int(data.get("tick")),
        hz=_as_number(data.get("hz")),
        achieved_hz=_as_number(data.get("achieved_hz")),
        overruns=_as_int(data.get("overruns")),
        raw=data,
    )


def pid_is_alive(pid: int) -> bool:
    """Whether *pid* exists (signal 0 probe). A pid we may not signal still counts."""
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


@dataclass
class Heartbeat:
    """Publishes ``state.json`` atomically so a second engine can see us.

    The path is INJECTED (a state dir resolved by
    :mod:`microduck_cli.duck.addressing`, or a tmp dir in a test) rather than
    discovered here: this module must stay usable by an engine that was pointed at
    an explicit socket and never consulted the environment at all.

    ``last_beat`` is monotonic, which is the clock the freshness check compares
    against, and ``last_beat_wall`` rides along purely so a human reading the file
    can tell when it was written. Both are recorded because neither answers the
    other's question.
    """

    path: Path
    pid: int = 0
    clock: Callable[[], float] = time.monotonic
    wall_clock: Callable[[], float] = time.time
    started_at: float | None = None
    last: dict | None = None

    def __post_init__(self) -> None:
        if not self.pid:
            self.pid = os.getpid()
        self.path = Path(self.path)
        if self.started_at is None:
            self.started_at = self.wall_clock()

    def beat(
        self,
        *,
        tick: int = 0,
        hz: float = 0.0,
        achieved_hz: float = 0.0,
        overruns: int = 0,
    ) -> dict:
        """Write one heartbeat; returns the document written."""
        document = {
            "pid": self.pid,
            "started_at": self.started_at,
            "last_beat": round(self.clock(), 3),
            "last_beat_wall": round(self.wall_clock(), 3),
            "tick": tick,
            "hz": hz,
            "achieved_hz": round(achieved_hz, 3),
            "overruns": overruns,
        }
        self._write_atomic(document)
        self.last = document
        return document

    def _write_atomic(self, document: dict) -> None:
        """Temp file + ``os.replace``, so a reader never observes a partial write."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temp = self.path.with_name(f"{self.path.name}.{self.pid}.tmp")
        temp.write_text(json.dumps(document, sort_keys=True), encoding="utf-8")
        os.replace(temp, self.path)

    def clear(self) -> None:
        """Remove our heartbeat on a clean exit (best effort; absence is fine)."""
        try:
            self.path.unlink()
        except OSError:
            pass


def _reading(now: Callable[[], float] | float | None) -> float:
    """Resolve the injected *now* — a callable, a literal reading, or ``None`` — to a float."""
    if callable(now):
        return now()
    if now is None:
        return time.monotonic()
    return float(now)


def engine_is_live(
    state_dir: str | os.PathLike[str],
    *,
    stale_after_s: float = DEFAULT_STALE_AFTER_S,
    now: Callable[[], float] | float | None = None,
    pid_alive: Callable[[int], bool] = pid_is_alive,
    report: bool = True,
) -> EngineState | None:
    """The live engine's state, or ``None`` when nothing proves one is running.

    Both *now* and *pid_alive* are injected so the whole decision is testable with
    no process to kill and no clock to wait on; *now* accepts either a reading or
    a callable returning one.
    """
    state = read_state(state_dir, report=report)
    if state is None:
        return None
    reading = _reading(now)
    if state.last_beat is None:
        if report:
            senselog.drop(STAGE, str(state_path(state_dir)), "corrupt-heartbeat", "no last_beat")
        return None
    age = reading - state.last_beat
    if age > stale_after_s or age < -SKEW_TOLERANCE_S:
        if report:
            senselog.drop(
                STAGE,
                str(state_path(state_dir)),
                "stale-heartbeat",
                f"last beat {age:.3f}s away (pid {state.pid}); the next engine overwrites it",
            )
        return None
    if state.pid is None or not pid_alive(state.pid):
        if report:
            senselog.drop(
                STAGE,
                str(state_path(state_dir)),
                "dead-engine-pid",
                f"fresh heartbeat but pid {state.pid} is gone",
            )
        return None
    return state


def refuse_if_engine_live(
    state_dir: str | os.PathLike[str],
    *,
    verb: str = "engine run",
    stale_after_s: float = DEFAULT_STALE_AFTER_S,
    now: Callable[[], float] | float | None = None,
    pid_alive: Callable[[int], bool] = pid_is_alive,
) -> None:
    """Refuse *verb* while an engine is live against *state_dir*; else return.

    Call this FIRST — before the socket is opened — so a refused second engine
    never contends for the duck it was told not to drive. Raises exit-1
    :class:`~microduck_cli.cli._errors.CliError` whose message contains
    ``engine live``; a stale or absent heartbeat returns quietly (it was reported
    on the sense log) and the next :class:`Heartbeat` write replaces the file.
    """
    state = engine_is_live(state_dir, stale_after_s=stale_after_s, now=now, pid_alive=pid_alive)
    if state is None:
        return
    raise CliError(
        code=EXIT_USER_ERROR,
        message=(
            f"'{verb}' refused: engine live — pid {state.pid} is driving this duck "
            f"(fresh heartbeat in {state_path(state_dir)}, tick {state.tick})"
        ),
        remediation=(
            "one engine owns the duck's socket at a time: stop the running engine "
            f"(kill {state.pid}, or Ctrl-C the terminal owning it) and retry, or point "
            "this one at a different duck with --duck/--socket"
        ),
    )
