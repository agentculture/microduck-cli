#!/usr/bin/env python3
"""Check a tutorial's fenced shell commands against captured verification records.

Extracts every command line from fenced ``bash``/``sh`` code blocks in a tutorial
Markdown (or MDX) file, then checks whether each command actually appears in one of
the given verification-record files (i.e. was really run and captured). It also
scans the tutorial text (and, optionally, image filenames) for identity-revealing
strings such as a literal home directory path.

Usage::

    python docs/tools/check_tutorial.py TUTORIAL --record FILE [--record FILE ...] \\
        [--images-dir DIR] [--identity STR ...] [--json]

Exit codes: 0 clean (no MISS, no identity hit), 1 a MISS or identity hit was found,
2 a usage error (e.g. a missing file).
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path

DEFAULT_IDENTITIES = ["/home/"]

_FENCE_RE = re.compile(r"^(`{3,}|~{3,})(.*)$")
_TRAILING_COMMENT_RE = re.compile(r"\s+#.*$")
_UV_RUN_PREFIX = "uv run "


@dataclass(frozen=True)
class CommandCheck:
    """The outcome of checking one extracted command against the records."""

    command: str
    hit: bool
    record: str | None


@dataclass(frozen=True)
class IdentityHit:
    """One place an identity-revealing string was found."""

    source: str
    line: int
    text: str


def normalize_ws(text: str) -> str:
    """Collapse all whitespace runs to single spaces and strip the ends."""
    return " ".join(text.split())


def _iter_fenced_blocks(text: str):
    """Yield (info_string, [content_lines]) for every fenced code block in text."""
    lines = text.splitlines()
    i = 0
    n = len(lines)
    while i < n:
        match = _FENCE_RE.match(lines[i].strip())
        if match is None:
            i += 1
            continue
        fence_marker = match.group(1)
        fence_char = fence_marker[0]
        fence_len = len(fence_marker)
        info = match.group(2).strip()
        i += 1
        block_lines: list[str] = []
        while i < n:
            stripped = lines[i].strip()
            if stripped.startswith(fence_char * fence_len) and set(stripped) == {fence_char}:
                i += 1
                break
            block_lines.append(lines[i])
            i += 1
        yield info, block_lines


def _lines_to_commands(lines: list[str]) -> list[str]:
    """Turn the raw lines of one fenced block into logical, joined command strings."""
    commands: list[str] = []
    pending: str | None = None
    for raw in lines:
        stripped = raw.strip()
        if pending is None:
            if stripped == "" or stripped.startswith("#"):
                continue
            if stripped.startswith("$ "):
                stripped = stripped[2:].strip()
            elif stripped == "$":
                stripped = ""
            content = stripped
        else:
            content = pending + " " + stripped
            pending = None
        if content.endswith("\\"):
            pending = content[:-1].rstrip()
            continue
        commands.append(content)
    if pending is not None:
        commands.append(pending)
    return commands


def extract_commands(text: str) -> list[str]:
    """Extract every logical command line from bash/sh fenced code blocks in text.

    Fences whose info string starts with ``bash`` or ``sh`` are considered; a fence
    whose info string contains ``nocheck`` is skipped entirely. Blank lines and
    comment lines (starting with ``#``) are ignored, backslash line continuations
    are joined, and a leading ``$ `` prompt is stripped.
    """
    commands: list[str] = []
    for info, block_lines in _iter_fenced_blocks(text):
        lowered = info.lower()
        if not (lowered.startswith("bash") or lowered.startswith("sh")):
            continue
        if "nocheck" in lowered:
            continue
        commands.extend(_lines_to_commands(block_lines))
    return commands


def _strip_trailing_comment(text: str) -> str:
    """Strip a trailing `` #comment`` (whitespace then ``#``) — never a quoted ``#``.

    Deliberately simple: this does not attempt to parse quoting, so a ``#`` that
    happens to sit inside quotes after whitespace is still stripped. That case is
    rare in the verification records this checks and is treated as out of scope
    (see the regression test documenting it).
    """
    return _TRAILING_COMMENT_RE.sub("", text)


def _canonical(command: str) -> str:
    """Normalise whitespace and drop a leading ``uv run `` for cross-form matching."""
    normalized = normalize_ws(command)
    if normalized.startswith(_UV_RUN_PREFIX):
        normalized = normalized[len(_UV_RUN_PREFIX) :]
    return normalized


def _lines_to_entries(
    lines: list[str], start_lineno: int, require_prompt: bool
) -> list[tuple[int, str]]:
    """Turn raw lines into (line-number, command) entries.

    Mirrors ``_lines_to_commands``'s continuation-joining and prompt-stripping, plus
    trailing-comment stripping. When ``require_prompt`` is True, only lines that
    start with a ``$ `` prompt (after stripping leading whitespace) become entries —
    used for plain record prose and ``text``-fenced transcripts. When False, every
    non-blank, non-comment-only line becomes an entry — used for ``bash``/``sh``
    fenced record blocks, which paste raw shell without a prompt.
    """
    entries: list[tuple[int, str]] = []
    pending: str | None = None
    pending_lineno: int | None = None
    for offset, raw in enumerate(lines):
        lineno = start_lineno + offset
        stripped = raw.strip()
        if pending is None:
            if stripped == "" or stripped.startswith("#"):
                continue
            if stripped.startswith("$ "):
                content = stripped[2:].strip()
            elif stripped == "$":
                content = ""
            elif require_prompt:
                continue
            else:
                content = stripped
            entry_lineno = lineno
        else:
            content = pending + " " + stripped
            entry_lineno = pending_lineno
            pending = None
        if content.endswith("\\"):
            pending = content[:-1].rstrip()
            pending_lineno = entry_lineno
            continue
        content = _strip_trailing_comment(content).strip()
        entries.append((entry_lineno, content))
    if pending is not None:
        entries.append((pending_lineno, _strip_trailing_comment(pending).strip()))
    return entries


def _record_entries(text: str) -> list[tuple[int, str]]:
    """Parse a verification record into (line-number, command) entries.

    An entry comes from every line that starts with a ``$ `` prompt (anywhere in
    the file, fenced or not), plus every line inside a fenced code block whose info
    string starts with ``bash`` or ``sh`` (those paste raw shell with no prompt).
    """
    entries: list[tuple[int, str]] = []
    lines = text.splitlines()
    n = len(lines)
    i = 0
    buffer: list[str] = []
    buffer_start = 1

    def flush() -> None:
        if buffer:
            entries.extend(_lines_to_entries(buffer, buffer_start, require_prompt=True))
            buffer.clear()

    while i < n:
        stripped = lines[i].strip()
        match = _FENCE_RE.match(stripped)
        if match is None:
            if not buffer:
                buffer_start = i + 1
            buffer.append(lines[i])
            i += 1
            continue
        flush()
        fence_marker = match.group(1)
        fence_char = fence_marker[0]
        fence_len = len(fence_marker)
        info = match.group(2).strip().lower()
        i += 1
        block_start_lineno = i + 1
        block_lines: list[str] = []
        while i < n:
            candidate = lines[i].strip()
            if candidate.startswith(fence_char * fence_len) and set(candidate) == {fence_char}:
                i += 1
                break
            block_lines.append(lines[i])
            i += 1
        require_prompt = not (info.startswith("bash") or info.startswith("sh"))
        entries.extend(_lines_to_entries(block_lines, block_start_lineno, require_prompt))
    flush()
    return [(lineno, entry) for lineno, entry in entries if entry != ""]


def check_commands(commands: list[str], record_paths: list[Path]) -> list[CommandCheck]:
    """Check each command against each record file, in order, first match wins.

    A HIT requires the (whitespace-normalised, uv-run-normalised) tutorial command
    to EQUAL a parsed record entry — never a substring match — so a shorter command
    cannot match as a fragment of a longer one, prose cannot match, and tokens split
    across adjacent record lines cannot combine into a false hit.
    """
    per_record_entries = [(str(path), _record_entries(path.read_text())) for path in record_paths]
    results = []
    for command in commands:
        canonical_command = _canonical(command)
        hit_record = None
        for record_path, entries in per_record_entries:
            if any(_canonical(entry) == canonical_command for _lineno, entry in entries):
                hit_record = record_path
                break
        results.append(CommandCheck(command=command, hit=hit_record is not None, record=hit_record))
    return results


def scan_identity_text(source: str, text: str, identities: list[str]) -> list[IdentityHit]:
    """Find every line in text containing any identity string, case-insensitively."""
    hits = []
    lowered_identities = [ident.lower() for ident in identities]
    for lineno, line in enumerate(text.splitlines(), start=1):
        lowered_line = line.lower()
        if any(ident in lowered_line for ident in lowered_identities):
            hits.append(IdentityHit(source=source, line=lineno, text=line))
    return hits


def scan_identity_filenames(paths: list[Path], identities: list[str]) -> list[IdentityHit]:
    """Find every filename among paths containing any identity string."""
    hits = []
    lowered_identities = [ident.lower() for ident in identities]
    for path in paths:
        lowered_name = path.name.lower()
        if any(ident in lowered_name for ident in lowered_identities):
            hits.append(IdentityHit(source=str(path), line=0, text=path.name))
    return hits


def list_images(images_dir: Path) -> list[tuple[str, int]]:
    """List every PNG file directly under images_dir as (name, size-in-bytes)."""
    images = []
    for path in sorted(images_dir.iterdir()):
        if path.is_file() and path.suffix.lower() == ".png":
            images.append((path.name, path.stat().st_size))
    return images


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="check_tutorial.py",
        description=(
            "Check a tutorial's fenced shell commands against verification records, "
            "and scan for identity-revealing strings."
        ),
    )
    parser.add_argument("tutorial", help="path to the tutorial .md/.mdx file")
    parser.add_argument(
        "--record",
        action="append",
        required=True,
        dest="records",
        metavar="FILE",
        help="a captured verification-record file to search (repeatable)",
    )
    parser.add_argument(
        "--images-dir",
        default=None,
        metavar="DIR",
        help="a directory of screenshots to list and identity-scan",
    )
    parser.add_argument(
        "--identity",
        action="append",
        dest="identities",
        default=None,
        metavar="STR",
        help="an identity-revealing string to scan for (repeatable, default: /home/)",
    )
    parser.add_argument("--json", action="store_true", help="emit a single JSON object")
    return parser


def _render_text(
    checks: list[CommandCheck],
    identity_hits: list[IdentityHit],
    images: list[tuple[str, int]],
) -> None:
    for check in checks:
        if check.hit:
            print(f"HIT {check.record}: {check.command}")
        else:
            print(f"MISS: {check.command}")

    for hit in identity_hits:
        print(f"IDENTITY {hit.source}:{hit.line}: {hit.text}")

    for name, size in images:
        print(f"IMAGE {name} {size} bytes")

    hit_count = sum(1 for check in checks if check.hit)
    miss_count = len(checks) - hit_count
    print(
        f"totals: commands={len(checks)} hit={hit_count} miss={miss_count} "
        f"identity_hits={len(identity_hits)} images={len(images)}"
    )


def _render_json(
    checks: list[CommandCheck],
    identity_hits: list[IdentityHit],
    images: list[tuple[str, int]],
    ok: bool,
) -> None:
    payload = {
        "commands": [
            {"command": check.command, "hit": check.hit, "record": check.record} for check in checks
        ],
        "identity_hits": [
            {"source": hit.source, "line": hit.line, "text": hit.text} for hit in identity_hits
        ],
        "images": [{"name": name, "bytes": size} for name, size in images],
        "ok": ok,
    }
    print(json.dumps(payload))


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    tutorial_path = Path(args.tutorial)
    if not tutorial_path.is_file():
        print(f"error: tutorial file not found: {tutorial_path}", file=sys.stderr)
        return 2

    record_paths = [Path(record) for record in args.records]
    for record_path in record_paths:
        if not record_path.is_file():
            print(f"error: record file not found: {record_path}", file=sys.stderr)
            return 2

    images_dir = Path(args.images_dir) if args.images_dir else None
    if images_dir is not None and not images_dir.is_dir():
        print(f"error: images directory not found: {images_dir}", file=sys.stderr)
        return 2

    identities = args.identities if args.identities else list(DEFAULT_IDENTITIES)

    try:
        tutorial_text = tutorial_path.read_text()
    except OSError as exc:
        print(f"error: could not read tutorial: {exc}", file=sys.stderr)
        return 2

    commands = extract_commands(tutorial_text)
    checks = check_commands(commands, record_paths)

    identity_hits = scan_identity_text(str(tutorial_path), tutorial_text, identities)
    images: list[tuple[str, int]] = []
    if images_dir is not None:
        images = list_images(images_dir)
        image_paths = [images_dir / name for name, _size in images]
        identity_hits.extend(scan_identity_filenames(image_paths, identities))

    ok = all(check.hit for check in checks) and not identity_hits

    if args.json:
        _render_json(checks, identity_hits, images, ok)
    else:
        _render_text(checks, identity_hits, images)

    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
