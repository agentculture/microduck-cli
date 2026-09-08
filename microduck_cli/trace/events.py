"""The run-trace event schema, the run-dir layout, and ``import_run``.

One run directory holds one recorded (or imported) session:

.. code-block:: text

    <run-dir>/
      meta.json        {"t0_wall": float epoch, ...free keys}
      events.jsonl     one JSON object per line, append-only
      steps/           captured stdout/stderr per command
      shots/           viewer frames, named by the shot event's "file"

Every line of ``events.jsonl`` is one event: required fields ``t`` (float
seconds since ``meta.t0_wall``), ``wall`` (float epoch), ``lane`` (one of
:data:`LANES`), ``kind`` (one of :data:`KINDS`) and ``label`` (a string of at
most :data:`MAX_LABEL_LEN` characters); an optional ``step``; and whatever
per-kind fields that kind carries (``rc``/``elapsed``/... for ``cmd-end``,
``file``/``window``/``size`` for ``shot``, and so on — see the contract doc).
:func:`validate_line` enforces only the fields named above; the per-kind
fields are free-form JSON at this layer and pass through as
:attr:`Event.extra` unchanged.

**Step is present-or-absent, never explicit null.** The contract documents
``step`` as "str or null", but a JSONL line either carries a string ``step``
or omits the key entirely — :func:`validate_line`/:meth:`Event.from_dict`
treat a missing key and an explicit ``null`` the same way (as
``Event.step is None``), and :meth:`Event.to_json` omits the key whenever
``step`` is ``None``. This is the one place this module resolves an ambiguity
in the contract: it keeps ``append_event`` deterministic and round-trip-safe
(a byte fixture with no ``step`` key stays free of one after
load-then-append) without inventing a distinct "explicitly null" state
nothing else in the plan needs.

Every failure in this module raises :class:`~microduck_cli.cli._errors.CliError`
— never a bare ``KeyError``/``ValueError``/``json.JSONDecodeError`` escaping to
a caller.
"""

from __future__ import annotations

import json
import re
import shutil
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from microduck_cli.cli._errors import EXIT_USER_ERROR, CliError

__all__ = [
    "LANES",
    "KINDS",
    "MAX_LABEL_LEN",
    "Event",
    "RunDir",
    "new_run_dir",
    "append_event",
    "load_events",
    "validate_line",
    "import_run",
]

#: Every legal ``lane`` tag, in the contract's documented order.
LANES: tuple[str, ...] = (
    "operator",
    "cli",
    "engine",
    "robotd",
    "body",
    "train",
    "viewer",
    "checks",
    "microduck",
    "rl",
)

#: Every legal ``kind`` tag.
KINDS: tuple[str, ...] = ("cmd-start", "cmd-end", "stderr", "log", "shot", "note")

#: The longest a ``label`` may be.
MAX_LABEL_LEN = 400

#: The fields every event carries at the top level; anything else on a parsed
#: object is a per-kind field and lands in :attr:`Event.extra` unchanged.
_CORE_FIELDS = ("t", "wall", "lane", "kind", "label", "step")

_EVENTS_FILENAME = "events.jsonl"

#: Matches ``/home/<user>`` (one path segment, no further ``/``) anywhere in a
#: string — the shape :func:`import_run` redacts to ``~`` in labels.
_HOME_PATH_RE = re.compile(r"/home/[^/\s]+")


def _is_number(value: Any) -> bool:
    """True for an int/float that is not a bool (``isinstance(True, int)`` is)."""
    return isinstance(value, (int, float)) and not isinstance(value, bool)


@dataclass
class Event:
    """One line of ``events.jsonl``.

    ``extra`` carries every per-kind field (``rc``, ``file``, ``ev``, ...) that
    is not one of the five required fields or ``step`` — this module does not
    interpret them, it only round-trips them.
    """

    t: float
    wall: float
    lane: str
    kind: str
    label: str
    step: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)

    def to_json(self) -> dict[str, Any]:
        """Render as the plain dict :func:`append_event` serialises.

        ``step`` is included only when it is not ``None`` — see the module
        docstring for why a missing key and an explicit ``null`` are treated
        the same way throughout this module.
        """
        obj: dict[str, Any] = {
            "t": self.t,
            "wall": self.wall,
            "lane": self.lane,
            "kind": self.kind,
            "label": self.label,
        }
        if self.step is not None:
            obj["step"] = self.step
        obj.update(self.extra)
        return obj

    @classmethod
    def from_dict(cls, obj: dict[str, Any]) -> "Event":
        """Build an :class:`Event` from an already-validated mapping."""
        extra = {k: v for k, v in obj.items() if k not in _CORE_FIELDS}
        return cls(
            t=float(obj["t"]),
            wall=float(obj["wall"]),
            lane=obj["lane"],
            kind=obj["kind"],
            label=obj["label"],
            step=obj.get("step"),
            extra=extra,
        )


@dataclass(frozen=True)
class RunDir:
    """One run directory and the paths inside it. Layout only — no I/O policy."""

    path: Path

    def __post_init__(self) -> None:
        object.__setattr__(self, "path", Path(self.path))

    @property
    def events_path(self) -> Path:
        return self.path / _EVENTS_FILENAME

    @property
    def steps(self) -> Path:
        return self.path / "steps"

    @property
    def shots(self) -> Path:
        return self.path / "shots"

    @property
    def meta_path(self) -> Path:
        return self.path / "meta.json"

    def ensure(self) -> None:
        """Create the run dir and its two subdirectories, idempotently."""
        self.path.mkdir(parents=True, exist_ok=True)
        self.steps.mkdir(parents=True, exist_ok=True)
        self.shots.mkdir(parents=True, exist_ok=True)

    def read_meta(self) -> dict[str, Any]:
        """The parsed ``meta.json``, or ``{}`` when it does not exist yet."""
        if not self.meta_path.exists():
            return {}
        with self.meta_path.open("r", encoding="utf-8") as handle:
            return json.load(handle)

    def write_meta(self, meta: dict[str, Any]) -> None:
        """Overwrite ``meta.json`` with *meta*, pretty-printed and sorted."""
        with self.meta_path.open("w", encoding="utf-8") as handle:
            json.dump(meta, handle, indent=2, sort_keys=True)
            handle.write("\n")


def new_run_dir(state_dir: str, now: float) -> RunDir:
    """Create ``<state_dir>/trace/<YYYYMMDDTHHMMSSZ>/`` (UTC, from *now*).

    Writes ``meta.json`` with ``{"t0_wall": now}``. Takes *now* rather than
    reading the clock itself, so the caller controls determinism.
    """
    stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime(now))
    run_dir = RunDir(Path(state_dir) / "trace" / stamp)
    run_dir.ensure()
    run_dir.write_meta({"t0_wall": now})
    return run_dir


def append_event(run_dir: RunDir, event: Event) -> None:
    """Append one compact JSON line for *event* to ``events.jsonl`` and flush."""
    line = json.dumps(event.to_json(), separators=(",", ":"), sort_keys=True)
    with run_dir.events_path.open("a", encoding="utf-8") as handle:
        handle.write(line)
        handle.write("\n")
        handle.flush()


def validate_line(obj: Any, lineno: int) -> Event:
    """Validate one parsed JSON object and return its :class:`Event`.

    Refuses (as :class:`CliError`, exit :data:`~microduck_cli.cli._errors.EXIT_USER_ERROR`,
    naming *lineno* and the offending field) a missing or invalid ``lane``,
    ``kind``, ``label``, ``t`` or ``wall``. Per-kind fields are not validated
    here — see the module docstring.
    """
    if not isinstance(obj, dict):
        raise CliError(
            EXIT_USER_ERROR,
            f"{_EVENTS_FILENAME} line {lineno}: not a JSON object",
            "each line of events.jsonl must be a single JSON object",
        )

    for required in ("t", "wall", "lane", "kind", "label"):
        if required not in obj:
            raise CliError(
                EXIT_USER_ERROR,
                f"{_EVENTS_FILENAME} line {lineno}: missing field {required!r}",
                f"add {required!r} to the event on line {lineno}",
            )

    lane = obj["lane"]
    if lane not in LANES:
        raise CliError(
            EXIT_USER_ERROR,
            f"{_EVENTS_FILENAME} line {lineno}: invalid lane {lane!r}",
            f"lane must be one of {', '.join(LANES)}",
        )

    kind = obj["kind"]
    if kind not in KINDS:
        raise CliError(
            EXIT_USER_ERROR,
            f"{_EVENTS_FILENAME} line {lineno}: invalid kind {kind!r}",
            f"kind must be one of {', '.join(KINDS)}",
        )

    label = obj["label"]
    if not isinstance(label, str) or not label or len(label) > MAX_LABEL_LEN:
        raise CliError(
            EXIT_USER_ERROR,
            f"{_EVENTS_FILENAME} line {lineno}: invalid label",
            f"label must be a non-empty string of at most {MAX_LABEL_LEN} characters",
        )

    t_value = obj["t"]
    if not _is_number(t_value):
        raise CliError(
            EXIT_USER_ERROR,
            f"{_EVENTS_FILENAME} line {lineno}: invalid 't' (not a number)",
            "'t' must be a float, seconds since meta.t0_wall",
        )

    wall_value = obj["wall"]
    if not _is_number(wall_value):
        raise CliError(
            EXIT_USER_ERROR,
            f"{_EVENTS_FILENAME} line {lineno}: invalid 'wall' (not a number)",
            "'wall' must be a float epoch timestamp",
        )

    step = obj.get("step")
    if step is not None and not isinstance(step, str):
        raise CliError(
            EXIT_USER_ERROR,
            f"{_EVENTS_FILENAME} line {lineno}: invalid 'step' (not a string)",
            "'step' must be a string, or omitted",
        )

    return Event.from_dict(obj)


def _iter_valid_lines(text: str) -> list[tuple[int, str]]:
    return [
        (lineno, raw)
        for lineno, raw in enumerate((line.strip() for line in text.splitlines()), start=1)
        if raw
    ]


def _parse_events(text: str) -> list[Event]:
    """Parse and validate every non-blank line of *text*, in order.

    Raises on the FIRST bad line — a partially-valid file is never partially
    accepted.
    """
    events: list[Event] = []
    for lineno, raw in _iter_valid_lines(text):
        try:
            obj = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise CliError(
                EXIT_USER_ERROR,
                f"{_EVENTS_FILENAME} line {lineno}: invalid JSON ({exc})",
                "fix or remove the malformed line",
            ) from exc
        events.append(validate_line(obj, lineno))
    return events


def load_events(run_dir: RunDir) -> list[Event]:
    """Load and validate every event in *run_dir*'s ``events.jsonl``.

    Returns ``[]`` when the file does not exist yet (a fresh run dir).
    Raises :class:`CliError` naming the first invalid line.
    """
    path = run_dir.events_path
    if not path.exists():
        return []
    return _parse_events(path.read_text(encoding="utf-8"))


def _redact_home(text: str) -> str:
    return _HOME_PATH_RE.sub("~", text)


def _redact_home_in(value: Any) -> Any:
    """Redact every ``/home/<user>`` inside nested strings (``stdout_first``, ...)."""
    if isinstance(value, str):
        return _redact_home(value)
    if isinstance(value, dict):
        return {key: _redact_home_in(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_redact_home_in(item) for item in value]
    return value


def import_run(src: str, dst: RunDir, *, redact_home: bool = True) -> int:
    """Adopt an external run directory (``src``) into ``dst``.

    *src* is a directory holding ``events.jsonl`` and optionally ``shots/``
    and ``meta.json``. Every line is validated first — the first bad line
    raises :class:`CliError` and nothing is written to *dst*. When
    ``redact_home`` (the default), every ``/home/<user>`` occurrence in an
    event's ``label`` (and in every string under its per-kind fields) is
    rewritten to ``~``. ``shots/*`` and ``meta.json`` are
    copied verbatim; when *src* has no ``meta.json`` a minimal one is written
    with ``t0_wall`` derived from the first event (``wall - t``, so the
    adopted events line up on ``t == 0`` at that wall time) — deterministic,
    no clock read. Returns the number of events written.
    """
    src_path = Path(src)
    events_src = src_path / _EVENTS_FILENAME
    if not events_src.is_file():
        raise CliError(
            EXIT_USER_ERROR,
            f"{src}: no {_EVENTS_FILENAME} found",
            f"point the import source at a directory containing {_EVENTS_FILENAME}",
        )

    events = _parse_events(events_src.read_text(encoding="utf-8"))

    if redact_home:
        for event in events:
            event.label = _redact_home(event.label)
            event.extra = _redact_home_in(event.extra)

    dst.ensure()
    if dst.events_path.exists():
        dst.events_path.unlink()
    for event in events:
        append_event(dst, event)

    shots_src = src_path / "shots"
    if shots_src.is_dir():
        for item in sorted(shots_src.iterdir()):
            if item.is_file():
                shutil.copy2(item, dst.shots / item.name)

    meta_src = src_path / "meta.json"
    if meta_src.is_file():
        meta = json.loads(meta_src.read_text(encoding="utf-8"))
    else:
        t0_wall = events[0].wall - events[0].t if events else 0.0
        meta = {"t0_wall": t0_wall}
    dst.write_meta(meta)

    return len(events)
