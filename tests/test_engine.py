"""The behaviour engine (t12): one tick seam, determinism, and fault isolation.

Acceptance criteria exercised here:

1. with ``max_ticks=500`` and a fake clock, two runs produce identical seam call
   sequences;
2. a driver that raises is isolated by :class:`TickBus` — the fault is recorded,
   the other drivers still run, and the measured tick period is unchanged.
"""

from __future__ import annotations

import logging

import pytest

from microduck_cli.behavior import senselog
from microduck_cli.behavior.engine import (
    DEFAULT_HZ,
    Engine,
    TickBus,
    TickMetrics,
    compose_pose,
)
from microduck_cli.behavior.liveness import Heartbeat, read_state, state_path
from microduck_cli.behavior.model import Behavior, BehaviorSpec, Lifetime, StopClass
from microduck_cli.behavior.sense import EMPTY_SENSE, SenseProviders

# --- fixtures / helpers ---------------------------------------------------


class RecordingSink:
    """A :class:`~microduck_cli.behavior.engine.TargetSink` that keeps every write."""

    def __init__(self, log: list | None = None) -> None:
        self.writes: list[dict] = []
        self.log = log

    def write(self, pose: dict) -> None:
        self.writes.append(dict(pose))
        if self.log is not None:
            self.log.append(("write", dict(pose)))


class FakeClock:
    """A deterministic monotonic clock: every read advances by a fixed step."""

    def __init__(self, step: float = 0.001, start: float = 0.0) -> None:
        self.t = start
        self.step = step
        self.sleeps: list[float] = []

    def __call__(self) -> float:
        value = self.t
        self.t += self.step
        return value

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.t += seconds


def behavior(
    beh_id: str,
    channels,
    *,
    fn=None,
    stop_class: StopClass = StopClass.STOPPABLE,
    lifetime: Lifetime | None = None,
    wants_sense: bool = False,
) -> Behavior:
    spec = BehaviorSpec(
        name=beh_id.rstrip("0123456789-") or beh_id,
        channels=frozenset(channels),
        stop_class=stop_class,
        lifetime=lifetime or Lifetime(looping=True),
    )
    return Behavior(
        id=beh_id,
        spec=spec,
        fn=fn or (lambda t, params, sense: {c: round(t, 6) for c in channels}),
        wants_sense=wants_sense,
    )


@pytest.fixture(autouse=True)
def _quiet_senselog():
    """Keep engine sense logging out of the captured streams unless a test wants it."""
    root = logging.getLogger(senselog.ROOT_LOGGER_NAME)
    saved = list(root.handlers)
    root.handlers.clear()
    yield
    root.handlers.clear()
    root.handlers.extend(saved)


def run_engine(*, seam=None, max_ticks=10, behaviors=(), providers=None, clock=None, hz=DEFAULT_HZ):
    clock = clock or FakeClock()
    sink = RecordingSink()
    engine = Engine(
        sink,
        providers if providers is not None else SenseProviders(),
        clock=clock,
        sleep=clock.sleep,
        hz=hz,
    )
    for beh in behaviors:
        engine.admit(beh)
    ticks = engine.run(tick_seam=seam, max_ticks=max_ticks)
    return engine, sink, clock, ticks


# --- acceptance 1: determinism -------------------------------------------


def _seam_trace() -> tuple[list, callable]:
    calls: list[tuple] = []

    def seam(ctx):
        calls.append((ctx.tick, ctx.now, tuple(sorted(ctx.pose.items())), ctx.active_names()))

    return calls, seam


def test_500_ticks_under_a_fake_clock_are_bit_for_bit_reproducible():
    traces = []
    for _ in range(2):
        calls, seam = _seam_trace()
        run_engine(
            seam=seam,
            max_ticks=500,
            behaviors=[behavior("wiggle-1", ("twist", "head"))],
        )
        traces.append(calls)
    assert len(traces[0]) == 500
    assert traces[0] == traces[1]


def test_the_seam_runs_exactly_once_per_tick():
    calls, seam = _seam_trace()
    _, sink, _, ticks = run_engine(
        seam=seam, max_ticks=37, behaviors=[behavior("wiggle-1", ("twist",))]
    )
    assert ticks == 37
    assert len(calls) == 37
    assert len(sink.writes) == 37
    assert [c[0] for c in calls] == list(range(1, 38))


def test_the_pose_is_written_before_the_seam_sees_it():
    order: list = []
    sink = RecordingSink(log=order)
    clock = FakeClock()
    engine = Engine(sink, clock=clock, sleep=clock.sleep)
    engine.admit(behavior("wiggle-1", ("twist",)))
    engine.run(tick_seam=lambda ctx: order.append(("seam", dict(ctx.pose))), max_ticks=3)
    assert [kind for kind, _ in order] == ["write", "seam", "write", "seam", "write", "seam"]
    # The seam sees exactly what was sent, not a prediction of it.
    assert order[0][1] == order[1][1]


def test_no_wall_clock_is_read_in_the_loop():
    """Every timestamp in a run comes from the injected clock's arithmetic."""
    clock = FakeClock(step=0.002, start=1000.0)
    calls, seam = _seam_trace()
    run_engine(seam=seam, max_ticks=5, behaviors=[behavior("w-1", ("head",))], clock=clock)
    stamps = [c[1] for c in calls]
    # 1000.002, not 1000.0: admitting the behaviour consumed the first read.
    assert stamps[0] == pytest.approx(1000.002)
    # The deadline schedule absorbs each tick's work, so from the second tick on
    # every stamp is exactly one period later — pure arithmetic on the injected
    # clock, with no real time consulted anywhere.
    gaps = [b - a for a, b in zip(stamps[1:], stamps[2:])]
    assert gaps == pytest.approx([0.02] * len(gaps), abs=1e-9)


# --- acceptance 2: TickBus fault isolation --------------------------------


def test_a_raising_driver_is_isolated_and_the_others_still_run():
    seen: list[str] = []

    def good_first(ctx):
        seen.append(f"first:{ctx.tick}")

    def boom(ctx):
        raise RuntimeError("driver exploded")

    def good_last(ctx):
        seen.append(f"last:{ctx.tick}")

    bus = TickBus(drivers=[good_first, boom, good_last])
    _, _, _, ticks = run_engine(seam=bus, max_ticks=5)

    assert ticks == 5
    assert seen == [f"{who}:{n}" for n in range(1, 6) for who in ("first", "last")]
    assert len(bus.faults) == 5
    assert {f.driver for f in bus.faults} == {"boom"}
    assert {f.error for f in bus.faults} == {"RuntimeError"}
    assert bus.faults[0].message == "driver exploded"
    assert bus.fault_counts == {"boom": 5}
    assert [f.tick for f in bus.faults] == [1, 2, 3, 4, 5]


def test_a_raising_driver_does_not_change_the_measured_tick_period():
    def good(ctx):
        return None

    def boom(ctx):
        raise ValueError("nope")

    clean = TickBus(drivers=[good])
    faulty = TickBus(drivers=[good, boom])
    engine_clean, _, _, _ = run_engine(seam=clean, max_ticks=50, clock=FakeClock())
    engine_faulty, _, _, _ = run_engine(seam=faulty, max_ticks=50, clock=FakeClock())

    assert engine_clean.metrics.snapshot() == engine_faulty.metrics.snapshot()
    assert engine_clean.metrics.overruns == 0


def test_a_raising_consumer_never_breaks_the_emit_fan_out():
    got: list = []
    bus = TickBus(
        drivers=[lambda ctx: ctx.emit({"tick": ctx.tick})],
        consumers=[lambda e: (_ for _ in ()).throw(KeyError("bad consumer")), got.append],
    )
    run_engine(seam=bus, max_ticks=3)
    assert got == [{"tick": 1}, {"tick": 2}, {"tick": 3}]
    assert {f.error for f in bus.faults} == {"KeyError"}


def test_bus_faults_are_capped_so_a_broken_driver_cannot_grow_without_bound():
    bus = TickBus(drivers=[lambda ctx: (_ for _ in ()).throw(RuntimeError("x"))])
    bus.max_faults = 10
    run_engine(seam=bus, max_ticks=40)
    assert len(bus.faults) == 10
    assert bus.fault_counts == {"<lambda>": 40}
    assert [f.tick for f in bus.faults] == list(range(31, 41))


def test_a_driver_fault_is_named_on_the_sense_log(capsys):
    senselog.install_logging()
    bus = TickBus(drivers=[lambda ctx: (_ for _ in ()).throw(RuntimeError("boom"))])
    run_engine(seam=bus, max_ticks=1)
    err = capsys.readouterr().err
    assert "event=tick-driver-fault" in err
    assert "RuntimeError" in err


# --- composition, arbitration and lifetimes ------------------------------


def test_the_owner_of_each_channel_supplies_that_channel_s_value():
    twist = behavior("twist-1", ("twist",), fn=lambda t, p, s: {"twist": "slow"})
    both = behavior(
        "urgent-1",
        ("twist", "head"),
        stop_class=StopClass.UNSTOPPABLE,
        fn=lambda t, p, s: {"twist": "fast", "head": "left"},
    )
    _, sink, _, _ = run_engine(max_ticks=1, behaviors=[twist, both])
    assert sink.writes[0] == {"twist": "fast", "head": "left"}


def test_an_unclaimed_channel_is_absent_from_the_pose_not_invented():
    _, sink, _, _ = run_engine(max_ticks=1, behaviors=[behavior("m-1", ("mouth",))])
    assert set(sink.writes[0]) == {"mouth"}


def test_an_abstaining_owner_falls_through_to_the_next_claimant():
    quiet = behavior(
        "quiet-1",
        ("head",),
        stop_class=StopClass.UNSTOPPABLE,
        fn=lambda t, p, s: {"head": None},
    )
    loud = behavior("loud-1", ("head",), fn=lambda t, p, s: {"head": "centre"})
    # loud first: an UNSTOPPABLE incumbent on ``head`` would refuse it outright.
    _, sink, _, _ = run_engine(max_ticks=1, behaviors=[loud, quiet])
    assert sink.writes[0] == {"head": "centre"}


def test_compose_pose_is_usable_without_an_engine():
    beh = behavior("b-1", ("sound",))
    ownership = {"sound": beh, "twist": None}
    assert compose_pose(ownership, {"b-1": {"sound": "quack"}}) == {"sound": "quack"}


def test_a_finished_lifetime_is_expired_after_the_seam_ran():
    seen: list[tuple[int, tuple]] = []
    one_shot = behavior("shot-1", ("sound",), lifetime=Lifetime(duration=0.004))
    clock = FakeClock(step=0.002)
    run_engine(
        seam=lambda ctx: seen.append((ctx.tick, ctx.active_names())),
        max_ticks=4,
        behaviors=[one_shot],
        clock=clock,
    )
    # It is still active on the tick that reaches its duration (the seam sees it),
    # and gone on the next one.
    assert seen[0][1] == ("shot",)
    assert seen[-1][1] == ()


def test_admit_refuses_a_newcomer_blocked_by_an_unstoppable_incumbent():
    clock = FakeClock()
    engine = Engine(RecordingSink(), clock=clock, sleep=clock.sleep)
    engine.admit(behavior("stand-1", ("twist",), stop_class=StopClass.UNSTOPPABLE))
    result = engine.admit(behavior("walk-1", ("twist",)))
    assert result.admitted is False
    assert result.blocked == ("twist",)
    assert engine.active_names() == ("stand",)


def test_a_stopping_newcomer_evicts_the_stoppable_incumbent_it_shares_a_channel_with():
    clock = FakeClock()
    engine = Engine(RecordingSink(), clock=clock, sleep=clock.sleep)
    engine.admit(behavior("walk-1", ("twist",)))
    result = engine.admit(behavior("halt-1", ("twist",), stop_class=StopClass.STOPPING))
    assert result.admitted is True
    assert [b.id for b in result.evicted] == ["walk-1"]
    assert engine.active_names() == ("halt",)


def test_the_seam_can_admit_and_evict_through_the_context():
    def seam(ctx):
        if ctx.tick == 1:
            ctx.admit(behavior("late-1", ("sound",)))
        if ctx.tick == 3:
            assert ctx.evict("late") == ("late-1",)

    _, sink, _, _ = run_engine(seam=seam, max_ticks=4)
    assert sink.writes[0] == {}
    assert "sound" in sink.writes[1]
    assert sink.writes[3] == {}


def test_stop_ends_the_loop_after_the_tick_in_flight():
    clock = FakeClock()
    engine = Engine(RecordingSink(), clock=clock, sleep=clock.sleep)
    ticks = engine.run(tick_seam=lambda ctx: engine.stop() if ctx.tick == 6 else None)
    assert ticks == 6


# --- sense ---------------------------------------------------------------


def test_one_sense_snapshot_is_read_per_tick_and_shared_by_the_seam():
    reads: list[int] = []

    def fallen():
        reads.append(1)
        return True

    seen: list = []
    run_engine(
        seam=lambda ctx: seen.append(ctx.sense),
        max_ticks=5,
        providers=SenseProviders(fallen=fallen),
    )
    assert len(reads) == 5
    assert all(s.fallen is True for s in seen)


def test_a_behaviour_that_did_not_ask_for_sense_gets_the_empty_snapshot():
    seen: list = []
    pure = behavior("pure-1", ("head",), fn=lambda t, p, s: seen.append(s) or {"head": 1})
    run_engine(max_ticks=1, behaviors=[pure], providers=SenseProviders(fallen=lambda: True))
    assert seen == [EMPTY_SENSE]


def test_a_raising_provider_cannot_kill_the_tick():
    def boom():
        raise OSError("socket gone")

    _, _, _, ticks = run_engine(max_ticks=3, providers=SenseProviders(fallen=boom))
    assert ticks == 3


# --- cadence and metrics -------------------------------------------------


def test_the_loop_sleeps_the_remainder_of_each_deadline_not_a_full_period():
    clock = FakeClock(step=0.004)  # 4 ms of measured work per tick
    _, _, _, _ = run_engine(max_ticks=3, clock=clock, hz=50.0)
    # Never a full 20 ms period: each tick sleeps only the remainder of its
    # absolute deadline, so work is absorbed into the gap instead of added to it.
    assert clock.sleeps == pytest.approx([0.016, 0.012], abs=1e-9)


def test_an_overrunning_tick_sleeps_zero_and_is_counted():
    clock = FakeClock(step=0.030)  # 60 ms of work against a 20 ms budget
    engine, _, _, _ = run_engine(max_ticks=3, clock=clock, hz=50.0)
    assert clock.sleeps == []
    assert engine.metrics.overruns == 3
    assert engine.metrics.snapshot()["max_tick_ms"] == pytest.approx(30.0)


def test_an_overrun_never_skips_the_seam():
    calls, seam = _seam_trace()
    clock = FakeClock(step=0.030)
    run_engine(seam=seam, max_ticks=10, clock=clock, hz=50.0)
    assert len(calls) == 10


def test_metrics_snapshot_reports_the_budget_and_the_achieved_rate():
    clock = FakeClock(step=0.005)  # 5 ms of measured work per tick
    engine, _, _, _ = run_engine(max_ticks=4, clock=clock, hz=50.0)
    snap = engine.metrics.snapshot()
    assert snap["ticks"] == 4
    assert snap["overruns"] == 0
    assert snap["period_s"] == pytest.approx(0.02)
    assert snap["mean_tick_ms"] == pytest.approx(5.0)
    # What the measured WORK would allow — not the rate the loop chose to run at.
    assert snap["capacity_hz"] == pytest.approx(200.0)
    # The cadence the loop held is a separate number, measured start-to-start.
    assert snap["achieved_hz"] > 0.0


def test_achieved_hz_is_the_cadence_not_the_work_capacity():
    metrics = TickMetrics(period=0.02)
    for start in (0.00, 0.02, 0.04, 0.06):
        metrics.record(0.001, at=start)  # 1 ms of work, paced at 50 Hz
    assert metrics.achieved_hz == pytest.approx(50.0)
    assert metrics.capacity_hz == pytest.approx(1000.0)


def test_tick_metrics_logs_one_line_per_overrun_episode(capsys):
    senselog.install_logging()
    metrics = TickMetrics(period=0.02)
    for duration in (0.05, 0.05, 0.05, 0.001, 0.05):
        metrics.record(duration)
    err = capsys.readouterr().err
    assert err.count("event=overrun") == 2  # two episodes, not four ticks
    assert metrics.overruns == 4


def test_a_fresh_metrics_snapshot_is_all_zero():
    assert TickMetrics(period=0.02).snapshot()["achieved_hz"] == 0.0


# --- heartbeat wiring ----------------------------------------------------


def test_the_engine_publishes_a_heartbeat_every_beat_every_ticks(tmp_path):
    clock = FakeClock()
    sink = RecordingSink()
    heartbeat = Heartbeat(path=state_path(tmp_path), clock=clock, wall_clock=lambda: 1.0)
    engine = Engine(
        sink, clock=clock, sleep=clock.sleep, hz=50.0, heartbeat=heartbeat, beat_every=10
    )
    engine.run(max_ticks=25)
    state = read_state(tmp_path)
    assert state.tick == 21  # ticks 1, 11, 21
    assert state.hz == 50.0
    assert state.overruns == 0


def test_beat_every_defaults_to_half_the_compose_rate():
    engine = Engine(RecordingSink(), hz=50.0)
    assert engine.beat_every == 25


def test_compose_pose_passes_a_channel_owners_stop_and_mode_through():
    from microduck_cli.behavior.engine import compose_pose

    owner = behavior(
        "mode-switch-1",
        channels=("twist",),
        stop_class=StopClass.STOPPING,
        lifetime=Lifetime(1.0),
        fn=lambda t, params, sense: {"twist": (0.0, 0.0, 0.0), "mode": "roller", "stop": True},
    )
    ownership = {"twist": owner}
    contribs = {owner.id: {"twist": (0.0, 0.0, 0.0), "mode": "roller", "stop": True}}
    pose = compose_pose(ownership, contribs)
    assert pose["twist"] == (0.0, 0.0, 0.0)
    assert pose["mode"] == "roller"
    assert pose["stop"] is True
    # An unowned extra never leaks: a contribution from a non-owner is ignored.
    assert compose_pose({}, contribs) == {}
