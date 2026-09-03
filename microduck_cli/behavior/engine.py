"""The 50 Hz duck engine — hold behaviours, arbitrate, compose, write, seam, sleep.

The engine is the only place in this package that has a loop. It holds a set of
:class:`~microduck_cli.behavior.model.Behavior` objects in admission order and,
every tick:

1. reads ONE :class:`~microduck_cli.behavior.sense.Sense` snapshot through the
   injected :class:`~microduck_cli.behavior.sense.SenseProviders`;
2. asks every active behaviour for its contribution exactly once;
3. resolves a single owner per channel with
   :func:`~microduck_cli.behavior.model.arbitrate` (abstention-aware);
4. composes the owners' values into one pose and writes it through the
   :class:`TargetSink` **exactly once**;
5. calls ``tick_seam(ctx)`` **exactly once, after the write**;
6. expires finished lifetimes;
7. sleeps to the tick's absolute deadline.

Built extraction-first (decision c20, see ``CLAUDE.md``). Everything the engine
touches the outside world with is an injected seam, and nothing below the
composition root imports a transport, an SDK or a CLI error type:

* :class:`TargetSink` — ``write(pose)``, the only way a pose leaves here;
* :class:`~microduck_cli.behavior.sense.SenseProviders` — the only way a reading
  gets in;
* ``clock`` / ``sleep`` — injected, so a whole run is deterministic in a test and
  **no wall-clock read happens anywhere in the loop**;
* ``tick_seam`` — the ONE per-tick integration point every rider shares (the
  rules driver, an export feed, a heartbeat publisher). Riders compose onto this
  seam; they never become a second process contending for the same socket.

Order matters and is part of the contract. The seam runs *after* the write, so a
rider observing ``ctx.pose`` sees what the duck was actually told this tick, not
a prediction. An overrunning tick is counted (:class:`TickMetrics`) and sleeps
zero — it never "catches up" by skipping the seam, because a skipped seam is a
rule that silently did not run.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Callable, Protocol, runtime_checkable

from microduck_cli.behavior import senselog
from microduck_cli.behavior.model import CHANNELS, AdmitResult, Behavior, Contribution
from microduck_cli.behavior.model import admit as admit_model
from microduck_cli.behavior.model import arbitrate
from microduck_cli.behavior.sense import (
    EMPTY_SENSE,
    NO_PROVIDERS,
    Sense,
    SenseProviders,
    read_sense,
)

#: The default compose rate. 50 Hz is robotd's own control cadence.
DEFAULT_HZ = 50.0

#: ``senselog`` stage token for everything the loop itself reports.
STAGE = "tick"


@runtime_checkable
class TargetSink(Protocol):
    """Where a composed pose goes. The engine's ONLY outbound seam.

    One method, one direction: ``write`` takes the tick's ``{channel: value}``
    dict and is responsible for turning it into whatever the transport speaks.
    Keeping this a Protocol (not a base class) is what lets the engine be
    extracted later without dragging a transport along: a test passes a list
    recorder, the CLI passes a JSON-RPC-backed sink, and neither is imported here.
    """

    def write(self, pose: dict[str, object]) -> None:
        """Send one fully composed tick's pose. Called at most once per tick."""


@dataclass
class ActiveBehavior:
    """A live behaviour plus the tick-clock reading it was admitted at."""

    behavior: Behavior
    start_t: float


@dataclass
class TickFault:
    """One seam driver's failure, recorded rather than raised.

    Named by *driver* and *error* (the exception CLASS name, not the instance) so
    a fault is greppable and comparable across ticks; ``message`` keeps the
    instance's text for a human.
    """

    tick: int
    driver: str
    error: str
    message: str


@dataclass
class TickContext:
    """The per-tick contract handed to ``run(tick_seam=…)``.

    One fresh context per tick, built AFTER the pose was written. A rider reads
    perception, inspects what was actually sent, admits or evicts behaviours, and
    publishes events — all without the engine importing the rider.

    * ``now`` / ``tick`` — the injected clock's reading for this tick, and the
      1-based tick counter. Both fully deterministic under an injected clock.
    * ``sense`` — this tick's snapshot, read once and shared, so two riders on the
      same tick can never disagree about what was sensed.
    * ``active`` — the live behaviours, oldest first (the same order arbitration
      used).
    * ``ownership`` — ``{channel: Behavior | None}`` resolved this tick.
    * ``pose`` — the exact dict already written to the sink this tick.
    * ``emit`` — ``emit(event: dict) -> None``, fanned out to whatever consumers
      the seam registered (a no-op when the seam exposes no ``.emit``).
    * ``admit`` — ``admit(behavior) -> AdmitResult``: offer a behaviour to the
      live set through :func:`~microduck_cli.behavior.model.admit`.
    * ``evict`` — ``evict(name_or_id) -> tuple[str, ...]``: stop every live
      behaviour matching that name or id; returns the ids removed.
    * ``active_names`` — ``active_names() -> tuple[str, ...]``.
    """

    now: float
    tick: int
    sense: Sense
    active: tuple[Behavior, ...]
    ownership: dict[str, Behavior | None]
    pose: dict[str, object]
    emit: Callable[[dict], None]
    admit: Callable[[Behavior], AdmitResult]
    evict: Callable[[str], tuple[str, ...]]
    active_names: Callable[[], tuple[str, ...]]


def _noop_emit(_event: dict) -> None:
    """``ctx.emit`` when the installed seam registers no event consumers."""


def _driver_name(driver: object) -> str:
    """A stable, human-readable name for a seam driver."""
    name = getattr(driver, "name", None)
    if isinstance(name, str) and name:
        return name
    return getattr(driver, "__name__", None) or type(driver).__name__


class TickBus:
    """Fan the engine's ONE seam out to an ordered list of drivers, fault-isolated.

    Each driver is called ``driver(ctx)`` in registration order. A driver that
    raises is caught, recorded on :attr:`faults` as a named :class:`TickFault`,
    logged as a ``tick-driver-fault`` drop — and **the remaining drivers still
    run**. The engine can therefore never die from a consumer's bug, and a
    consumer that is failing is loud rather than invisible.

    ``consumers`` receive whatever a driver publishes through ``ctx.emit`` and
    are isolated the same way.
    """

    #: How many faults to retain (a permanently broken driver must not grow the
    #: list without bound over a long run).
    max_faults = 100

    def __init__(self, drivers=None, consumers=None) -> None:
        self._drivers = list(drivers or [])
        self._consumers = list(consumers or [])
        self.faults: list[TickFault] = []
        self.fault_counts: dict[str, int] = {}
        self._tick = 0

    def add_driver(self, driver):
        """Register a per-tick driver; returns it for chaining."""
        self._drivers.append(driver)
        return driver

    def add_consumer(self, consumer):
        """Register an event consumer; returns it for chaining."""
        self._consumers.append(consumer)
        return consumer

    @property
    def drivers(self) -> tuple:
        return tuple(self._drivers)

    def _record(self, driver: object, exc: Exception, kind: str) -> None:
        name = _driver_name(driver)
        fault = TickFault(tick=self._tick, driver=name, error=type(exc).__name__, message=str(exc))
        self.faults.append(fault)
        del self.faults[: max(0, len(self.faults) - self.max_faults)]
        self.fault_counts[name] = self.fault_counts.get(name, 0) + 1
        senselog.drop(STAGE, name, "tick-driver-fault", f"{kind} raised {fault.error}: {exc}")

    def __call__(self, ctx: TickContext) -> None:
        self._tick = getattr(ctx, "tick", 0)
        for driver in self._drivers:
            try:
                driver(ctx)
            except Exception as exc:  # one rider never breaks the loop
                self._record(driver, exc, "driver")

    def emit(self, event: dict) -> None:
        """Publish *event* to every registered consumer, isolated per consumer."""
        for consumer in self._consumers:
            try:
                consumer(event)
            except Exception as exc:  # one consumer never breaks the fan-out
                self._record(consumer, exc, "consumer")


@dataclass
class TickMetrics:
    """What a tick actually cost, measured on the engine's own injected clock.

    Wraps the WHOLE tick — sense read, composition, sink write and seam — not one
    driver, because "did we hold 50 Hz" is a question about the total, and a
    driver that times itself cannot see its siblings. ``period`` is the budget
    (``1 / hz``); a tick whose measured duration exceeds it is an OVERRUN: counted
    here, logged once per episode, and never compensated for by dropping work.
    """

    period: float
    ticks: int = 0
    overruns: int = 0
    max_tick_s: float = 0.0
    total_tick_s: float = 0.0
    first_start: float | None = None
    last_start: float | None = None
    _in_overrun: bool = field(default=False, repr=False)

    def record(self, duration: float, at: float | None = None) -> bool:
        """Record one tick's measured duration (and, with ``at``, when it started).

        ``at`` is the tick's start on the engine clock; it is what makes
        :attr:`achieved_hz` a real cadence. Return whether the tick overran.
        """
        self.ticks += 1
        self.total_tick_s += duration
        if at is not None:
            if self.first_start is None:
                self.first_start = at
            self.last_start = at
        self.max_tick_s = max(self.max_tick_s, duration)
        over = self.period > 0.0 and duration > self.period
        if over:
            self.overruns += 1
            if not self._in_overrun:
                self._in_overrun = True
                senselog.drop(
                    STAGE,
                    "engine",
                    "overrun",
                    f"tick {self.ticks} took {duration * 1000:.2f} ms "
                    f"over a {self.period * 1000:.2f} ms budget",
                )
        else:
            self._in_overrun = False
        return over

    @property
    def capacity_hz(self) -> float:
        """Ticks per second the measured WORK alone would allow (0.0 before any tick).

        Not the rate the loop ran at — that is :attr:`achieved_hz`. A 6 kHz
        capacity on a 50 Hz loop just says a tick costs ~0.17 ms.
        """
        if self.ticks == 0 or self.total_tick_s <= 0.0:
            return 0.0
        return self.ticks / self.total_tick_s

    @property
    def achieved_hz(self) -> float:
        """The cadence the loop actually held: ticks over wall time on the engine clock.

        Measured from the first tick's start to the last tick's start, so a
        paced 50 Hz loop reports ~50 whatever its work costs. 0.0 until two
        ticks carry a start time; this is the number the heartbeat publishes
        (upstream's ``achieved_hz`` has the same meaning).
        """
        if (
            self.first_start is None
            or self.last_start is None
            or self.ticks < 2
            or self.last_start <= self.first_start
        ):
            return 0.0
        return (self.ticks - 1) / (self.last_start - self.first_start)

    def snapshot(self) -> dict[str, float | int]:
        """A plain-dict readout, safe to publish into ``state.json`` or ``--json``."""
        mean = self.total_tick_s / self.ticks if self.ticks else 0.0
        return {
            "ticks": self.ticks,
            "period_s": self.period,
            "overruns": self.overruns,
            "max_tick_ms": self.max_tick_s * 1000.0,
            "mean_tick_ms": mean * 1000.0,
            "achieved_hz": self.achieved_hz,
            "capacity_hz": self.capacity_hz,
        }


def compose_pose(
    ownership: dict[str, Behavior | None], contribs: dict[str, Contribution]
) -> dict[str, object]:
    """Take each owned channel's value from its owner's contribution for this tick.

    A channel nobody owns is ABSENT from the pose rather than filled with a
    made-up neutral: this CLI has no authority to invent a resting value for the
    duck's twist or head, and a sink that receives no key for a channel leaves the
    daemon's own last value (and its deadman) in charge. That is a deliberate
    difference from the donor, whose neutral head pose is a documented constant.
    """
    pose: dict[str, object] = {}
    for channel in CHANNELS:
        owner = ownership.get(channel)
        if owner is None:
            continue
        value = contribs.get(owner.id, {}).get(channel)
        if value is not None:
            pose[channel] = value
    return pose


class Engine:
    """The live behaviour set and the tick loop that drives it.

    Everything that reaches outside is injected: the *sink* pose values go to, the
    sense *providers* readings come from, and the *clock* / *sleep* pair the
    cadence is built on. Construct one per process; :meth:`run` blocks until
    ``max_ticks`` or :meth:`stop`.
    """

    def __init__(
        self,
        sink: TargetSink,
        providers: SenseProviders = NO_PROVIDERS,
        *,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
        hz: float = DEFAULT_HZ,
        heartbeat=None,
        beat_every: int | None = None,
    ) -> None:
        self.sink = sink
        self.providers = providers
        self.clock = clock
        self.sleep = sleep
        self.hz = hz
        self.period = 1.0 / hz if hz > 0 else 0.0
        self.metrics = TickMetrics(period=self.period)
        self.active: list[ActiveBehavior] = []
        #: Optional :class:`~microduck_cli.behavior.liveness.Heartbeat`; the engine
        #: never constructs one, so an engine with no state dir simply has none.
        self.heartbeat = heartbeat
        self.beat_every = beat_every if beat_every is not None else max(1, int(round(hz / 2.0)))
        self.ticks = 0
        self._stop = False

    # --- the live set -----------------------------------------------------

    def behaviors(self) -> list[Behavior]:
        """The live behaviours, oldest-admitted first (arbitration's order)."""
        return [ab.behavior for ab in self.active]

    def active_names(self) -> tuple[str, ...]:
        """The library names of the live behaviours, in admission order."""
        return tuple(ab.behavior.name for ab in self.active)

    def admit(self, behavior: Behavior, now: float | None = None) -> AdmitResult:
        """Offer *behavior* to the live set, applying whatever the model decided.

        Pure decision, impure application: :func:`model.admit` says whether the
        newcomer joins and which incumbents it evicts, and this method is the only
        thing that mutates the set. A refusal is returned, never raised — the
        caller (a rule firing, a CLI injection) turns it into its own named drop.
        """
        result = admit_model(behavior, self.behaviors())
        if not result.admitted:
            senselog.drop(
                STAGE,
                behavior.name,
                "admission-blocked",
                f"channels {', '.join(result.blocked)} held by a blocking behaviour",
            )
            return result
        evicted = {b.id for b in result.evicted}
        if evicted:
            self.active = [ab for ab in self.active if ab.behavior.id not in evicted]
        start = now if now is not None else self.clock()
        self.active.append(ActiveBehavior(behavior=behavior, start_t=start))
        return result

    def evict(self, name_or_id: str) -> tuple[str, ...]:
        """Stop every live behaviour whose id or library name matches; return the ids."""
        removed = tuple(
            ab.behavior.id
            for ab in self.active
            if ab.behavior.id == name_or_id or ab.behavior.name == name_or_id
        )
        if removed:
            self.active = [ab for ab in self.active if ab.behavior.id not in removed]
        return removed

    def stop(self) -> None:
        """Ask :meth:`run` to return after the tick in flight."""
        self._stop = True

    # --- one tick ---------------------------------------------------------

    def _contributions(self, now: float, sense: Sense) -> dict[str, Contribution]:
        contribs: dict[str, Contribution] = {}
        for ab in self.active:
            contribs[ab.behavior.id] = ab.behavior.contribute(now - ab.start_t, sense)
        return contribs

    def _expire(self, now: float) -> tuple[str, ...]:
        expired = tuple(
            ab.behavior.id for ab in self.active if ab.behavior.is_expired(now - ab.start_t)
        )
        if expired:
            self.active = [ab for ab in self.active if ab.behavior.id not in expired]
        return expired

    def compose_tick(self, now: float, sense: Sense = EMPTY_SENSE) -> dict[str, object]:
        """Arbitrate + compose for *now*, without writing or expiring anything.

        Split out from :meth:`run` so a dry-run planner (and every test) can ask
        "what would this tick send?" without a sink, a clock or a loop.
        """
        contribs = self._contributions(now, sense)
        ownership = arbitrate(self.behaviors(), contribs)
        return {
            "ownership": ownership,
            "contributions": contribs,
            "pose": compose_pose(ownership, contribs),
        }

    def _beat(self) -> None:
        if self.heartbeat is None or (self.ticks - 1) % self.beat_every:
            return
        self.heartbeat.beat(
            tick=self.ticks,
            hz=self.hz,
            achieved_hz=self.metrics.achieved_hz,
            overruns=self.metrics.overruns,
        )

    # --- the loop ---------------------------------------------------------

    def run(self, tick_seam=None, max_ticks: int | None = None) -> int:
        """Drive the duck until :meth:`stop`, *max_ticks*, or the sink raises.

        Returns the number of ticks run. ``tick_seam`` is invoked exactly once per
        tick, immediately after that tick's pose reached the sink (see
        :class:`TickContext`); compose several riders onto it with
        :class:`TickBus` rather than starting a second process.

        Cadence is deadline-based: each tick sleeps only the remainder of its
        absolute deadline, so the achieved rate equals ``hz`` instead of
        ``work + period``. A tick that overruns sleeps zero and is counted by
        :attr:`metrics`; once more than a full period behind, the deadline is
        reset to now rather than firing catch-up ticks back to back — a burst of
        composed poses is worse for a walking duck than a late one.
        """
        self._stop = False
        seam_emit = self._resolve_emit(tick_seam)
        deadline: float | None = None
        while not self._stop:
            started = self.clock()
            if deadline is None:
                deadline = started
            self.ticks += 1
            sense = read_sense(self.providers, started)
            tick = self.compose_tick(started, sense)
            self.sink.write(tick["pose"])
            if tick_seam is not None:
                tick_seam(self._context(started, sense, tick, seam_emit))
            self._expire(started)
            self._beat()
            work_end = self.clock()
            self.metrics.record(work_end - started, at=started)
            if max_ticks is not None and self.ticks >= max_ticks:
                break
            remaining = deadline + self.period - work_end
            deadline = work_end if remaining <= -self.period else deadline + self.period
            if remaining > 0.0:
                self.sleep(remaining)
        return self.ticks

    @staticmethod
    def _resolve_emit(tick_seam) -> Callable[[dict], None]:
        candidate = getattr(tick_seam, "emit", None) if tick_seam is not None else None
        return candidate if callable(candidate) else _noop_emit

    def _context(self, now: float, sense: Sense, tick: dict, emit) -> TickContext:
        return TickContext(
            now=now,
            tick=self.ticks,
            sense=sense,
            active=tuple(self.behaviors()),
            ownership=tick["ownership"],
            pose=tick["pose"],
            emit=emit,
            admit=lambda behavior, _t=now: self.admit(behavior, _t),
            evict=self.evict,
            active_names=self.active_names,
        )
