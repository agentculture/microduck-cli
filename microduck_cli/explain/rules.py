"""Explain entries and verb list for the ``rules`` noun.

Owned by the ``rules`` noun task: adding a ``rules`` verb means editing this
module (``VERBS`` + ``ENTRIES``), ``cli/_commands/rules.py`` and
``tests/test_rules_cli.py`` — nothing else. See :mod:`microduck_cli.explain.env`
for the shared conventions.
"""

from __future__ import annotations

VERBS: list[str] = [
    "rules overview — describe the rules noun (the data-only rules layer and its engine)",
]

_RULES = """\
# microduck-cli rules

Noun group for the *data-only rules layer*: reactions and inhibitions declared
as data (events → rules → actions), merged from a shipped set plus a local
overlay, and evaluated by the rule engine on the tick.

Scaffold today: only `rules overview` exists. The rules data model, the merge
semantics and the engine that runs them land with the rules tasks; the rules
layer itself never imports the CLI or a transport.

## Usage

    microduck-cli rules
    microduck-cli rules overview
    microduck-cli rules overview --json
"""

_RULES_OVERVIEW = """\
# microduck-cli rules overview

Read-only description of the `rules` noun: what it will hold (the data-only
rules layer and its engine) and which verbs exist today. Descriptive, so it
never hard-fails — a stray positional argument is accepted and ignored.

## Usage

    microduck-cli rules overview
    microduck-cli rules overview --json
"""

ENTRIES: dict[tuple[str, ...], str] = {
    ("rules",): _RULES,
    ("rules", "overview"): _RULES_OVERVIEW,
}
