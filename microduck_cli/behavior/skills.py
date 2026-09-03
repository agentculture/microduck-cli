"""A skills snapshot — what the duck can actually run — and rules validated against it.

Deviation d1 (approved): on the pinned daemon (``API_VERSION`` 16,
``docs/upstream-pins.md``), ``robot.policies``/``robot.skills`` are
``METHOD_NOT_FOUND`` — they exist only from ``POLICY_API_VERSION`` (18) on
(``tests/fake_robotd.py``). Before then, the ONLY source for which skills are
configured is the ``SubscribeResult`` answer to ``robot.subscribe``: alongside
``accepted``/``walk``/``stand``/``unavailable`` it carries one extra field per
configured skill, named by the skill and valued by its policy file
(``{"ground_pick": "ground_pick.onnx", ...}``). :func:`skills_from_subscribe_result`
reads exactly that shape. :func:`skills_from_policies_result` reads the richer
``{"slots": {...}, "skills": [{"name", "file"}, ...]}`` shape ``robot.policies``
answers from API 18 on. Both normalise to the same :class:`SkillsSnapshot`, so a
caller (``rules check --skills``, a future ``rules`` noun) does not need to know
which daemon it is talking to.

A caller with no live daemon at all — offline replay, CI, a box with nothing
attached — uses :func:`load_snapshot` on a file :func:`save_snapshot` wrote
earlier (a "``--skills`` snapshot file", the CLI flag ``t21``'s ``rules check``
adds). This module performs exactly the I/O those two functions need (one JSON
file, read or written) and nothing else: no socket, no daemon call. Capturing a
live snapshot from a running daemon is the caller's job (the ``duck``/``rules``
CLI nouns), not this leaf's.

:func:`validate_rule_actions` is the other half: given a validated
:class:`~microduck_cli.behavior.rules.RulesConfig` and a :class:`SkillsSnapshot`,
it reports every react rule whose ``do`` payload names a skill the snapshot does
not carry — offline, before ever reaching the daemon. The refusal message is
pinned verbatim (``tests/test_skills.py``): ``"rule '<id>': <skill> not in
[<sorted skills>]"``.
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Callable

from microduck_cli.behavior.intents import MODES, SOUND_NAMES
from microduck_cli.behavior.rules import KIND_REACT, Rule, RulesConfig
from microduck_cli.cli._errors import EXIT_USER_ERROR, CliError

__all__ = [
    "SOURCE_SUBSCRIBE",
    "SOURCE_POLICIES",
    "SOURCE_SNAPSHOT",
    "SOURCES",
    "SkillsSnapshot",
    "skills_from_subscribe_result",
    "skills_from_policies_result",
    "load_snapshot",
    "save_snapshot",
    "validate_rule_actions",
]

#: A snapshot built from ``robot.subscribe``'s ``SubscribeResult`` (API < 18).
SOURCE_SUBSCRIBE = "subscribe"
#: A snapshot built from ``robot.policies`` (API >= 18).
SOURCE_POLICIES = "policies"
#: A snapshot read back from a file :func:`save_snapshot` wrote (offline use).
SOURCE_SNAPSHOT = "snapshot"

SOURCES: frozenset[str] = frozenset({SOURCE_SUBSCRIBE, SOURCE_POLICIES, SOURCE_SNAPSHOT})

#: ``SubscribeResult`` fields that are NOT skill names — the fixed slot report
#: every ``robot.subscribe`` answer carries alongside the per-skill entries.
_SUBSCRIBE_SLOT_FIELDS: frozenset[str] = frozenset({"accepted", "walk", "stand", "unavailable"})


def _error(message: str, remediation: str = "") -> CliError:
    return CliError(code=EXIT_USER_ERROR, message=message, remediation=remediation)


@dataclass(frozen=True)
class SkillsSnapshot:
    """What the duck can run, as of one moment — the validation target.

    ``skills`` is the sorted tuple of configured skill names (a policy is
    loaded for each). ``slots`` is the raw ``walk``/``stand``/``unavailable``
    report, kept alongside for a caller that wants it (never consulted by
    :func:`validate_rule_actions`, which only reads ``skills``). ``source``
    names where this snapshot came from (:data:`SOURCES`); ``api_version`` the
    daemon's reported API version, or the version pinned by whoever wrote the
    file this was loaded from. ``captured_at`` is an ISO-8601 UTC timestamp.
    """

    skills: tuple[str, ...] = ()
    slots: dict[str, object] = field(default_factory=dict)
    source: str = SOURCE_SNAPSHOT
    api_version: int = 0
    captured_at: str = ""


def _now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def skills_from_subscribe_result(
    result: dict, *, api_version: int, captured_at: str | None = None
) -> SkillsSnapshot:
    """Build a :class:`SkillsSnapshot` from a ``robot.subscribe`` ``SubscribeResult``.

    The API-16 shape (``tests/fake_robotd.py: _h_subscribe``): fixed keys
    ``accepted``/``walk``/``stand``/``unavailable``, plus one extra key PER
    configured skill, named by the skill and valued by its policy file name
    (e.g. ``{"kick_left": "kick_left.onnx"}``). Every key outside the fixed set
    is read as a skill name — the value (the file name) is not otherwise used,
    since :class:`SkillsSnapshot` only needs to know WHICH skills are
    configured, not which file backs each.
    """
    if not isinstance(result, dict):
        raise _error(f"a SubscribeResult must be an object (got {result!r})")
    skills = tuple(sorted(key for key in result if key not in _SUBSCRIBE_SLOT_FIELDS))
    slots = {key: result.get(key) for key in ("walk", "stand", "unavailable") if key in result}
    return SkillsSnapshot(
        skills=skills,
        slots=slots,
        source=SOURCE_SUBSCRIBE,
        api_version=api_version,
        captured_at=captured_at or _now_iso(),
    )


def skills_from_policies_result(
    result: dict, *, api_version: int, captured_at: str | None = None
) -> SkillsSnapshot:
    """Build a :class:`SkillsSnapshot` from a ``robot.policies`` answer (API >= 18).

    Shape (``tests/fake_robotd.py: _h_policies``): ``{"slots": {"walk", "stand",
    "unavailable"}, "skills": [{"name", "file"}, ...]}``. Only ``name`` is read
    from each skill entry — ``file`` is not needed here, for the same reason
    :func:`skills_from_subscribe_result` does not need it.
    """
    if not isinstance(result, dict):
        raise _error(f"a robot.policies result must be an object (got {result!r})")
    raw_skills = result.get("skills", [])
    if not isinstance(raw_skills, list):
        raise _error(f"'skills' must be a list (got {raw_skills!r})")
    names: list[str] = []
    for entry in raw_skills:
        if not isinstance(entry, dict) or not isinstance(entry.get("name"), str):
            raise _error(f"each skills[] entry must be an object with a 'name' (got {entry!r})")
        names.append(entry["name"])
    slots = result.get("slots", {})
    if not isinstance(slots, dict):
        raise _error(f"'slots' must be an object (got {slots!r})")
    return SkillsSnapshot(
        skills=tuple(sorted(names)),
        slots=dict(slots),
        source=SOURCE_POLICIES,
        api_version=api_version,
        captured_at=captured_at or _now_iso(),
    )


def save_snapshot(path: str | os.PathLike[str], snapshot: SkillsSnapshot) -> None:
    """Write *snapshot* as JSON to *path* — the only write this module performs."""
    payload = asdict(snapshot)
    payload["skills"] = list(snapshot.skills)
    try:
        Path(path).write_text(
            json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
    except OSError as err:
        raise _error(
            f"skills snapshot {path} could not be written: {err}",
            remediation="check the path and directory permissions",
        ) from err


def load_snapshot(path: str | os.PathLike[str]) -> SkillsSnapshot:
    """Read a :class:`SkillsSnapshot` back from a JSON file :func:`save_snapshot` wrote.

    The offline path: no daemon required. ``source`` on the returned snapshot
    is always :data:`SOURCE_SNAPSHOT` regardless of what the file's own
    ``source`` field says — reading a file is itself the "snapshot" origin.
    """
    try:
        raw_text = Path(path).read_text(encoding="utf-8")
    except OSError as err:
        raise _error(
            f"skills snapshot {path} could not be read: {err}",
            remediation="check the path and file permissions",
        ) from err
    try:
        data = json.loads(raw_text)
    except json.JSONDecodeError as err:
        raise _error(
            f"skills snapshot {path} is not valid JSON: {err}",
            remediation="regenerate the snapshot file",
        ) from err
    if not isinstance(data, dict):
        raise _error(f"skills snapshot {path} must contain a JSON object (got {data!r})")

    skills = data.get("skills", [])
    if not isinstance(skills, list) or not all(isinstance(s, str) for s in skills):
        raise _error(f"skills snapshot {path}: 'skills' must be a list of strings")
    slots = data.get("slots", {})
    if not isinstance(slots, dict):
        raise _error(f"skills snapshot {path}: 'slots' must be an object")
    api_version = data.get("api_version", 0)
    if isinstance(api_version, bool) or not isinstance(api_version, int):
        raise _error(f"skills snapshot {path}: 'api_version' must be an integer")

    return SkillsSnapshot(
        skills=tuple(sorted(skills)),
        slots=dict(slots),
        source=SOURCE_SNAPSHOT,
        api_version=api_version,
        captured_at=str(data.get("captured_at", "")),
    )


def _skill_problem(rule_id: str, skill: object, skills: tuple[str, ...]) -> str:
    return f"rule '{rule_id}': {skill} not in [{', '.join(sorted(skills))}]"


def _do_problem(rule: Rule, snapshot: SkillsSnapshot) -> str | None:
    """A ``do`` rule's skill must be one the daemon reported."""
    skill = rule.params.get("skill")
    if skill in snapshot.skills:
        return None
    return _skill_problem(rule.id, skill, snapshot.skills)


def _mode_problem(rule: Rule, _snapshot: SkillsSnapshot) -> str | None:
    """``mode`` is checked against the known modes — no daemon reports those."""
    mode = rule.params.get("mode")
    if mode in MODES:
        return None
    return f"rule '{rule.id}': {mode} not in [{', '.join(sorted(MODES))}]"


def _sound_problem(rule: Rule, _snapshot: SkillsSnapshot) -> str | None:
    """``sound`` is checked against the voice-bank tags; an absent name is fine."""
    name = rule.params.get("name")
    if name is None or name in SOUND_NAMES:
        return None
    return f"rule '{rule.id}': {name} not in [{', '.join(sorted(SOUND_NAMES))}]"


#: One checker per validated react action; an action absent here is not validated.
_ACTION_CHECKS: dict[str, Callable[[Rule, SkillsSnapshot], str | None]] = {
    "do": _do_problem,
    "mode": _mode_problem,
    "sound": _sound_problem,
}


def validate_rule_actions(config: RulesConfig, snapshot: SkillsSnapshot) -> list[str]:
    """Every problem found matching *config*'s react rules against *snapshot*.

    Offline validation — no daemon call. For every ``do`` react rule, the
    payload's ``skill`` must name one of ``snapshot.skills``; the message is
    pinned verbatim (``tests/test_skills.py``): ``"rule '<id>': <skill> not in
    [<sorted skills>]"``. ``mode`` payloads are checked against the known
    ``walk``/``roller`` modes, and ``sound`` payloads against the known
    voice-bank tags — both independent of the snapshot (no daemon reports
    those), so they are always checked when present.

    Returns an empty list when every action names something that exists.
    Never raises: an unreadable payload shape is itself reported as a problem
    string rather than propagated, so one malformed rule cannot abort
    validation of the rest.
    """
    problems: list[str] = []
    for rule in config.react:
        if rule.kind != KIND_REACT:
            continue  # pragma: no cover - defensive: react tuple is react-only
        check = _ACTION_CHECKS.get(rule.action)
        problem = check(rule, snapshot) if check is not None else None
        if problem is not None:
            problems.append(problem)
    return problems
