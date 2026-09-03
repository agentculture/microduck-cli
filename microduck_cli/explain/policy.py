"""Explain entries and verb list for the ``policy`` noun.

Owned by the ``policy`` noun task: adding a ``policy`` verb means editing this
module (``VERBS`` + ``ENTRIES``), ``cli/_commands/policy.py`` and
``tests/test_policy.py`` — nothing else. See :mod:`microduck_cli.explain.env`
for the shared conventions.
"""

from __future__ import annotations

VERBS: list[str] = [
    "policy overview — describe the policy noun (train, export, publish, install policies)",
]

_POLICY = """\
# microduck-cli policy

Noun group for the *policy lifecycle*: train a policy, export it to a
deployable artifact, publish it, and install it onto a duck.

Scaffold today: only `policy overview` exists. The lifecycle verbs land with the
train lane, which builds argv for the upstream `microduck_rl` tooling rather
than importing it.

## Usage

    microduck-cli policy
    microduck-cli policy overview
    microduck-cli policy overview --json
"""

_POLICY_OVERVIEW = """\
# microduck-cli policy overview

Read-only description of the `policy` noun: what it will hold (train, export,
publish, install policies) and which verbs exist today. Descriptive, so it never
hard-fails — a stray positional argument is accepted and ignored.

## Usage

    microduck-cli policy overview
    microduck-cli policy overview --json
"""

ENTRIES: dict[tuple[str, ...], str] = {
    ("policy",): _POLICY,
    ("policy", "overview"): _POLICY_OVERVIEW,
}
