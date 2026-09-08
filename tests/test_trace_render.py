"""Tests for microduck_cli.trace.render — the deterministic MicroDuck Run Trace page."""

from __future__ import annotations

import base64
import json
import os
import re
from pathlib import Path

import pytest

from microduck_cli.cli._errors import CliError
from microduck_cli.trace import render as render_mod
from microduck_cli.trace.render import Rendered, render, template_text, write

# a 1x1 transparent PNG
PNG_1X1 = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk"
    "YPhfDwAChwGA60e6kgAAAABJRU5ErkJggg=="
)

FAIL_COLOUR = "var(--bad)"


def _event(t, lane, kind, label, **extra):
    rec = {"t": t, "wall": 1000.0 + t, "lane": lane, "kind": kind, "label": label}
    rec.update(extra)
    return rec


def _write_run(tmp_path: Path, events: list[dict], meta: dict | None = None, shots=()) -> Path:
    run = tmp_path / "run"
    run.mkdir(parents=True)
    (run / "meta.json").write_text(json.dumps({"t0_wall": 1000.0, **(meta or {})}))
    (run / "events.jsonl").write_text("".join(json.dumps(e) + "\n" for e in events))
    if shots:
        (run / "shots").mkdir()
        for name in shots:
            (run / "shots" / name).write_bytes(PNG_1X1)
    return run


def _basic_events() -> list[dict]:
    return [
        _event(0.1, "operator", "note", "run begins", step="0"),
        _event(1.0, "cli", "cmd-start", "microduck env doctor", step="5"),
        _event(
            1.2,
            "cli",
            "cmd-end",
            "microduck env doctor",
            step="5",
            rc=0,
            elapsed=0.2,
            stdout_first="microduck-cli env doctor: healthy",
            stdout_lines=15,
            stderr_lines=0,
            out="steps/01-doctor.out",
        ),
        _event(2.0, "robotd", "log", "robotd: control loop running joints=15 hz=50.0"),
        _event(
            2.5, "engine", "stderr", "[SENSE stage=rule source=r event=cooldown] dropped", step="9"
        ),
        _event(
            2.55, "engine", "stderr", "[SENSE stage=rule source=r event=cooldown] dropped", step="9"
        ),
        _event(
            2.7,
            "engine",
            "stderr",
            "[SENSE stage=rule source=r event=fired] look -> look-1",
            step="9",
        ),
    ]


def test_render_is_deterministic(tmp_path):
    run = _write_run(tmp_path, _basic_events(), shots=["s0.png"])
    a = render(run)
    b = render(run)
    assert a == b
    assert a.index_html.encode() == b.index_html.encode()


def test_fragment_and_index_shapes(tmp_path):
    run = _write_run(tmp_path, _basic_events())
    out = render(run)
    assert isinstance(out, Rendered)
    frag = out.trace_html
    assert frag.lstrip().startswith("<title>")
    for tag in ("<!doctype", "<html>", "<head>", "<body>"):
        assert tag not in frag.lower()
    low = out.index_html.lower()
    for tag in ("<!doctype html>", "<html>", "<head>", "<body>", "</body>", "</html>"):
        assert low.count(tag) == 1, tag
    assert frag in out.index_html


def test_failed_then_retried_command_renders_both_spans(tmp_path):
    events = [
        _event(1.0, "cli", "cmd-start", "microduck env up --sim", step="6"),
        _event(
            3.0,
            "cli",
            "cmd-end",
            "microduck env up --sim",
            step="6",
            rc=2,
            elapsed=2.0,
            stdout_first="",
            stdout_lines=0,
            stderr_lines=3,
            out="steps/01.out",
        ),
        _event(4.0, "cli", "cmd-start", "microduck env up --sim", step="6"),
        _event(
            5.0,
            "cli",
            "cmd-end",
            "microduck env up --sim",
            step="6",
            rc=0,
            elapsed=1.0,
            stdout_first="microduck-cli env up: healthy (sim)",
            stdout_lines=3,
            stderr_lines=1,
            out="steps/02.out",
        ),
    ]
    out = render(_write_run(tmp_path, events))
    spans = json.loads(re.search(r"const SPANS=(\[.*?\]);\n", out.trace_html).group(1))
    assert [s["rc"] for s in spans] == [2, 0]
    assert [(s["t0"], s["t1"]) for s in spans] == [(1.0, 3.0), (4.0, 5.0)]
    assert FAIL_COLOUR in out.trace_html


def test_no_shots_renders_reason_text(tmp_path):
    out = render(_write_run(tmp_path, _basic_events()))
    assert "no viewer frames in this run" in out.trace_html
    assert "const SHOTS={};" in out.trace_html
    headless = render(_write_run(tmp_path / "h", _basic_events(), meta={"headless": True}))
    assert "headless run: no viewer" in headless.trace_html


def test_shot_with_geometry_renders_css_crop(tmp_path):
    events = _basic_events() + [
        _event(
            3.0,
            "viewer",
            "shot",
            "standing",
            step="7",
            file="shots/s0.png",
            window=False,
            size=[1920, 1080],
            geometry={"x": 102, "y": 70, "w": 1468, "h": 1026, "screen": [1920, 1080]},
        ),
    ]
    out = render(_write_run(tmp_path, events, shots=["s0.png"]))
    shots = json.loads(
        re.search(r"const SHOTS=(\{.*?\});\nconst META", out.trace_html, re.S).group(1)
    )
    assert list(shots) == ["shots/s0.png"]
    assert shots["shots/s0.png"]["window"] is False
    assert shots["shots/s0.png"]["geometry"]["screen"] == [1920, 1080]
    assert shots["shots/s0.png"]["src"].startswith("data:image/png;base64,")
    # the crop is applied in the page from that geometry
    assert 'img.classList.add("cropped")' in out.trace_html
    assert "g.screen[0] / g.w" in out.trace_html


def test_shots_embedded_in_sorted_filename_order(tmp_path):
    names = ["s10-b.jpg", "s2-a.png", "s1-c.jpg"]
    run = _write_run(tmp_path, _basic_events(), shots=names)
    shots = json.loads(
        re.search(r"const SHOTS=(\{.*?\});\nconst META", render(run).trace_html, re.S).group(1)
    )
    assert list(shots) == ["shots/s1-c.jpg", "shots/s10-b.jpg", "shots/s2-a.png"]
    assert shots["shots/s1-c.jpg"]["src"].startswith("data:image/jpeg;base64,")


def test_engine_chatter_is_thinned_into_buckets(tmp_path):
    out = render(_write_run(tmp_path, _basic_events()))
    events = json.loads(re.search(r"const EVENTS=(\[.*?\]);\n", out.trace_html).group(1))
    cool = [e for e in events if e.get("ev") == "cooldown"]
    assert len(cool) == 1
    assert cool[0]["n"] == 2
    assert any(e.get("ev") == "fired" for e in events)


def test_render_reads_no_environment(tmp_path, monkeypatch):
    run = _write_run(tmp_path, _basic_events())
    before = render(run)
    monkeypatch.setattr(os, "environ", {})
    assert render(run) == before


def test_meta_optional_beyond_t0(tmp_path):
    run = _write_run(tmp_path, _basic_events())
    out = render(run)
    meta = json.loads(re.search(r"const META=(\{.*?\});$", out.trace_html, re.M).group(1))
    assert meta["t0_wall"] == 1000.0
    assert meta["t_end"] == 2.7
    assert "checks" not in meta


def test_meta_checks_and_notes_pass_through(tmp_path):
    meta = {
        "checks": [["pytest", "1124 passed", "pass"]],
        "notes": ["**Bold.** rest"],
        "title": "T",
        "t_end": 99,
    }
    out = render(_write_run(tmp_path, _basic_events(), meta=meta))
    got = json.loads(re.search(r"const META=(\{.*?\});$", out.trace_html, re.M).group(1))
    assert got["checks"] == [["pytest", "1124 passed", "pass"]]
    assert got["notes"] == ["**Bold.** rest"]
    assert got["t_end"] == 99


def test_script_close_tag_in_labels_is_escaped(tmp_path):
    events = [_event(0.5, "operator", "note", "bad </script><b>x")]
    out = render(_write_run(tmp_path, events))
    assert "</script><b>x" not in out.trace_html
    assert "<\\/script>" in out.trace_html


def test_write_emits_both_files(tmp_path):
    run = _write_run(tmp_path, _basic_events(), shots=["s0.png"])
    trace_path, index_path = write(run)
    assert Path(trace_path).name == "trace.html"
    assert Path(index_path).name == "index.html"
    assert Path(trace_path).read_text().lstrip().startswith("<title>")
    assert Path(index_path).read_text().startswith("<!doctype html>")


def test_template_resource_resolves():
    text = template_text()
    assert text.lstrip().startswith("<title>")
    assert render_mod.DATA_SLOT in text


def test_missing_events_is_a_cli_error(tmp_path):
    run = tmp_path / "empty"
    run.mkdir()
    (run / "meta.json").write_text('{"t0_wall": 1.0}')
    with pytest.raises(CliError) as exc:
        render(run)
    assert "events.jsonl" in exc.value.message


def test_bad_event_line_names_the_line(tmp_path):
    run = tmp_path / "bad"
    run.mkdir()
    (run / "meta.json").write_text('{"t0_wall": 1.0}')
    (run / "events.jsonl").write_text(json.dumps(_event(0.1, "cli", "note", "ok")) + "\n{oops\n")
    with pytest.raises(CliError) as exc:
        render(run)
    assert "line 2" in exc.value.message


def test_rundir_like_object_is_accepted(tmp_path):
    run = _write_run(tmp_path, _basic_events(), shots=["s0.png"])

    class FakeRunDir:
        path = run
        events_path = run / "events.jsonl"
        shots = run / "shots"
        meta_path = run / "meta.json"

        def read_meta(self):
            return {"t0_wall": 1000.0, "title": "via RunDir"}

    out = render(FakeRunDir())
    assert '"title":"via RunDir"' in out.trace_html


def _meta_of(html: str) -> dict:
    return json.loads(re.search(r"const META=(\{.*?\});$", html, re.M).group(1))


MALFORMED_META = {
    "checks": [None, ["only", "two"], ["a", "b", "c"], "str"],
    "iterations": [[0, "x"], [1, 0.8], "junk", [True, 1.0]],
    "step_titles": 5,
    "notes": "one bare note",
    "smoke": [],
    "open_at": "abc",
    "pins": {"microduck": "0cd676d", "bad": 3},
    "engine": "nope",
    "totally_unknown": {"deep": [1, 2]},
}


def test_malformed_optional_meta_is_normalised_not_fatal(tmp_path):
    out = render(_write_run(tmp_path, _basic_events(), meta=MALFORMED_META))
    meta = _meta_of(out.trace_html)
    assert meta["checks"] == [["a", "b", "c"]]
    assert meta["iterations"] == [[1, 0.8]]
    assert "step_titles" not in meta
    assert meta["notes"] == ["one bare note"]
    assert "smoke" not in meta
    assert "open_at" not in meta
    assert meta["pins"] == {"microduck": "0cd676d"}
    assert "engine" not in meta
    assert "totally_unknown" not in meta
    assert meta["headless"] is False


def test_malformed_meta_page_scripts_still_parse(tmp_path):
    out = render(_write_run(tmp_path, _basic_events(), meta=MALFORMED_META))
    for block in re.findall(r"<script>([\s\S]*?)</script>", out.index_html):
        # the data block is plain JSON literals; the page block must be syntactically whole
        assert block.count("{") == block.count("}")
    data = re.search(r"<script>\n(const EVENTS=.*?)\n?</script>", out.index_html, re.S).group(1)
    for line in data.strip().splitlines():
        json.loads(line.split("=", 1)[1].rstrip(";").replace("<\\/", "</"))


def test_t0_wall_must_be_numeric(tmp_path):
    run = _write_run(tmp_path, _basic_events())
    (run / "meta.json").write_text(json.dumps({"t0_wall": "soon"}))
    with pytest.raises(CliError) as exc:
        render(run)
    assert "t0_wall" in exc.value.message


def test_page_keeps_its_structural_ids(tmp_path):
    out = render(_write_run(tmp_path, _basic_events(), shots=["s0.png"]))
    for anchor in ('id="map"', 'id="tlsvg"', 'id="frame"', 'id="ladder"'):
        assert anchor in out.index_html


def _events_of(html: str) -> list[dict]:
    return json.loads(re.search(r"const EVENTS=(\[.*?\]);$", html, re.M).group(1))


def _spans_of(html: str) -> list[dict]:
    return json.loads(re.search(r"const SPANS=(\[.*?\]);$", html, re.M).group(1))


def test_timeline_routes_events_through_the_same_lane_map_as_spans():
    # Qodo 3959991560: events on the microduck / rl lanes fell through laneIdx[e.lane]
    src = template_text()
    timeline = src[src.index("laneIdx[laneOf(s.lane)]") :]
    events_loop = timeline[timeline.index("for (const e of events)") :]
    assert "laneIdx[laneOf(e.lane)]" in events_loop[:200]
    assert "laneIdx[e.lane]" not in src


def test_rl_lane_event_is_kept_in_the_data(tmp_path):
    events = _basic_events() + [_event(3.0, "rl", "note", "venv probe ok", step="4")]
    out = render(_write_run(tmp_path, events))
    lanes = [e["lane"] for e in _events_of(out.trace_html)]
    assert "rl" in lanes


def test_non_numeric_elapsed_falls_back_to_the_span_bounds(tmp_path):
    # Qodo 3959991597
    events = [
        _event(1.0, "cli", "cmd-start", "microduck env doctor", step="5"),
        _event(1.5, "cli", "cmd-end", "microduck env doctor", step="5", rc=0, elapsed="soon"),
        _event(4.0, "cli", "cmd-end", "orphan", rc=0, elapsed=["x"]),
    ]
    out = render(_write_run(tmp_path, events))
    spans = {s["label"]: s for s in _spans_of(out.trace_html)}
    assert spans["microduck env doctor"]["t0"] == 1.0
    assert spans["microduck env doctor"]["t1"] == 1.5
    assert spans["orphan"]["t0"] == 4.0
    assert spans["orphan"]["t1"] == 4.0


@pytest.mark.parametrize("bad_n", ["three", [3], {"n": 3}])
def test_engine_count_of_the_wrong_type_does_not_break_render(tmp_path, bad_n):
    # Qodo 3959991605
    events = [
        _event(2.5, "engine", "stderr", "[SENSE stage=rule source=r event=cooldown] x", n=bad_n),
        _event(2.6, "engine", "stderr", "[SENSE stage=rule source=r event=fired] y", n=bad_n),
    ]
    out = render(_write_run(tmp_path, events))
    recs = _events_of(out.trace_html)
    fired = next(r for r in recs if r["ev"] == "fired")
    assert "n" not in fired or isinstance(fired["n"], int)
    cooldown = next(r for r in recs if r["ev"] == "cooldown")
    assert cooldown["n"] == 1


def test_wrong_typed_per_kind_extras_never_raise(tmp_path):
    events = [
        _event(1.0, "cli", "cmd-start", "c1", step="7"),
        _event(
            1.2,
            "cli",
            "cmd-end",
            "c1",
            step="7",
            rc="zero",
            elapsed=None,
            stdout_first=["not", "a", "str"],
            stdout_lines="many",
            stderr_lines={"n": 1},
            out=3,
        ),
        _event(2.0, "viewer", "shot", "frame", file="shots/s0.png", window="yes", size="big"),
        _event(2.1, "viewer", "shot", "frame2", file="shots/s1.png", geometry=[1, 2, 3]),
        _event(2.5, "engine", "stderr", "[SENSE stage=x event=fired] z", n=None, ev=5, stage=[]),
        _event(3.5, "robotd", "log", "robotd: text"),
    ]
    out = render(_write_run(tmp_path, events, shots=["s0.png", "s1.png"]))
    spans = _spans_of(out.trace_html)
    assert spans[0]["rc"] is None
    assert spans[0]["outn"] is None
    assert spans[0]["errn"] is None
    assert spans[0]["first"] == ""
    shots = json.loads(re.search(r"const SHOTS=(\{.*?\});$", out.trace_html, re.M).group(1))
    assert shots["shots/s0.png"]["window"] is True
    assert shots["shots/s0.png"]["size"] is None
    assert shots["shots/s1.png"]["geometry"] is None
    recs = _events_of(out.trace_html)
    assert any(r["t"] == 3.5 for r in recs)
    for block in re.findall(r"<script>([\s\S]*?)</script>", out.index_html):
        assert block.count("{") == block.count("}")
