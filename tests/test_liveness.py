"""Engine liveness (t12): the state.json heartbeat and the second-engine refusal.

Obligation o12: a second engine exits 1 with 'engine live' while the heartbeat is
fresh AND its pid is alive, and starts once the heartbeat is stale.
"""

from __future__ import annotations

import json

import pytest

from microduck_cli.behavior.liveness import (
    DEFAULT_STALE_AFTER_S,
    STATE_FILENAME,
    EngineState,
    Heartbeat,
    engine_is_live,
    pid_is_alive,
    read_state,
    refuse_if_engine_live,
    state_path,
)
from microduck_cli.cli._errors import EXIT_USER_ERROR, CliError


def make_heartbeat(state_dir, *, pid=4242, mono=100.0, wall=1_700_000_000.0) -> Heartbeat:
    return Heartbeat(
        path=state_path(state_dir),
        pid=pid,
        clock=lambda: mono,
        wall_clock=lambda: wall,
    )


# --- writing --------------------------------------------------------------


def test_beat_writes_every_documented_field(tmp_path):
    beat = make_heartbeat(tmp_path).beat(tick=17, hz=50.0, achieved_hz=49.5, overruns=3)
    on_disk = json.loads((tmp_path / STATE_FILENAME).read_text(encoding="utf-8"))
    assert on_disk == beat
    assert on_disk["pid"] == 4242
    assert on_disk["started_at"] == 1_700_000_000.0
    assert on_disk["last_beat"] == 100.0
    assert on_disk["last_beat_wall"] == 1_700_000_000.0
    assert (on_disk["tick"], on_disk["hz"], on_disk["achieved_hz"], on_disk["overruns"]) == (
        17,
        50.0,
        49.5,
        3,
    )


def test_beat_is_atomic_and_leaves_no_temp_file(tmp_path):
    hb = make_heartbeat(tmp_path)
    hb.beat(tick=1)
    hb.beat(tick=2)
    assert [p.name for p in sorted(tmp_path.iterdir())] == [STATE_FILENAME]
    assert read_state(tmp_path).tick == 2


def test_beat_creates_a_missing_state_dir(tmp_path):
    nested = tmp_path / "duck" / "state"
    make_heartbeat(nested).beat(tick=1)
    assert (nested / STATE_FILENAME).is_file()


def test_clear_removes_the_file_and_tolerates_its_absence(tmp_path):
    hb = make_heartbeat(tmp_path)
    hb.beat()
    hb.clear()
    hb.clear()
    assert not (tmp_path / STATE_FILENAME).exists()


# --- reading never raises -------------------------------------------------


def test_read_state_of_a_missing_file_is_absent_and_reported(tmp_path, capsys):
    from microduck_cli.behavior import senselog

    senselog.install_logging()
    try:
        assert read_state(tmp_path) is None
    finally:
        import logging

        logging.getLogger(senselog.ROOT_LOGGER_NAME).handlers.clear()
    assert "event=no-heartbeat" in capsys.readouterr().err


def test_read_state_of_a_corrupt_file_is_absent_not_an_exception(tmp_path):
    (tmp_path / STATE_FILENAME).write_text('{"pid": 1, "last_be', encoding="utf-8")
    assert read_state(tmp_path) is None


def test_read_state_of_a_non_object_document_is_absent(tmp_path):
    (tmp_path / STATE_FILENAME).write_text("[1, 2, 3]", encoding="utf-8")
    assert read_state(tmp_path) is None


def test_read_state_ignores_bool_and_non_numeric_fields(tmp_path):
    (tmp_path / STATE_FILENAME).write_text(
        json.dumps({"pid": True, "last_beat": "soon", "tick": 4}), encoding="utf-8"
    )
    state = read_state(tmp_path)
    assert isinstance(state, EngineState)
    assert state.pid is None
    assert state.last_beat is None
    assert state.tick == 4


# --- the liveness decision ------------------------------------------------


def test_fresh_heartbeat_with_a_live_pid_is_live(tmp_path):
    make_heartbeat(tmp_path).beat(tick=9)
    state = engine_is_live(tmp_path, now=100.5, pid_alive=lambda pid: True)
    assert state is not None
    assert state.pid == 4242
    assert state.tick == 9


def test_stale_heartbeat_is_not_live_even_with_a_live_pid(tmp_path):
    make_heartbeat(tmp_path).beat()
    assert (
        engine_is_live(tmp_path, now=100.0 + DEFAULT_STALE_AFTER_S + 0.1, pid_alive=lambda _: True)
        is None
    )


def test_fresh_heartbeat_with_a_dead_pid_is_not_live(tmp_path):
    make_heartbeat(tmp_path).beat()
    assert engine_is_live(tmp_path, now=100.1, pid_alive=lambda _: False) is None


def test_a_stamp_far_in_the_future_is_stale_not_live(tmp_path):
    """A monotonic reset (a reboot) must not lock the operator out forever."""
    make_heartbeat(tmp_path, mono=10_000.0).beat()
    assert engine_is_live(tmp_path, now=5.0, pid_alive=lambda _: True) is None


def test_a_hair_of_skew_still_counts_as_live(tmp_path):
    make_heartbeat(tmp_path, mono=100.0).beat()
    assert engine_is_live(tmp_path, now=99.9, pid_alive=lambda _: True) is not None


def test_now_accepts_a_callable(tmp_path):
    make_heartbeat(tmp_path).beat()
    assert engine_is_live(tmp_path, now=lambda: 100.2, pid_alive=lambda _: True) is not None


# --- the refusal (obligation o12) -----------------------------------------


def test_second_engine_refuses_while_the_heartbeat_is_fresh_and_the_pid_alive(tmp_path):
    make_heartbeat(tmp_path).beat(tick=200)
    with pytest.raises(CliError) as excinfo:
        refuse_if_engine_live(tmp_path, verb="duck engine run", now=100.3, pid_alive=lambda _: True)
    err = excinfo.value
    assert err.code == EXIT_USER_ERROR == 1
    assert "engine live" in err.message
    assert "4242" in err.message
    assert err.remediation


def test_the_same_engine_starts_once_the_heartbeat_is_stale(tmp_path):
    make_heartbeat(tmp_path).beat(tick=200)
    # Fresh: refused.
    with pytest.raises(CliError):
        refuse_if_engine_live(tmp_path, now=100.1, pid_alive=lambda _: True)
    # Stale by more than the TTL: allowed, and the next writer overwrites the file.
    refuse_if_engine_live(
        tmp_path, now=100.0 + DEFAULT_STALE_AFTER_S + 1.0, pid_alive=lambda _: True
    )
    make_heartbeat(tmp_path, pid=99, mono=200.0).beat(tick=1)
    assert read_state(tmp_path).pid == 99


def test_no_heartbeat_at_all_never_refuses(tmp_path):
    refuse_if_engine_live(tmp_path, now=1.0, pid_alive=lambda _: True)


def test_a_corrupt_heartbeat_never_refuses(tmp_path):
    (tmp_path / STATE_FILENAME).write_text("not json", encoding="utf-8")
    refuse_if_engine_live(tmp_path, now=1.0, pid_alive=lambda _: True)


def test_pid_is_alive_answers_for_this_process_and_rejects_zero():
    import os

    assert pid_is_alive(os.getpid()) is True
    assert pid_is_alive(0) is False
    assert pid_is_alive(-1) is False
