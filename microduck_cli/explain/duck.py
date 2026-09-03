"""Explain entries and verb list for the ``duck`` noun.

Owned by the ``duck`` noun task: adding a ``duck`` verb means editing this
module (``VERBS`` + ``ENTRIES``), ``cli/_commands/duck.py`` and
``tests/test_duck.py`` — nothing else. See :mod:`microduck_cli.explain.env` for
the shared conventions.
"""

from __future__ import annotations

VERBS: list[str] = [
    "duck overview — describe the duck noun (operate the duck in robotctl's words)",
]

_DUCK = """\
# microduck-cli duck

Noun group for *operating the duck*: the direct-control surface, spoken in
robotctl's words (init, enable, relax, move, look, do, stop, pose, mouth,
sound) and carried over robotd's JSON-RPC socket.

Scaffold today: only `duck overview` exists. Motion verbs land with the duck
task, gated the way `arm101-cli` gates hardware — a dry-run plan on a non-TTY
without `--apply`, a confirmation on a TTY.

## Usage

    microduck-cli duck
    microduck-cli duck overview
    microduck-cli duck overview --json
"""

_DUCK_OVERVIEW = """\
# microduck-cli duck overview

Read-only description of the `duck` noun: what it will hold (operate the duck in
robotctl's words) and which verbs exist today. Descriptive, so it never
hard-fails — a stray positional argument is accepted and ignored.

## Usage

    microduck-cli duck overview
    microduck-cli duck overview --json
"""

ENTRIES: dict[tuple[str, ...], str] = {
    ("duck",): _DUCK,
    ("duck", "overview"): _DUCK_OVERVIEW,
}
