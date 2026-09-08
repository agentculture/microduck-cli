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
run directory holding only ``t0_wall`` and events still renders.
"""

from __future__ import annotations

import base64
import json
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
    """Prefer the events module's loader (its validation); fall back to plain JSONL."""
    try:
        from microduck_cli.trace.events import load_events  # type: ignore[import-not-found]
    except ImportError:
        load_events = None
    if load_events is not None and hasattr(run_dir, "events_path"):
        return [_as_dict(e) for e in load_events(run_dir)]
    if not events_path.exists():
        raise CliError(
            code=EXIT_USER_ERROR,
            message=f"{events_path}: no events.jsonl to render",
            remediation="point trace render at a run directory (trace run / exec / import)",
        )
    out: list[dict[str, Any]] = []
    for lineno, line in enumerate(events_path.read_text("utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError as exc:
            raise CliError(
                code=EXIT_USER_ERROR,
                message=f"{events_path}:{lineno}: not JSON ({exc.msg})",
                remediation="every line of events.jsonl must be one JSON object",
            ) from exc
        if not isinstance(obj, dict) or "t" not in obj or "lane" not in obj or "kind" not in obj:
            raise CliError(
                code=EXIT_USER_ERROR,
                message=f"{events_path}:{lineno}: an event needs t, lane, kind and label",
                remediation="see docs/plans/2026-09-08-run-trace-tool.contract.md",
            )
        out.append(obj)
    return out


# --- the data the page consumes ------------------------------------------------


def _spans_and_events(
    events: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Pair ``cmd-start``/``cmd-end`` into spans; thin engine chatter; keep the rest."""
    spans: list[dict[str, Any]] = []
    open_cmds: dict[tuple[str, str], dict[str, Any]] = {}
    kept: list[dict[str, Any]] = []
    for e in sorted(events, key=lambda x: (float(x.get("t", 0.0)), x.get("lane", ""))):
        lane, kind = str(e.get("lane", "")), str(e.get("kind", ""))
        label = str(e.get("label", ""))[:LABEL_MAX]
        step = e.get("step")
        step_s = None if step is None else str(step)
        if kind == "cmd-start":
            open_cmds[(lane, label)] = e
            continue
        if kind == "cmd-end":
            start = open_cmds.pop((lane, label), None)
            t1 = round(float(e.get("t", 0.0)), 2)
            elapsed = float(e.get("elapsed", 0.0) or 0.0)
            t0 = round(float(start["t"]), 2) if start else round(t1 - elapsed, 2)
            spans.append(
                {
                    "t0": t0,
                    "t1": t1,
                    "lane": lane,
                    "step": step_s,
                    "label": label,
                    "rc": e.get("rc"),
                    "first": str(e.get("stdout_first", "") or "")[:FIRST_LINE_MAX],
                    "outn": e.get("stdout_lines"),
                    "errn": e.get("stderr_lines"),
                }
            )
            continue
        rec: dict[str, Any] = {
            "t": round(float(e.get("t", 0.0)), 2),
            "lane": lane,
            "kind": kind,
            "label": label,
        }
        if step_s is not None:
            rec["step"] = step_s
        if kind == "shot":
            rec["shot"] = str(e.get("file", ""))
        if kind == "stderr" and lane == "engine":
            ev = e.get("ev")
            stage = e.get("stage")
            if ev is None:
                m = re.search(r"event=([a-z-]+)", label)
                ev = m.group(1) if m else None
            if stage is None:
                m = re.search(r"stage=([a-z]+)", label)
                stage = m.group(1) if m else None
            rec["ev"] = ev
            rec["stage"] = stage
            if e.get("n") is not None:
                rec["n"] = int(e["n"])
        kept.append(rec)
    # every span carries a start, so an unterminated command still shows up
    for (lane, label), start in sorted(open_cmds.items(), key=lambda kv: float(kv[1]["t"])):
        t0 = round(float(start["t"]), 2)
        spans.append(
            {
                "t0": t0,
                "t1": t0,
                "lane": lane,
                "step": None if start.get("step") is None else str(start["step"]),
                "label": label,
                "rc": None,
                "first": "",
                "outn": None,
                "errn": None,
            }
        )
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
            "window": bool(meta.get("window", True)),
            "size": meta.get("size"),
            "geometry": meta.get("geometry"),
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


def _page_meta(meta: dict[str, Any], events: list[dict[str, Any]], spans: list) -> dict[str, Any]:
    """Only what the page reads, plus a computed ``t_end`` when meta has none."""
    out = dict(meta)
    if "t_end" not in out:
        last = 0.0
        for e in events:
            last = max(last, float(e["t"]))
        for s in spans:
            last = max(last, float(s["t1"]))
        out["t_end"] = round(last, 1) or 1.0
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
