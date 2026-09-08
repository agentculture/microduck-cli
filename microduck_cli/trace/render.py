"""Render a run directory as the MicroDuck Run Trace page — deterministically.

``render(run_dir)`` reads ``meta.json``, ``events.jsonl`` and ``shots/`` from a
run directory (the layout in ``docs/plans/2026-09-08-run-trace-tool.contract.md``)
and returns two documents built from ONE template:

* ``trace_html`` — the Artifact fragment: starts with ``<title>``, carries no
  ``doctype``/``html``/``head``/``body`` of its own (the Artifact tool wraps it);
* ``index_html`` — the standalone document: a minimal skeleton around the same
  fragment, so it opens from ``file://`` or a static server with no network.

The render is pure. It reads no clock and no environment, sorts everything it
emits, embeds frames in sorted filename order and dumps JSON with sorted keys, so
rendering the same run directory twice yields identical bytes — that property is
what makes a committed example page regenerable, and a test asserts it.

What the page needs from ``meta.json`` is optional except ``t0_wall``: ``title``,
``box``, ``date``, ``subtitle``, ``step_titles`` (step -> label), ``checks``
(rows of ``[name, result, status]``), ``notes`` (strings; a leading ``**bold**``
marker renders bold), ``smoke``, ``iterations``, ``headless``, ``open_at``. A
run directory holding only ``t0_wall`` and events still renders; a malformed optional
field is dropped or coerced (``_page_meta``), never a blank page.
"""

from __future__ import annotations

import base64
import json
import math
import os
import re
from dataclasses import dataclass
from importlib import resources
from pathlib import Path
from typing import Any

from microduck_cli.cli._errors import EXIT_USER_ERROR, CliError

TEMPLATE_RESOURCE = "template.html"
DATA_SLOT = "/*DATA*/"
INDEX_HEAD = (
    "<!doctype html><html><head>"
    '<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
    "</head><body>\n"
)
INDEX_TAIL = "\n</body></html>\n"

#: Engine events thinned into buckets of this many seconds (one record + a count).
THIN_BUCKET_S = 0.2
#: Which engine ``event=`` tokens are thinned; everything else is kept line by line.
THIN_EVENTS = frozenset({"cooldown", "inhibits"})
LABEL_MAX = 400
FIRST_LINE_MAX = 220
_MIME = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg"}


@dataclass(frozen=True)
class Rendered:
    """The two documents one render produces."""

    trace_html: str
    index_html: str


def template_text() -> str:
    """The page template, read from the package resource beside this module."""
    return resources.files("microduck_cli.trace").joinpath(TEMPLATE_RESOURCE).read_text("utf-8")


# --- run-dir access -----------------------------------------------------------


def _paths(run_dir: Any) -> tuple[Path, Path, Path, Path]:
    """``(root, events_path, shots_dir, meta_path)`` from a RunDir-like or a path."""
    root = Path(getattr(run_dir, "path", run_dir))
    events_path = Path(getattr(run_dir, "events_path", root / "events.jsonl"))
    shots = Path(getattr(run_dir, "shots", root / "shots"))
    meta_path = Path(getattr(run_dir, "meta_path", root / "meta.json"))
    return root, events_path, shots, meta_path


def _read_meta(run_dir: Any, meta_path: Path) -> dict[str, Any]:
    reader = getattr(run_dir, "read_meta", None)
    if callable(reader):
        meta = reader()
    elif meta_path.exists():
        meta = json.loads(meta_path.read_text("utf-8"))
    else:
        meta = {}
    if not isinstance(meta, dict):
        raise CliError(
            code=EXIT_USER_ERROR,
            message=f"{meta_path}: meta.json must hold a JSON object",
            remediation="write meta.json as an object with at least a numeric t0_wall",
        )
    return dict(meta)


def _as_dict(event: Any) -> dict[str, Any]:
    if isinstance(event, dict):
        return event
    to_json = getattr(event, "to_json", None)
    if callable(to_json):
        out = to_json()
        return json.loads(out) if isinstance(out, str) else dict(out)
    if hasattr(event, "__dataclass_fields__"):
        from dataclasses import asdict

        return asdict(event)
    raise CliError(
        code=EXIT_USER_ERROR,
        message=f"cannot render an event of type {type(event).__name__}",
        remediation="events must be dicts or microduck_cli.trace.events.Event records",
    )


def _load_events(run_dir: Any, events_path: Path) -> list[dict[str, Any]]:
    """Load through the events module's validator — one set of rules for every reader."""
    if not events_path.exists():
        raise CliError(
            code=EXIT_USER_ERROR,
            message=f"{events_path}: no events.jsonl to render",
            remediation="point trace render at a run directory (trace run / exec / import)",
        )
    from microduck_cli.trace.events import RunDir, load_events

    if isinstance(run_dir, RunDir):
        loaded = load_events(run_dir)
    else:
        loaded = load_events(RunDir(events_path.parent))
    return [_as_dict(e) for e in loaded]


def _num(value: Any, default: float | None = None) -> float | None:
    """A finite float from a free-form field, or ``default`` (never raises)."""
    if isinstance(value, bool):
        return default
    if isinstance(value, (int, float)):
        out = float(value)
    elif isinstance(value, str):
        try:
            out = float(value.strip())
        except ValueError:
            return default
    else:
        return default
    return out if math.isfinite(out) else default


def _int(value: Any, default: int | None = None) -> int | None:
    """An int from a free-form field (bools excluded), or ``default``."""
    if isinstance(value, bool):
        return default
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        try:
            return int(value.strip())
        except ValueError:
            return default
    return default


def _t_of(e: dict[str, Any]) -> float:
    return round(_num(e.get("t"), 0.0) or 0.0, 2)


def _sorted_events(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(events, key=lambda x: (_t_of(x), str(x.get("lane", ""))))


def _step_of(e: dict[str, Any]) -> str | None:
    step = e.get("step")
    return None if step is None else str(step)


def _plain_text(value: Any) -> str:
    return value if isinstance(value, str) else ""


def _span(lane: str, label: str, step: str | None, t0: float, t1: float, end: dict | None) -> dict:
    end = end or {}
    return {
        "t0": t0,
        "t1": t1,
        "lane": lane,
        "step": step,
        "label": label,
        "rc": _int(end.get("rc")),
        "first": _plain_text(end.get("stdout_first"))[:FIRST_LINE_MAX],
        "outn": _int(end.get("stdout_lines")),
        "errn": _int(end.get("stderr_lines")),
    }


def _closed_span(e: dict[str, Any], start: dict[str, Any] | None, lane: str, label: str) -> dict:
    t1 = _t_of(e)
    elapsed = _num(e.get("elapsed"), 0.0) or 0.0
    t0 = _t_of(start) if start else round(t1 - max(elapsed, 0.0), 2)
    return _span(lane, label, _step_of(e), t0, t1, e)


def _engine_fields(e: dict[str, Any], label: str) -> dict[str, Any]:
    ev = e.get("ev") if isinstance(e.get("ev"), str) else None
    stage = e.get("stage") if isinstance(e.get("stage"), str) else None
    if ev is None:
        m = re.search(r"event=([a-z-]+)", label)
        ev = m.group(1) if m else None
    if stage is None:
        m = re.search(r"stage=([a-z]+)", label)
        stage = m.group(1) if m else None
    out: dict[str, Any] = {"ev": ev, "stage": stage}
    n = _int(e.get("n"))
    if n is not None and n > 0:
        out["n"] = n
    return out


def _event_record(e: dict[str, Any], lane: str, kind: str, label: str) -> dict[str, Any]:
    rec: dict[str, Any] = {
        "t": _t_of(e),
        "lane": lane,
        "kind": kind,
        "label": label,
    }
    step = _step_of(e)
    if step is not None:
        rec["step"] = step
    if kind == "shot":
        rec["shot"] = str(e.get("file", ""))
    if kind == "stderr" and lane == "engine":
        rec.update(_engine_fields(e, label))
    return rec


def _spans_and_events(
    events: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Pair ``cmd-start``/``cmd-end`` into spans; thin engine chatter; keep the rest."""
    spans: list[dict[str, Any]] = []
    open_cmds: dict[tuple[str, str], dict[str, Any]] = {}
    kept: list[dict[str, Any]] = []
    for e in _sorted_events(events):
        lane, kind = str(e.get("lane", "")), str(e.get("kind", ""))
        label = str(e.get("label", ""))[:LABEL_MAX]
        if kind == "cmd-start":
            open_cmds[(lane, label)] = e
        elif kind == "cmd-end":
            spans.append(_closed_span(e, open_cmds.pop((lane, label), None), lane, label))
        else:
            kept.append(_event_record(e, lane, kind, label))
    # every span carries a start, so an unterminated command still shows up
    for (lane, label), start in sorted(open_cmds.items(), key=lambda kv: _t_of(kv[1])):
        t0 = _t_of(start)
        spans.append(_span(lane, label, _step_of(start), t0, t0, None))
    spans.sort(key=lambda s: (s["t0"], s["t1"], s["lane"], s["label"]))
    return spans, _thin(kept)


def _thin(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Collapse engine cooldown/inhibits lines to one record per bucket with a count."""
    out: list[dict[str, Any]] = []
    buckets: dict[tuple[str, int], dict[str, Any]] = {}
    for rec in events:
        if rec["lane"] == "engine" and rec.get("ev") in THIN_EVENTS:
            key = (str(rec["ev"]), int(rec["t"] / THIN_BUCKET_S))
            hit = buckets.get(key)
            if hit is not None:
                hit["n"] = hit.get("n", 1) + rec.get("n", 1)
                continue
            rec = dict(rec)
            rec.setdefault("n", 1)
            buckets[key] = rec
        out.append(rec)
    return out


def _window_flag(value: Any) -> bool:
    return value if isinstance(value, bool) else True


def _size_pair(value: Any) -> list[int] | None:
    if isinstance(value, (list, tuple)) and len(value) == 2:
        w, h = _int(value[0]), _int(value[1])
        if w is not None and h is not None and w > 0 and h > 0:
            return [w, h]
    return None


def _geometry(value: Any) -> dict[str, Any] | None:
    """A crop rectangle the page can use: x, y, w, h ints and a positive screen size."""
    if not isinstance(value, dict):
        return None
    out: dict[str, Any] = {}
    for key in ("x", "y", "w", "h"):
        num = _int(value.get(key))
        if num is None:
            return None
        out[key] = num
    if out["w"] <= 0 or out["h"] <= 0:
        return None
    screen = _size_pair(value.get("screen"))
    if screen is not None:
        out["screen"] = screen
    return out


def _shots(shots_dir: Path, events: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Frames keyed by the shot event's ``file``, embedded as data URIs in sorted order."""
    by_file: dict[str, dict[str, Any]] = {}
    for e in events:
        if e.get("kind") == "shot" and e.get("file"):
            by_file[str(e["file"])] = e
    out: dict[str, dict[str, Any]] = {}
    if not shots_dir.is_dir():
        return out
    root = shots_dir.parent
    for path in sorted(shots_dir.iterdir()):
        mime = _MIME.get(path.suffix.lower())
        if mime is None or not path.is_file():
            continue
        rel = path.relative_to(root).as_posix()
        meta = by_file.get(rel) or by_file.get(path.name) or {}
        out[rel] = {
            "src": f"data:{mime};base64," + base64.b64encode(path.read_bytes()).decode("ascii"),
            "window": _window_flag(meta.get("window")),
            "size": _size_pair(meta.get("size")),
            "geometry": _geometry(meta.get("geometry")),
        }
    return out


def _data_js(
    events: list[dict[str, Any]],
    spans: list[dict[str, Any]],
    shots: dict[str, dict[str, Any]],
    meta: dict[str, Any],
) -> str:
    def dumps(obj: Any) -> str:
        text = json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        return text.replace("</", "<\\/")  # never close the page's own <script>

    return (
        f"const EVENTS={dumps(events)};\n"
        f"const SPANS={dumps(spans)};\n"
        f"const SHOTS={dumps(shots)};\n"
        f"const META={dumps(meta)};"
    )


def _text(value: Any) -> str | None:
    return value if isinstance(value, str) else None


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def _str_map(value: Any) -> dict[str, str]:
    if not isinstance(value, dict):
        return {}
    return {str(k): v for k, v in value.items() if isinstance(v, str)}


def _check_rows(value: Any) -> list[list[str]]:
    rows: list[list[str]] = []
    for row in value if isinstance(value, list) else []:
        if isinstance(row, list) and len(row) == 3 and all(isinstance(x, str) for x in row):
            rows.append(list(row))
    return rows


def _notes(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]
    return [n for n in value if isinstance(n, str)] if isinstance(value, list) else []


def _iterations(value: Any) -> list[list[float]]:
    out: list[list[float]] = []
    for item in value if isinstance(value, list) else []:
        if not (isinstance(item, list) and len(item) == 2):
            continue
        idx, dur = item
        if isinstance(idx, bool) or not isinstance(idx, int) or _number(dur) is None:
            continue
        out.append([idx, float(dur)])
    return out


_SMOKE_KEYS = {
    "ok": lambda v: v if isinstance(v, bool) else None,
    "elapsed_s": _number,
    "maxrss_gb": _number,
    "warp": _text,
    "envs": _number,
    "iters": _number,
}


def _smoke(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    out: dict[str, Any] = {}
    for key, coerce in _SMOKE_KEYS.items():
        if key in value:
            got = coerce(value[key])
            if got is not None:
                out[key] = got
    return out


def _t_end(meta: dict[str, Any], events: list[dict[str, Any]], spans: list) -> float:
    given = _number(meta.get("t_end"))
    if given is not None:
        return given
    last = 0.0
    for e in events:
        last = max(last, _num(e.get("t"), 0.0) or 0.0)
    for s in spans:
        last = max(last, _num(s.get("t1"), 0.0) or 0.0)
    return round(last, 1) or 1.0


#: Optional meta fields the page reads, each with the normaliser that fixes its shape.
#: A malformed known field is dropped or coerced, never fatal; unknown fields never
#: reach the page (they stay in ``meta.json``).
_PAGE_FIELDS: dict[str, Any] = {
    "title": _text,
    "subtitle": _text,
    "box": _text,
    "date": _text,
    "wheel": _text,
    "api": _number,
    "open_at": _number,
    "step_titles": _str_map,
    "pins": _str_map,
    "checks": _check_rows,
    "notes": _notes,
    "iterations": _iterations,
    "smoke": _smoke,
    "engine": lambda v: dict(v) if isinstance(v, dict) else {},
}


def _page_meta(meta: dict[str, Any], events: list[dict[str, Any]], spans: list) -> dict[str, Any]:
    """Only what the page reads, every field in the shape the template destructures."""
    t0_wall = _number(meta.get("t0_wall"))
    if t0_wall is None:
        raise CliError(
            code=EXIT_USER_ERROR,
            message="meta.json: t0_wall must be a number (the run's start, epoch seconds)",
            remediation='write meta.json with {"t0_wall": <epoch seconds>} or re-import the run',
        )
    out: dict[str, Any] = {"t0_wall": t0_wall, "t_end": _t_end(meta, events, spans)}
    out["headless"] = meta.get("headless") is True
    for key, normalise in _PAGE_FIELDS.items():
        if key not in meta:
            continue
        got = normalise(meta[key])
        if got is None or got == {} or got == []:
            continue
        out[key] = got
    return out


# --- public --------------------------------------------------------------------


def render(run_dir: Any) -> Rendered:
    """Render one run directory; pure and deterministic (see the module docstring)."""
    _root, events_path, shots_dir, meta_path = _paths(run_dir)
    meta = _read_meta(run_dir, meta_path)
    raw = _load_events(run_dir, events_path)
    # the shot events keep their `file` for the frame map; the page keys frames by it
    for e in raw:
        if e.get("kind") == "shot" and e.get("file"):
            e["file"] = str(e["file"])
    spans, events = _spans_and_events(raw)
    for rec in events:
        if rec["kind"] == "shot" and rec.get("shot") and not rec["shot"].startswith("shots/"):
            rec["shot"] = "shots/" + rec["shot"]
    shots = _shots(shots_dir, raw)
    template = template_text()
    if DATA_SLOT not in template:
        raise CliError(
            code=EXIT_USER_ERROR,
            message="the trace template has no data slot",
            remediation=f"restore {TEMPLATE_RESOURCE} from the package",
        )
    fragment = template.replace(
        DATA_SLOT, _data_js(events, spans, shots, _page_meta(meta, events, spans)), 1
    )
    if not fragment.lstrip().startswith("<title>"):
        raise CliError(
            code=EXIT_USER_ERROR,
            message="the trace template must start with <title>",
            remediation=f"restore {TEMPLATE_RESOURCE} from the package",
        )
    return Rendered(trace_html=fragment, index_html=INDEX_HEAD + fragment + INDEX_TAIL)


def write(run_dir: Any) -> tuple[str, str]:
    """Render and write ``trace.html`` and ``index.html`` into the run dir; return the paths."""
    root, *_ = _paths(run_dir)
    rendered = render(run_dir)
    trace_path = os.path.join(str(root), "trace.html")
    index_path = os.path.join(str(root), "index.html")
    Path(trace_path).write_text(rendered.trace_html, "utf-8")
    Path(index_path).write_text(rendered.index_html, "utf-8")
    return trace_path, index_path
