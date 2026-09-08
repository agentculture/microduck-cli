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

Beside the schema this module owns the run directory *as a directory*: making
one (:func:`new_run_dir`, :func:`ensure_run_dir`), editing its metadata
(:func:`update_meta`, :func:`append_note`), describing what is on disk
(:func:`list_runs`, :func:`newest_run`, :func:`count_events`,
:func:`count_shots`) and removing an empty one (:func:`remove_empty_run_dir`).
No caller — the CLI least of all — should be listing a state directory or
rewriting a ``meta.json`` by hand.

Every failure in this module raises :class:`~microduck_cli.cli._errors.CliError`
— never a bare ``KeyError``/``ValueError``/``json.JSONDecodeError`` escaping to
a caller.
"""

from __future__ import annotations

import json
import math
import os
import re
import shutil
import tempfile
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
    "remove_empty_run_dir",
    "RunSummary",
    "TRACE_SUBDIR",
    "append_note",
    "count_events",
    "count_shots",
    "ensure_run_dir",
    "list_runs",
    "newest_run",
    "trace_root",
    "update_meta",
]

#: The subdirectory of a state directory that holds run directories.
TRACE_SUBDIR = "trace"

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
        """The parsed ``meta.json``, or ``{}`` when it does not exist yet.

        Raises :class:`CliError` when the file cannot be read as UTF-8, is not
        valid JSON, or does not decode to a JSON object — a caller downstream
        (``update_meta``, ``append_note``, ``ensure_run_dir``) must never see a
        list or scalar where it expects a mapping.
        """
        if not self.meta_path.exists():
            return {}
        text = _read_text_or_raise(self.meta_path, "meta.json")
        try:
            meta = json.loads(text)
        except json.JSONDecodeError as exc:
            raise CliError(
                EXIT_USER_ERROR,
                f"{self.meta_path}: invalid JSON ({exc})",
                "fix or remove meta.json",
            ) from exc
        if not isinstance(meta, dict):
            raise CliError(
                EXIT_USER_ERROR,
                f"{self.meta_path}: meta.json must be a JSON object",
                "meta.json must contain a single JSON object",
            )
        return meta

    def write_meta(self, meta: dict[str, Any]) -> None:
        """Overwrite ``meta.json`` with *meta*, pretty-printed and sorted."""
        with self.meta_path.open("w", encoding="utf-8") as handle:
            json.dump(meta, handle, indent=2, sort_keys=True)
            handle.write("\n")


def trace_root(state_dir: str) -> Path:
    """``<state_dir>/trace`` — the directory every run directory lives under."""
    return Path(state_dir) / TRACE_SUBDIR


def new_run_dir(state_dir: str, now: float) -> RunDir:
    """Create ``<state_dir>/trace/<YYYYMMDDTHHMMSSZ>/`` (UTC, from *now*).

    When that name is already taken (two runs in the same UTC second), the
    first free ``<stamp>-2``, ``<stamp>-3``, ... is used instead — the run
    root itself is claimed with ``mkdir(exist_ok=False)`` semantics so two
    concurrent callers never collide on the same directory, while
    :meth:`RunDir.ensure` stays idempotent for the ``steps``/``shots``
    subdirectories. Writes ``meta.json`` with ``{"t0_wall": now}``. Takes
    *now* rather than reading the clock itself, so the caller controls
    determinism.
    """
    stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime(now))
    trace_dir = trace_root(state_dir)
    trace_dir.mkdir(parents=True, exist_ok=True)
    suffix = 1
    while True:
        name = stamp if suffix == 1 else f"{stamp}-{suffix}"
        candidate = trace_dir / name
        try:
            candidate.mkdir(parents=False, exist_ok=False)
            break
        except FileExistsError:
            suffix += 1
            continue
    run_dir = RunDir(candidate)
    run_dir.ensure()
    run_dir.write_meta({"t0_wall": now})
    return run_dir


def ensure_run_dir(path: Path | str, now: float) -> RunDir:
    """The run directory at *path*, created with a ``t0_wall`` meta if it is new.

    The counterpart to :func:`new_run_dir` for a caller-chosen location (a
    ``--out``): idempotent, and it never rewrites an existing ``meta.json`` —
    a directory that already carries one keeps it (and its ``t0_wall``) so a
    second command against the same run dir stays on the first run's clock.
    """
    run_dir = RunDir(Path(path))
    run_dir.ensure()
    if not run_dir.read_meta():
        run_dir.write_meta({"t0_wall": now})
    return run_dir


def update_meta(run_dir: RunDir, **fields: Any) -> dict[str, Any]:
    """Merge *fields* into ``meta.json`` and write it back; returns the new meta.

    Read-modify-write of the whole document, so keys the caller does not name
    (``t0_wall``, ``notes``, ...) survive. A field whose value is ``None`` is
    skipped rather than written as null — "no title given" must not overwrite
    a title that is already there.
    """
    meta = run_dir.read_meta()
    meta.update({key: value for key, value in fields.items() if value is not None})
    run_dir.write_meta(meta)
    return meta


def append_note(run_dir: RunDir, text: str) -> list[str]:
    """Append one operator note to ``meta.notes``; returns the resulting list.

    Reads no clock of its own — a note is ordered by the list it lands in.
    """
    meta = run_dir.read_meta()
    notes = [*meta.get("notes", []), text]
    meta["notes"] = notes
    run_dir.write_meta(meta)
    return notes


def count_shots(run_dir: RunDir) -> int:
    """How many frame files ``shots/`` holds (0 when the directory is absent)."""
    if not run_dir.shots.is_dir():
        return 0
    return sum(1 for item in run_dir.shots.iterdir() if item.is_file())


def count_events(run_dir: RunDir) -> int:
    """How many non-blank lines ``events.jsonl`` holds, without validating them.

    A listing must be able to describe a run whose events are malformed, so
    this counts lines where :func:`load_events` would raise.
    """
    path = run_dir.events_path
    if not path.is_file():
        return 0
    with path.open("r", encoding="utf-8") as handle:
        return sum(1 for line in handle if line.strip())


@dataclass(frozen=True)
class RunSummary:
    """One row of :func:`list_runs` — a run directory described without opening it."""

    path: str
    stamp: str
    events: int
    rendered: bool
    title: str | None = None


def _summarise(path: Path) -> RunSummary:
    run_dir = RunDir(path)
    title: str | None = None
    try:
        meta = run_dir.read_meta()
    except (CliError, OSError, json.JSONDecodeError):
        meta = {}
    raw_title = meta.get("title")
    if isinstance(raw_title, str):
        title = raw_title
    return RunSummary(
        path=str(path),
        stamp=path.name,
        events=count_events(run_dir),
        rendered=(path / "index.html").is_file(),
        title=title,
    )


def list_runs(state_dir: str) -> list[RunSummary]:
    """Every run directory under ``<state_dir>/trace``, sorted by name.

    The ordering is the stamp's lexicographic order, which for the UTC stamps
    :func:`new_run_dir` writes is also chronological — so the newest run is
    last, deterministically. A state directory that does not exist (or holds
    no ``trace/``) is an empty list, never an error: listing is descriptive.
    """
    root = trace_root(state_dir)
    if not root.is_dir():
        return []
    return [_summarise(path) for path in sorted(p for p in root.iterdir() if p.is_dir())]


def newest_run(state_dir: str) -> RunDir | None:
    """The last run directory :func:`list_runs` reports, or ``None`` if there is none."""
    runs = list_runs(state_dir)
    return RunDir(Path(runs[-1].path)) if runs else None


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
    if not math.isfinite(t_value):
        raise CliError(
            EXIT_USER_ERROR,
            f"{_EVENTS_FILENAME} line {lineno}: invalid 't' (not finite)",
            "'t' must be a finite float, seconds since meta.t0_wall (NaN/inf are refused)",
        )

    wall_value = obj["wall"]
    if not _is_number(wall_value):
        raise CliError(
            EXIT_USER_ERROR,
            f"{_EVENTS_FILENAME} line {lineno}: invalid 'wall' (not a number)",
            "'wall' must be a float epoch timestamp",
        )
    if not math.isfinite(wall_value):
        raise CliError(
            EXIT_USER_ERROR,
            f"{_EVENTS_FILENAME} line {lineno}: invalid 'wall' (not finite)",
            "'wall' must be a finite float epoch timestamp (NaN/inf are refused)",
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
    return _parse_events(_read_text_or_raise(path, "events"))


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


def _read_text_or_raise(path: Path, what: str) -> str:
    """``path.read_text`` wrapped so ``OSError``/bad-encoding becomes a :class:`CliError`."""
    try:
        return path.read_text(encoding="utf-8")
    except OSError as exc:
        raise CliError(
            EXIT_USER_ERROR,
            f"{path}: cannot read {what} ({exc})",
            "check that the path exists, is a file, and is readable",
        ) from exc
    except UnicodeDecodeError as exc:
        raise CliError(
            EXIT_USER_ERROR,
            f"{path}: not valid UTF-8 at byte {exc.start}",
            f"{what} must be UTF-8 encoded text",
        ) from exc


def import_run(src: str, dst: RunDir, *, redact_home: bool = True) -> int:
    """Adopt an external run directory (``src``) into ``dst``.

    *src* is a directory holding ``events.jsonl`` and optionally ``shots/``
    and ``meta.json``. Everything is read and validated FIRST — events,
    and ``meta.json`` when present — before any write to *dst*; the first
    problem (a missing/unreadable ``events.jsonl``, a bad event line, an
    unreadable or malformed ``meta.json``) raises :class:`CliError` and
    leaves *dst* untouched. Importing a run into itself is refused up front
    for the same reason. When ``redact_home`` (the default), every
    ``/home/<user>`` occurrence in an event's ``label``/per-kind fields and in
    every string value of ``meta.json`` is rewritten to ``~``. ``shots/*`` and
    ``meta.json`` are then copied in (any file already directly under
    ``dst.shots`` is cleared first, so a prior import's stale frames never
    survive); when *src* has no ``meta.json`` a minimal one is written with
    ``t0_wall`` derived from the first event (``wall - t``, so the adopted
    events line up on ``t == 0`` at that wall time) — deterministic, no clock
    read. Returns the number of events written.
    """
    src_path = Path(src)
    if src_path.resolve() == Path(dst.path).resolve():
        raise CliError(
            EXIT_USER_ERROR,
            f"{src}: source and destination are the same directory",
            "pass --out <another dir>",
        )

    events_src = src_path / _EVENTS_FILENAME
    if not events_src.is_file():
        raise CliError(
            EXIT_USER_ERROR,
            f"{src}: no {_EVENTS_FILENAME} found",
            f"point the import source at a directory containing {_EVENTS_FILENAME}",
        )

    events_text = _read_text_or_raise(events_src, "events")
    events = _parse_events(events_text)

    meta_src = src_path / "meta.json"
    meta: dict[str, Any] | None = None
    if meta_src.is_file():
        meta_text = _read_text_or_raise(meta_src, "meta.json")
        try:
            meta = json.loads(meta_text)
        except json.JSONDecodeError as exc:
            raise CliError(
                EXIT_USER_ERROR,
                f"{meta_src}: invalid JSON ({exc})",
                "fix or remove meta.json before importing",
            ) from exc
        if not isinstance(meta, dict):
            raise CliError(
                EXIT_USER_ERROR,
                f"{meta_src}: not a JSON object",
                "meta.json must contain a single JSON object",
            )

    if redact_home:
        for event in events:
            event.label = _redact_home(event.label)
            event.extra = _redact_home_in(event.extra)
        if meta is not None:
            meta = _redact_home_in(meta)

    if meta is None:
        t0_wall = events[0].wall - events[0].t if events else 0.0
        meta = {"t0_wall": t0_wall}

    shots_to_copy = _safe_shots_to_copy(src_path / "shots")

    # Every read above succeeded and validated — only now do we touch dst.
    dst.ensure()

    staging = Path(tempfile.mkdtemp(prefix=".import-", dir=str(dst.path)))
    try:
        _stage_import(staging, events, meta, shots_to_copy)
    except OSError as exc:
        shutil.rmtree(staging, ignore_errors=True)
        raise CliError(
            EXIT_USER_ERROR,
            f"{src}: import failed while staging ({exc})",
            "check that the source shots are readable and the destination has room",
        ) from exc

    _swap_staged_import(dst, staging)

    return len(events)


def _safe_shots_to_copy(shots_src: Path) -> list[Path]:
    """Regular, non-symlinked files directly under *shots_src*, safe to copy.

    A symlink is never followed — a link planted in ``src/shots`` could point
    at an arbitrary host file, and copying its target would smuggle it into
    the run. ``item.resolve()`` is also required to land back inside
    *shots_src* itself, which catches a symlinked ancestor directory (e.g.
    *shots_src* reached through a symlinked parent) that ``is_symlink()``
    alone would miss.
    """
    if not shots_src.is_dir():
        return []
    resolved_root = shots_src.resolve()
    selected: list[Path] = []
    for item in sorted(shots_src.iterdir()):
        if item.is_symlink():
            continue
        if not item.is_file():
            continue
        if item.resolve().parent != resolved_root:
            continue
        selected.append(item)
    return selected


def _stage_import(
    staging: Path,
    events: list[Event],
    meta: dict[str, Any],
    shots_to_copy: list[Path],
) -> None:
    """Write the would-be ``dst`` contents under *staging*, touching nothing else.

    Raises ``OSError`` (never :class:`CliError`) on any failure so the caller
    can attribute it to the import and clean up *staging* uniformly.
    """
    events_tmp = staging / _EVENTS_FILENAME
    with events_tmp.open("w", encoding="utf-8") as handle:
        for event in events:
            handle.write(json.dumps(event.to_json(), separators=(",", ":"), sort_keys=True))
            handle.write("\n")

    meta_tmp = staging / "meta.json"
    with meta_tmp.open("w", encoding="utf-8") as handle:
        json.dump(meta, handle, indent=2, sort_keys=True)
        handle.write("\n")

    shots_tmp = staging / "shots"
    shots_tmp.mkdir(parents=True, exist_ok=True)
    for item in shots_to_copy:
        shutil.copy2(item, shots_tmp / item.name)


def _swap_staged_import(dst: RunDir, staging: Path) -> None:
    """Move a fully-staged import into place, as atomically as the OS allows.

    ``shots/`` is swapped by renaming the old directory aside, renaming the
    staged one in, then removing the old one — each a single filesystem
    rename, not a copy — and ``events.jsonl``/``meta.json`` follow via
    ``os.replace`` (atomic on the same filesystem). By the time this runs,
    every read and every staging write has already succeeded, so this is not
    expected to fail; if it somehow does, later steps are not attempted and
    *staging* (and any half-renamed leftovers) are left for the next run to
    ignore rather than risking a second, riskier repair.
    """
    old_shots = dst.path / ".shots-old"
    if old_shots.exists():
        shutil.rmtree(old_shots, ignore_errors=True)
    dst.shots.rename(old_shots)
    (staging / "shots").rename(dst.shots)
    shutil.rmtree(old_shots, ignore_errors=True)

    os.replace(staging / _EVENTS_FILENAME, dst.events_path)
    os.replace(staging / "meta.json", dst.meta_path)

    shutil.rmtree(staging, ignore_errors=True)


def remove_empty_run_dir(run_dir: RunDir) -> bool:
    """Remove *run_dir* if it holds only a freshly-created layout skeleton.

    "Only the skeleton" means: no ``events.jsonl``, an empty ``steps/``, an
    empty ``shots/``, and — if ``meta.json`` exists at all — one containing
    nothing but ``t0_wall``. Anything else (an events file, a captured step
    or shot, extra meta keys) means real work happened and the directory is
    left alone. Returns whether it removed the directory.
    """
    if not run_dir.path.is_dir():
        return False
    if run_dir.events_path.exists():
        return False
    if run_dir.steps.is_dir() and any(run_dir.steps.iterdir()):
        return False
    if run_dir.shots.is_dir() and any(run_dir.shots.iterdir()):
        return False
    if run_dir.meta_path.exists():
        try:
            meta = run_dir.read_meta()
        except (OSError, json.JSONDecodeError):
            return False
        if set(meta.keys()) - {"t0_wall"}:
            return False
    shutil.rmtree(run_dir.path)
    return True
