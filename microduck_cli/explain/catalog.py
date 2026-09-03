"""Markdown catalog for ``microduck-cli explain <path>``.

Each entry is verbatim markdown. Keys are command-path tuples. The empty tuple,
``("microduck-cli",)`` and ``("microduck",)`` all resolve to the root entry.

Keep bodies self-contained: an agent reading one entry should get enough
context without chaining reads.

Per-noun split
--------------
The global verbs (``whoami``, ``learn``, ``explain``, ``overview``, ``doctor``)
and the ``cli`` noun keep their entries here. Every domain noun owns its own
module — :mod:`microduck_cli.explain.env`, :mod:`~microduck_cli.explain.duck`,
:mod:`~microduck_cli.explain.policy`, :mod:`~microduck_cli.explain.rules` —
exporting ``ENTRIES`` and ``VERBS``; both are merged here so a noun task adds a
verb by editing only its own three files (``cli/_commands/<noun>.py``,
``explain/<noun>.py``, ``tests/test_<noun>.py``).

``VERBS`` is the canonical verb list: ``overview`` renders it and ``learn``
derives its command map and JSON payload from it at import time, so a verb's
one-line summary is written in exactly one place.
"""

from __future__ import annotations

from microduck_cli.explain.duck import ENTRIES as _DUCK_ENTRIES
from microduck_cli.explain.duck import VERBS as _DUCK_VERBS
from microduck_cli.explain.env import ENTRIES as _ENV_ENTRIES
from microduck_cli.explain.env import VERBS as _ENV_VERBS
from microduck_cli.explain.policy import ENTRIES as _POLICY_ENTRIES
from microduck_cli.explain.policy import VERBS as _POLICY_VERBS
from microduck_cli.explain.rules import ENTRIES as _RULES_ENTRIES
from microduck_cli.explain.rules import VERBS as _RULES_VERBS

TAGLINE = "One CLI for the MicroDuck robot — environment, duck control, policies, and rules."

#: Verbs that are not owned by a domain noun.
GLOBAL_VERBS: list[str] = [
    "whoami — identity probe (nick, version, backend, model)",
    "learn — structured self-teaching prompt",
    "explain <path> — markdown docs for a topic",
    "overview — this descriptive snapshot",
    "doctor — check the agent-identity invariants",
    "cli overview — describe the CLI surface itself",
]

#: Verbs contributed by the domain nouns, in noun order.
NOUN_VERBS: list[str] = [*_ENV_VERBS, *_DUCK_VERBS, *_POLICY_VERBS, *_RULES_VERBS]

#: The canonical verb list. Format: ``"<command path> — <one line>"``.
VERBS: list[str] = [*GLOBAL_VERBS, *NOUN_VERBS]


def split_verb(entry: str) -> tuple[str, str]:
    """Split a ``VERBS`` entry into its command path and its one-line summary."""
    path, _, summary = entry.partition(" — ")
    return path.strip(), summary.strip()


def verb_path(entry: str) -> tuple[str, ...]:
    """The command-path tokens of a ``VERBS`` entry (``<placeholders>`` dropped)."""
    path, _ = split_verb(entry)
    return tuple(token for token in path.split() if not token.startswith("<"))


def _root_verb_lines() -> str:
    return "\n".join(
        f"- `microduck-cli {path}` — {summary}." for path, summary in map(split_verb, VERBS)
    )


_ROOT = f"""\
# microduck-cli

{TAGLINE} Agent-agnostic: any agent or human drives the same surface. The
runtime package has no third-party dependencies; every command supports
`--json`, results go to stdout and errors/diagnostics to stderr, never mixed.

The four domain nouns are simulation-first, sim-first meaning no physical
MicroDuck has been driven from this CLI yet — every verb below is exercised
against `robotd --fake`/`--sim` and the in-process fake daemon
(`tests/fake_robotd.py`), never a real duck:

- `env` — bring up / doctor / tear down the simulator or a real duck's socket
  stack (`env up`, `env down`, `env status`, `env doctor`, `env hosts`).
- `duck` — operate one duck directly, in `robotctl`'s own words
  (`health`, `init`, `enable`, `do`, `move`, `record`, …).
- `policy` — the policy lifecycle: list/load/reset slots and skills, and the
  `microduck_rl` train/smoke/export/publish/infer lane.
- `rules` — the data-only rules layer and its 50 Hz tick engine
  (`rules list`, `rules check`, `rules engine run|start|stop|status`,
  `rules intent`).

## Verbs

{_root_verb_lines()}

## Exit-code policy

- `0` success
- `1` user-input error
- `2` environment / setup error
- `3+` reserved

## See also

- `microduck-cli explain whoami`
- `microduck-cli explain doctor`
- `microduck-cli explain env`
- `microduck-cli explain duck`
- `microduck-cli explain policy`
- `microduck-cli explain rules`
- https://github.com/pollen-robotics/microduck/blob/sim-remote-io/docs/robot/cheatsheet.md
"""

_WHOAMI = """\
# microduck-cli whoami

Reports the agent's identity from `culture.yaml`: nick (`suffix`), backend,
served model, and the package version. Read-only.

## Usage

    microduck-cli whoami
    microduck-cli whoami --json
"""

_LEARN = """\
# microduck-cli learn

Prints a structured self-teaching prompt covering purpose, command map,
exit-code policy, `--json` support, and the `explain` pointer.

It closes with **"Authoring the operator skill (operate-microduck)"** — the
consent rule and the recipe for recreating this repo's first-party operator
skill in another runtime: one directory, one `SKILL.md`, frontmatter carrying
`name`, `description` and the load-bearing `type: command`, and the section
list. The same content is in the `--json` payload under
`skills.operate-microduck`. The canonical copy ships here at
`.claude/skills/operate-microduck/SKILL.md`.

## Usage

    microduck-cli learn
    microduck-cli learn --json
"""

_EXPLAIN = """\
# microduck-cli explain <path>

Prints markdown documentation for any noun/verb path. Unlike `--help` (terse,
positional), `explain` is global and addressable by path.

## Usage

    microduck-cli explain microduck-cli
    microduck-cli explain whoami
    microduck-cli explain --json <path>
"""

_OVERVIEW = """\
# microduck-cli overview

Read-only descriptive snapshot of the agent: identity (from `culture.yaml`), the
verb surface, and the sibling-pattern artifacts the repo carries. Accepts an
ignored `target` so a stray path never hard-fails.

## Usage

    microduck-cli overview
    microduck-cli overview --json
"""

_DOCTOR = """\
# microduck-cli doctor

Checks the agent-identity invariants `steward doctor` verifies:
prompt-file-present and backend-consistency (`colleague` → `AGENTS.colleague.md`), plus a
skills-present check. Exits 1 when unhealthy.

## Usage

    microduck-cli doctor
    microduck-cli doctor --json
"""

_CLI = """\
# microduck-cli cli

Noun group for CLI-surface introspection. `cli overview` describes the CLI
itself (distinct from the global `overview`, which describes the agent).

## Usage

    microduck-cli cli overview
    microduck-cli cli overview --json
"""


ENTRIES: dict[tuple[str, ...], str] = {
    (): _ROOT,
    ("microduck-cli",): _ROOT,
    ("microduck",): _ROOT,
    ("whoami",): _WHOAMI,
    ("learn",): _LEARN,
    ("explain",): _EXPLAIN,
    ("overview",): _OVERVIEW,
    ("doctor",): _DOCTOR,
    ("cli",): _CLI,
    ("cli", "overview"): _CLI,
}

for _noun_entries in (_ENV_ENTRIES, _DUCK_ENTRIES, _POLICY_ENTRIES, _RULES_ENTRIES):
    ENTRIES.update(_noun_entries)
