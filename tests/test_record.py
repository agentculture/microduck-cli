"""Tests for the JSONL recorder (``microduck_cli/duck/record.py``).

The recording is evidence, so the properties under test are the ones a
consumer — the replay reader, a bug report, ``jq`` — actually depends on: one
JSON object per line, exactly three keys, a monotonic ``ts``, a known
``source``, arrival order, and **nothing but JSONL on the record stream** no
matter how loud the sense layer gets.
"""

from __future__ import annotations

import io
import json
import logging
import threading
import time
from typing import Callable, Iterator

import pytest

from microduck_cli.cli import main
from microduck_cli.duck.record import (
    DROP_SOURCE_ABSENT,
    RECORD_KEYS,
    RECORD_SCHEMA,
    RECORD_SOURCES,
    Recorder,
    encode,
)
from microduck_cli.ipc import proto
from microduck_cli.ipc.client import RobotClient
from tests.fake_robotd import FakeRobotd

RUN_S = 0.5

#: ``side_links(fake) -> (pad_client, tof_client)``.
_SideLinks = Callable[[FakeRobotd], tuple[RobotClient, RobotClient]]


@pytest.fixture()
def fake() -> Iterator[FakeRobotd]:
    with FakeRobotd() as running:
        yield running


@pytest.fixture()
def client(fake: FakeRobotd) -> Iterator[RobotClient]:
    connected = RobotClient(fake.socket_path, clock=time.monotonic).connect(verify_joints=False)
    try:
        yield connected
    finally:
        connected.close()


@pytest.fixture()
def side_links() -> Iterator[_SideLinks]:
    """Build the pad and ToF links (separate clients) against a running fake."""
    opened: list[RobotClient] = []

    def build(running: FakeRobotd) -> tuple[RobotClient, RobotClient]:
        links = tuple(
            RobotClient(running.socket_path, clock=time.monotonic).connect(verify_joints=False)
            for _ in range(2)
        )
        opened.extend(links)
        return links  # type: ignore[return-value]

    try:
        yield build
    finally:
        for link in opened:
            link.close()


class _Records(logging.Handler):
    def __init__(self) -> None:
        super().__init__()
        self.lines: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.lines.append(record.getMessage())


@pytest.fixture()
def sense_log() -> Iterator[_Records]:
    handler = _Records()
    logger = logging.getLogger("microduck.sense")
    logger.addHandler(handler)
    previous = logger.level
    logger.setLevel(logging.DEBUG)
    try:
        yield handler
    finally:
        logger.setLevel(previous)
        logger.removeHandler(handler)


def _parse(text: str) -> list[dict]:
    lines = [line for line in text.splitlines() if line.strip()]
    records = []
    for line in lines:
        record = json.loads(line)  # every line must parse, on its own
        assert set(record) == set(RECORD_KEYS), f"unexpected keys in {record!r}"
        assert isinstance(record["ts"], float)
        assert record["source"] in RECORD_SOURCES
        records.append(record)
    return records


# ---------------------------------------------------------------------------
# the schema
# ---------------------------------------------------------------------------


def test_schema_names_the_three_keys_and_the_six_sources() -> None:
    assert RECORD_KEYS == ("ts", "source", "params")
    assert set(RECORD_SOURCES) == {"state", "health", "pad", "tof", "remote", "hello"}
    assert RECORD_SCHEMA["keys"] == RECORD_KEYS
    assert RECORD_SCHEMA["sources"] == RECORD_SOURCES


def test_encode_renders_one_object_and_refuses_an_unknown_source() -> None:
    line = encode(1.5, "state", {"policy": "walk"})
    assert json.loads(line) == {"ts": 1.5, "source": "state", "params": {"policy": "walk"}}
    assert "\n" not in line
    with pytest.raises(ValueError, match="unknown record source"):
        encode(1.5, "not-a-source", {})


# ---------------------------------------------------------------------------
# recording against the fake
# ---------------------------------------------------------------------------


def test_half_a_second_yields_parseable_records_with_state_and_health(
    client: RobotClient,
) -> None:
    stream = io.StringIO()
    summary = Recorder(client, stream).run(RUN_S)
    records = _parse(stream.getvalue())

    assert summary.records == len(records) > 0
    sources = [record["source"] for record in records]
    assert sources[0] == "hello", "every recording opens with the handshake"
    assert "state" in sources, "the subscription must produce state frames"
    assert "health" in sources, "health is polled, not streamed — and must still appear"
    stamps = [record["ts"] for record in records]
    assert stamps == sorted(stamps), "ts is monotonic and the file is in arrival order"
    assert summary.by_source["state"] >= 1
    assert 0.4 <= summary.seconds <= 1.5


def test_the_stream_receives_nothing_but_jsonl(client: RobotClient) -> None:
    """Not one byte of prose: every line parses, and the summary is not in the stream."""
    stream = io.StringIO()
    summary = Recorder(client, stream).run(0.2)
    text = stream.getvalue()
    _parse(text)
    assert summary.as_text() not in text
    assert all(line.startswith("{") and line.endswith("}") for line in text.splitlines() if line)


def test_health_is_polled_at_about_two_hertz(client: RobotClient) -> None:
    stream = io.StringIO()
    Recorder(client, stream).run(1.1)
    health = [r for r in _parse(stream.getvalue()) if r["source"] == "health"]
    # 2 Hz over ~1.1 s: the first poll is immediate, then one every 500 ms.
    assert 2 <= len(health) <= 4, f"expected ~3 health polls, got {len(health)}"


def test_remote_session_is_polled_at_about_one_hertz(client: RobotClient) -> None:
    stream = io.StringIO()
    Recorder(client, stream).run(1.1)
    remote = [r for r in _parse(stream.getvalue()) if r["source"] == "remote"]
    assert 1 <= len(remote) <= 3
    assert remote[0]["params"] == {"active": False}


def test_pad_and_tof_frames_are_recorded_when_they_arrive(
    fake: FakeRobotd, client: RobotClient, side_links: _SideLinks
) -> None:
    """Frames pushed mid-run land in the file, tagged by their own source.

    The pad and ToF links are their own clients on their own socket paths — here
    all three point at the one fake, which is exactly the seam under test: what
    changes per source is the *path*, not the daemon behind it.
    """
    pad_link, tof_link = side_links(fake)
    stream = io.StringIO()
    recorder = Recorder(client, stream, pad_client=pad_link, tof_client=tof_link)
    thread = threading.Thread(target=recorder.run, args=(RUN_S,), daemon=True)
    thread.start()
    time.sleep(0.15)  # let the recorder's subscriptions land
    fake.feed_pad_report({"buttons": ["start"], "sticks": [0.0, 0.0]})
    fake.feed_tof_frame({"nearest_m": 0.42, "rows": 8, "cols": 8})
    thread.join(timeout=5.0)
    assert not thread.is_alive()

    records = _parse(stream.getvalue())
    by_source = {record["source"] for record in records}
    assert "pad" in by_source and "tof" in by_source
    tof = [r for r in records if r["source"] == "tof"][0]
    assert tof["params"]["nearest_m"] == 0.42, "params are recorded verbatim"


def test_pad_and_tof_are_never_asked_for_on_the_robot_socket(
    fake: FakeRobotd, client: RobotClient, sense_log: _Records
) -> None:
    """Upstream serves pad.input on padd's socket and tof.stream on tofd's.

    With no pad/tof link the recorder must say so by name and record the rest —
    and must NOT fall back to asking robotd for streams robotd does not serve.
    """
    stream = io.StringIO()
    summary = Recorder(client, stream).run(0.2)
    _parse(stream.getvalue())

    assert summary.records > 0, "a duck with no padd is still worth recording"
    called = fake.methods_called()
    assert proto.PAD_INPUT not in called
    assert proto.TOF_STREAM not in called
    absent = [line for line in sense_log.lines if DROP_SOURCE_ABSENT in line]
    assert len(absent) == 2, absent
    assert any("source=pad" in line for line in absent)
    assert any("source=tof" in line for line in absent)


def test_a_burst_of_state_frames_is_recorded_whole_and_in_order(
    fake: FakeRobotd, client: RobotClient
) -> None:
    """Twenty frames pushed back to back yield twenty records, in order.

    The regression this pins: reading the client's peek slots kept only the newest
    frame per method, so a 50 Hz stream was silently decimated to the recorder's
    own poll rate. Draining the arrival-ordered notification queue instead means
    every frame lands, in the order the reader thread saw it, each carrying that
    reader's timestamp rather than the drain time.
    """
    burst = 20
    stream = io.StringIO()
    # state_hz=1: the fake's own timed stream must not drown the burst. Only the
    # frames fed below carry a "seq", so they can be told apart either way.
    recorder = Recorder(client, stream, state_hz=1)
    thread = threading.Thread(target=recorder.run, args=(RUN_S,), daemon=True)
    thread.start()
    time.sleep(0.15)
    for seq in range(burst):
        fake.feed_state({"seq": seq})
    thread.join(timeout=5.0)
    assert not thread.is_alive()

    records = [r for r in _parse(stream.getvalue()) if isinstance(r["params"], dict)]
    fed = [r for r in records if "seq" in r["params"]]
    assert [r["params"]["seq"] for r in fed] == list(range(burst)), "no frame lost, none reordered"
    stamps = [r["ts"] for r in fed]
    assert all(b > a for a, b in zip(stamps, stamps[1:])), f"ts must strictly increase: {stamps}"


def test_the_engine_still_gets_its_peek_slots(client: RobotClient) -> None:
    """The queue is additive: a tick reading the latest sample is unaffected."""
    stream = io.StringIO()
    Recorder(client, stream).run(0.3)
    peeked = client.peek(proto.ROBOT_STATE)
    assert peeked is not None
    params, stamp = peeked
    assert isinstance(params, dict) and stamp is not None


def test_ctrl_c_closes_the_recording_and_still_leaves_a_valid_file(
    client: RobotClient, sense_log: _Records
) -> None:
    def interrupt(_seconds: float) -> None:
        raise KeyboardInterrupt

    stream = io.StringIO()
    summary = Recorder(client, stream, sleep=interrupt).run(30.0)
    records = _parse(stream.getvalue())
    assert records, "the records written before the interrupt survive"
    assert summary.records == len(records)
    assert summary.seconds < 30.0, "the run ended at the interrupt, not at the deadline"
    assert any("interrupted" in line for line in sense_log.lines)


def test_a_refused_stream_is_a_named_drop_and_the_recording_continues(
    fake: FakeRobotd, client: RobotClient, side_links: _SideLinks, sense_log: _Records
) -> None:
    """A tofd that is *there* and says no is a different fact from one that is absent."""
    _pad_link, tof_link = side_links(fake)
    fake.refuse("tof.stream", message="no ToF sensor fitted")
    stream = io.StringIO()
    summary = Recorder(client, stream, tof_client=tof_link).run(0.2)
    _parse(stream.getvalue())
    assert summary.records > 0, "a robot with no ToF is still worth recording"
    assert any("record-subscribe-failed" in line for line in sense_log.lines)


# ---------------------------------------------------------------------------
# the CLI verb
# ---------------------------------------------------------------------------


def test_cli_record_puts_jsonl_on_stdout_and_the_summary_on_stderr(
    fake: FakeRobotd, capsys: pytest.CaptureFixture[str]
) -> None:
    rc = main(["duck", "record", "--seconds", str(RUN_S), "--socket", fake.socket_path])
    captured = capsys.readouterr()
    records = _parse(captured.out)
    assert rc == 0
    assert {"hello", "state", "health"} <= {record["source"] for record in records}
    assert "recorded" in captured.err, "the summary is a diagnostic, not a result"
    assert "recorded" not in captured.out


def test_cli_record_opens_the_pad_and_tof_sockets_not_the_robot_one(
    fake: FakeRobotd,
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """End to end: three paths, three links, one recording.

    ``DUCK_PAD_SOCKET`` names padd's socket and ``<state>/<duck>-tof.sock`` is
    tofd's, per ``duck.addressing``; both are pointed at the one fake here (the
    seam is the path, not the daemon). Frames pushed on each land in the file.
    """
    state = tmp_path / "state"
    state.mkdir()
    (state / "duck-a.sock").symlink_to(fake.socket_path)
    (state / "duck-a-tof.sock").symlink_to(fake.socket_path)
    monkeypatch.setenv("DUCK_SIM_STATE", str(state))
    monkeypatch.setenv("DUCK_PAD_SOCKET", fake.socket_path)

    def feed() -> None:
        time.sleep(0.2)
        fake.feed_pad_report({"buttons": ["start"]})
        fake.feed_tof_frame({"nearest_m": 0.7})

    pusher = threading.Thread(target=feed, daemon=True)
    pusher.start()
    rc = main(["duck", "record", "--seconds", str(RUN_S), "--duck", "duck-a"])
    pusher.join(timeout=5.0)

    records = _parse(capsys.readouterr().out)
    assert rc == 0
    assert {"pad", "tof"} <= {record["source"] for record in records}


def test_cli_record_names_the_absent_sources_on_stderr(
    fake: FakeRobotd, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """No padd and no tofd on this box: two named drops, and a valid recording."""
    monkeypatch.setenv("DUCK_PAD_SOCKET", "/nonexistent/padd/pad.sock")
    rc = main(["duck", "record", "--seconds", "0.2", "--socket", fake.socket_path])
    captured = capsys.readouterr()
    _parse(captured.out)
    assert rc == 0
    absent = [line for line in captured.err.splitlines() if DROP_SOURCE_ABSENT in line]
    assert len(absent) == 2, captured.err  # one line per source, never two for one fact
    assert any("source=pad" in line for line in absent)
    assert any("source=tof" in line for line in absent)


def test_cli_record_out_file_moves_the_summary_to_stdout(
    fake: FakeRobotd, tmp_path, capsys: pytest.CaptureFixture[str]
) -> None:
    target = tmp_path / "run.jsonl"
    rc = main(
        [
            "duck",
            "record",
            "--seconds",
            "0.2",
            "--out",
            str(target),
            "--json",
            "--socket",
            fake.socket_path,
        ]
    )
    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    records = _parse(target.read_text(encoding="utf-8"))
    assert rc == 0
    assert payload["destination"] == str(target)
    assert payload["records"] == len(records)
    assert payload["by_source"]["hello"] == 1
