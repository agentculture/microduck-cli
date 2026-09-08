"""The committed example trace regenerates byte-for-byte.

``docs/traces/2026-09-08-spark-tutorial/`` is the 2026-09-08 DGX Spark tutorial
run, adopted through ``import_run`` and rendered with ``render``. This test is
the "verbatim regeneration" gate: the committed pages must equal a fresh render.
"""

from __future__ import annotations

import json
from pathlib import Path

from microduck_cli.trace.events import RunDir, load_events
from microduck_cli.trace.render import render

EXAMPLE = Path(__file__).resolve().parents[1] / "docs" / "traces" / "2026-09-08-spark-tutorial"


def test_example_renders_byte_for_byte() -> None:
    rendered = render(RunDir(str(EXAMPLE)))
    assert rendered.index_html == (EXAMPLE / "index.html").read_text(encoding="utf-8")
    assert rendered.trace_html == (EXAMPLE / "trace.html").read_text(encoding="utf-8")


def test_example_events_are_redacted() -> None:
    assert "/home/" not in (EXAMPLE / "events.jsonl").read_text(encoding="utf-8")


def test_example_has_twelve_frames_and_the_env_up_retry() -> None:
    events = load_events(RunDir(str(EXAMPLE)))
    shots = [e for e in events if e.kind == "shot"]
    assert len(shots) == 12
    assert sorted(EXAMPLE.joinpath("shots").iterdir()).__len__() == 12
    for shot in shots:
        assert (EXAMPLE / shot.extra["file"]).is_file()
    ends = [e for e in events if e.kind == "cmd-end" and e.label == "microduck env up --sim"]
    assert [e.extra["rc"] for e in ends] == [2, 0]
    assert ends[0].t < ends[1].t


def test_example_meta_and_size() -> None:
    meta = json.loads((EXAMPLE / "meta.json").read_text(encoding="utf-8"))
    assert meta["t0_wall"] > 0
    assert meta["title"] == "MicroDuck Run Trace"
    assert (EXAMPLE / "index.html").stat().st_size < 1_000_000
