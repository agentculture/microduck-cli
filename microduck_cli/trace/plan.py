"""The run-trace plan: an ordered list of steps, loaded from / dumped to TOML.

A plan can be authored by hand or derived from a tutorial's fenced ``bash``/``sh``
code blocks via :func:`from_tutorial`, using the same extraction rules as
``docs/tools/check_tutorial.py::extract_commands`` (that script is not a package,
so the rules are reproduced here rather than imported).
"""

from __future__ import annotations

import json
import re
import tomllib
from dataclasses import dataclass, field

from microduck_cli.cli._errors import EXIT_USER_ERROR, CliError

# Same lane vocabulary as microduck_cli/trace/events.py (LANES). Not imported from
# there: this module must not depend on a sibling task's module existing yet.
LANES = (
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

# Commands that drive the surrounding host/shell rather than the microduck CLI
# itself are labelled "operator"; everything else defaults to "cli".
_OPERATOR_COMMANDS = frozenset({"free", "git", "pgrep", "cargo", "docker"})

_SIDECAR_OVERRIDE_FIELDS = (
    "sleep_before",
    "sleep_after",
    "shot",
    "retry",
    "note",
    "timeout_s",
    "lane",
    "label",
)

_FENCE_RE = re.compile(r"^(`{3,}|~{3,})(.*)$")
_STEP_HEADING_RE = re.compile(r"^#{1,6}\s*Step\s+(\d+)\b", re.IGNORECASE)
_HEADING_RE = re.compile(r"^#{1,6}\s")


@dataclass
class Step:
    """One line of a run-trace plan: a command to run, plus timing/capture hints."""

    step: str
    label: str
    cmd: str
    lane: str = "cli"
    sleep_before: float = 0
    sleep_after: float = 0
    shot: str | None = None
    retry: int = 0
    note: str | None = None
    timeout_s: float | None = None


@dataclass
class Plan:
    """An ordered plan: a title, its steps, and a free-form meta table."""

    title: str
    steps: list[Step] = field(default_factory=list)
    meta: dict = field(default_factory=dict)


# --------------------------------------------------------------------------
# TOML load / dump
# --------------------------------------------------------------------------


def load_plan(text: str) -> Plan:
    """Parse plan TOML into a :class:`Plan`, validating every step."""
    try:
        data = tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:
        raise CliError(
            EXIT_USER_ERROR,
            f"invalid plan TOML: {exc}",
            "fix the TOML syntax and retry",
        ) from exc

    title = data.get("title", "")
    meta = dict(data.get("meta", {}))
    raw_steps = data.get("step", [])

    steps: list[Step] = []
    for idx, raw in enumerate(raw_steps):
        if not isinstance(raw, dict) or not raw.get("cmd"):
            raise CliError(
                EXIT_USER_ERROR,
                f"plan step {idx}: missing 'cmd'",
                "add a 'cmd' field to the [[step]] table",
            )
        cmd = raw["cmd"]

        retry = raw.get("retry", 0)
        if not isinstance(retry, int) or retry < 0:
            raise CliError(
                EXIT_USER_ERROR,
                f"plan step {idx}: 'retry' must be an integer >= 0",
                "set retry to 0 or a positive integer",
            )

        lane = raw.get("lane", "cli")
        if lane not in LANES:
            raise CliError(
                EXIT_USER_ERROR,
                f"plan step {idx}: unknown lane '{lane}'",
                f"use one of: {', '.join(LANES)}",
            )

        steps.append(
            Step(
                step=str(raw.get("step", "0")),
                label=raw.get("label", cmd),
                cmd=cmd,
                lane=lane,
                sleep_before=raw.get("sleep_before", 0),
                sleep_after=raw.get("sleep_after", 0),
                shot=raw.get("shot"),
                retry=retry,
                note=raw.get("note"),
                timeout_s=raw.get("timeout_s"),
            )
        )

    return Plan(title=title, steps=steps, meta=meta)


def _toml_str(value: str) -> str:
    """A TOML basic string literal for value (json's escaping is a compatible subset)."""
    return json.dumps(value)


def _toml_number(value: float | int) -> str:
    return repr(value) if isinstance(value, float) else str(value)


def _toml_value(value: object) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return _toml_number(value)
    if isinstance(value, str):
        return _toml_str(value)
    if isinstance(value, (list, tuple)):
        return "[" + ", ".join(_toml_value(item) for item in value) + "]"
    raise CliError(
        EXIT_USER_ERROR,
        f"cannot serialise meta value of type {type(value).__name__} to TOML",
        "use a string, number, bool or list of those in plan.meta",
    )


def dump_plan(plan: Plan) -> str:
    """Render plan as plan TOML text (stdlib has no TOML writer)."""
    lines: list[str] = [f"title = {_toml_str(plan.title)}"]

    if plan.meta:
        lines.append("")
        lines.append("[meta]")
        for key, value in plan.meta.items():
            lines.append(f"{key} = {_toml_value(value)}")

    for step in plan.steps:
        lines.append("")
        lines.append("[[step]]")
        lines.append(f"step = {_toml_str(step.step)}")
        lines.append(f"label = {_toml_str(step.label)}")
        lines.append(f"cmd = {_toml_str(step.cmd)}")
        if step.lane != "cli":
            lines.append(f"lane = {_toml_str(step.lane)}")
        if step.sleep_before:
            lines.append(f"sleep_before = {_toml_number(step.sleep_before)}")
        if step.sleep_after:
            lines.append(f"sleep_after = {_toml_number(step.sleep_after)}")
        if step.shot is not None:
            lines.append(f"shot = {_toml_str(step.shot)}")
        if step.retry:
            lines.append(f"retry = {step.retry}")
        if step.note is not None:
            lines.append(f"note = {_toml_str(step.note)}")
        if step.timeout_s is not None:
            lines.append(f"timeout_s = {_toml_number(step.timeout_s)}")

    return "\n".join(lines) + "\n"


# --------------------------------------------------------------------------
# from_tutorial: reproduces docs/tools/check_tutorial.py's extraction rules
# --------------------------------------------------------------------------


def _iter_fenced_blocks(lines: list[str]):
    """Yield (info_string, block_lines, fence_start_index) for each fenced block.

    Mirrors ``check_tutorial._iter_fenced_blocks``, plus the fence's own line
    index so the caller can look up which heading the block sits under.
    """
    i = 0
    n = len(lines)
    while i < n:
        match = _FENCE_RE.match(lines[i].strip())
        if match is None:
            i += 1
            continue
        fence_start = i
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
        yield info, block_lines, fence_start


def _lines_to_commands(lines: list[str]) -> list[str]:
    """Turn the raw lines of one fenced block into logical, joined command strings.

    Identical logic to ``check_tutorial._lines_to_commands``.
    """
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


def _step_numbers_by_line(lines: list[str]) -> list[str]:
    """The "## Step N" number in effect at each line: "0" before/outside one."""
    result: list[str] = []
    current = "0"
    for line in lines:
        stripped = line.strip()
        step_match = _STEP_HEADING_RE.match(stripped)
        if step_match:
            current = step_match.group(1)
        elif _HEADING_RE.match(stripped):
            current = "0"
        result.append(current)
    return result


def _extract_commands_with_step(text: str) -> list[tuple[str, str]]:
    """Every (step-number, command) pair from bash/sh fences, nocheck skipped."""
    lines = text.splitlines()
    step_by_line = _step_numbers_by_line(lines)
    out: list[tuple[str, str]] = []
    for info, block_lines, fence_start in _iter_fenced_blocks(lines):
        lowered = info.lower()
        if not (lowered.startswith("bash") or lowered.startswith("sh")):
            continue
        if "nocheck" in lowered:
            continue
        step_num = step_by_line[fence_start] if fence_start < len(step_by_line) else "0"
        for cmd in _lines_to_commands(block_lines):
            out.append((step_num, cmd))
    return out


def extract_commands(text: str) -> list[str]:
    """The command list alone, in extraction order (parity with check_tutorial)."""
    return [cmd for _step, cmd in _extract_commands_with_step(text)]


def _lane_for(cmd: str) -> str:
    first_token = cmd.split(" ", 1)[0] if cmd else ""
    return "operator" if first_token in _OPERATOR_COMMANDS else "cli"


def _apply_sidecar_override(step_obj: Step, overrides: dict) -> None:
    for key, value in overrides.items():
        if key not in _SIDECAR_OVERRIDE_FIELDS:
            continue
        setattr(step_obj, key, value)


def from_tutorial(mdx_text: str, sidecar: dict | None = None) -> Plan:
    """Build a Plan from a tutorial's fenced bash/sh commands, sidecar-augmented.

    Each command becomes a Step with ``cmd``/``label`` set to the command as
    written, ``step`` set to the enclosing "## Step N" heading (as a string; "0"
    outside any such heading), and ``lane`` "operator" for host-level commands
    (``free``, ``git``, ``pgrep``, ``cargo``, ``docker``) or "cli" otherwise.

    ``sidecar`` maps an exact command string to a dict of Step-field overrides.
    A sidecar key that matches no extracted command is recorded (sorted) in
    ``plan.meta["unmatched"]`` instead of being silently dropped.
    """
    steps = [
        Step(step=step_num, label=cmd, cmd=cmd, lane=_lane_for(cmd))
        for step_num, cmd in _extract_commands_with_step(mdx_text)
    ]

    meta: dict = {}
    if sidecar:
        by_cmd: dict[str, Step] = {}
        for step_obj in steps:
            by_cmd.setdefault(step_obj.cmd, step_obj)

        matched: set[str] = set()
        for key, overrides in sidecar.items():
            target = by_cmd.get(key)
            if target is None:
                continue
            matched.add(key)
            _apply_sidecar_override(target, overrides)

        unmatched = sorted(key for key in sidecar if key not in matched)
        if unmatched:
            meta["unmatched"] = unmatched

    return Plan(title="Tutorial", steps=steps, meta=meta)
