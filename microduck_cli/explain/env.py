"""Explain entries and verb list for the ``env`` noun.

Owned by the ``env`` noun task: adding an ``env`` verb means editing this
module (``VERBS`` + ``ENTRIES``), ``cli/_commands/env.py`` and
``tests/test_env.py`` — nothing else. :mod:`microduck_cli.explain.catalog`
merges ``ENTRIES`` into the global catalog and folds ``VERBS`` into the
canonical verb list that ``overview`` and ``learn`` render.

Each ``VERBS`` entry is ``"<command path> — <one line>"``; the em dash is the
separator the lockstep test splits on.
"""

from __future__ import annotations

VERBS: list[str] = [
    "env overview — describe the environment noun (sim or real MicroDuck bring-up)",
]

_ENV = """\
# microduck-cli env

Noun group for the MicroDuck *environment*: bringing up and doctoring the stack
the duck runs against — the simulator (duck-body + robotd) or a real duck on the
wire — plus the clones, build artifacts and state directory that bring-up needs.

Scaffold today: only `env overview` exists. The bring-up and diagnosis verbs
land with the environment task.

## Usage

    microduck-cli env
    microduck-cli env overview
    microduck-cli env overview --json
"""

_ENV_OVERVIEW = """\
# microduck-cli env overview

Read-only description of the `env` noun: what it will hold (bring up / doctor
the sim or real environment) and which verbs exist today. Descriptive, so it
never hard-fails — a stray positional argument is accepted and ignored.

## Usage

    microduck-cli env overview
    microduck-cli env overview --json
"""

ENTRIES: dict[tuple[str, ...], str] = {
    ("env",): _ENV,
    ("env", "overview"): _ENV_OVERVIEW,
}
