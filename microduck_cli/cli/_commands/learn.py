"""``microduck-cli learn`` — the learnability affordance.

Prints a structured self-teaching prompt. Must satisfy the agent-first rubric:
>=200 chars and mention purpose, command map, exit codes, --json, and explain.

The command map (text) and the ``commands`` payload (JSON) are both derived at
import time from the canonical verb list in
:mod:`microduck_cli.explain.catalog`, so a noun task that adds a verb to its own
``explain/<noun>.py`` shows up here without editing this file.
"""

from __future__ import annotations

import argparse

from microduck_cli import __version__
from microduck_cli.cli._output import emit_result
from microduck_cli.explain.catalog import TAGLINE, VERBS, split_verb, verb_path

_PURPOSE = (
    "Agent-agnostic control surface for the MicroDuck robot: bring up the sim or real\n"
    "environment (`env`), operate the duck in robotctl's words (`duck`), train/export/\n"
    "publish/install policies (`policy`), and run the data-only rules layer and its 50 Hz\n"
    "tick engine (`rules`). Sim-first: every verb is exercised against `robotd --fake`/\n"
    "`--sim` and the in-process fake daemon — no physical MicroDuck has been driven yet."
)


def _command_map() -> str:
    rows = [(f"microduck-cli {path}", summary) for path, summary in map(split_verb, VERBS)]
    width = max(len(invocation) for invocation, _ in rows)
    return "\n".join(f"  {invocation.ljust(width)}  {summary}" for invocation, summary in rows)


_TEXT = f"""\
microduck-cli — {TAGLINE}

Purpose
-------
{_PURPOSE}

Commands
--------
{_command_map()}

Machine-readable output
-----------------------
Every command supports --json. Errors in JSON mode emit
{{"code", "message", "remediation"}} to stderr. Stdout and stderr never mix.

Exit-code policy
----------------
  0 success
  1 user-input error (bad flag, bad path, missing arg)
  2 environment / setup error
  3+ reserved

More detail
-----------
  microduck-cli explain microduck-cli
  microduck-cli explain <noun>
"""


def _as_json_payload() -> dict[str, object]:
    return {
        "tool": "microduck-cli",
        "version": __version__,
        "purpose": TAGLINE,
        "commands": [
            {"path": list(verb_path(entry)), "summary": split_verb(entry)[1]} for entry in VERBS
        ],
        "exit_codes": {
            "0": "success",
            "1": "user-input error",
            "2": "environment/setup error",
        },
        "json_support": True,
        "explain_pointer": "microduck-cli explain <path>",
    }


def cmd_learn(args: argparse.Namespace) -> int:
    if getattr(args, "json", False):
        emit_result(_as_json_payload(), json_mode=True)
    else:
        emit_result(_TEXT, json_mode=False)
    return 0


def register(sub: argparse._SubParsersAction) -> None:
    p = sub.add_parser(
        "learn",
        help="Print a structured self-teaching prompt for agent consumers.",
    )
    p.add_argument("--json", action="store_true", help="Emit structured JSON.")
    p.set_defaults(func=cmd_learn)
