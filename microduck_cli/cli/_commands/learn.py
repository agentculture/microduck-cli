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


#: The canonical operator skill this CLI teaches an agent to recreate.
SKILL_NAME = "operate-microduck"

#: Where the canonical copy ships, inside this repo.
SKILL_PATH = ".claude/skills/operate-microduck/SKILL.md"

#: The section titles a recreated ``operate-microduck`` SKILL.md must carry.
SKILL_SECTIONS: list[str] = [
    "When to use",
    "Prerequisites",
    "Open the simulation",
    "Operate",
    "Watch it",
    "Close the simulation",
    "Hard rules",
    "Provenance",
]

_SKILL_SECTION_LINES = "\n".join(f"    - {section}" for section in SKILL_SECTIONS)

_SKILL_TEXT = f"""\
Authoring the operator skill ({SKILL_NAME})
-------------------------------------------
This CLI is driven end to end by one operator skill, `{SKILL_NAME}`: open the
MicroDuck simulation, operate the duck (stand, enable, skills, look, move,
rules engine, monitor/record), and close it down again. Recreate it in your own
runtime so you can drive the duck the same way everywhere.

Consent — read before creating anything:
  - This is an instruction for you, the assisting agent, not a CLI side effect.
    microduck-cli never writes skill files; you do.
  - Create the skill only when the user asked for it. If you reached this text
    by default (a bare `microduck-cli learn`), ask first.
  - Never overwrite an existing skill of the same name — if
    <skills>/{SKILL_NAME}/ already exists, stop and surface the conflict
    instead of clobbering it.

Recipe:
  One directory in your runtime's skills folder (Claude Code:
  .claude/skills/{SKILL_NAME}/) holding a single file:

    <skills>/{SKILL_NAME}/SKILL.md   frontmatter + the operating doc

  Method-only — no scripts/ directory; the skill invokes this CLI directly.
  SKILL.md opens with YAML frontmatter:

    name: {SKILL_NAME}
    description: >
      one paragraph — what it does (opens the sim, operates the duck, closes
      it), when to use it, and that it is first-party to microduck-cli.
    type: command    # REQUIRED by the culture/colleague backend, whose
                     # core.skill_loader SILENTLY SKIPS a SKILL.md without it.
                     # Harmless on claude-code.

  Body sections, in this order:
{_SKILL_SECTION_LINES}

Canonical copy:
  The skill ships in this repo at {SKILL_PATH}.
  An agent in another runtime copies that file (cite-don't-import — fixes go
  upstream to microduck-cli, never patched locally in a consumer) or writes it
  from the recipe above.
"""


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

{_SKILL_TEXT}"""


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
        "skills": {
            SKILL_NAME: {
                "title": f"Authoring the operator skill ({SKILL_NAME})",
                "summary": (
                    "Opens the MicroDuck simulation, operates the duck (stand, enable, "
                    "skills, look, move, rules engine, monitor/record), and closes it "
                    "down again."
                ),
                "first_party": True,
                "origin": "microduck-cli",
                "canonical_path": SKILL_PATH,
                "consent": [
                    "This is an instruction for the assisting agent, not a CLI side "
                    "effect: microduck-cli never writes skill files.",
                    "Create the skill only when the user asked for it; if you reached "
                    "this by a bare `microduck-cli learn`, ask first.",
                    f"Never overwrite an existing skill named {SKILL_NAME} — surface "
                    "the conflict instead of clobbering it.",
                ],
                "recipe": {
                    "directory": f"<skills>/{SKILL_NAME}/",
                    "files": [f"<skills>/{SKILL_NAME}/SKILL.md"],
                    "scripts": False,
                    "frontmatter": {
                        "name": SKILL_NAME,
                        "description": (
                            "one paragraph — what it does, when to use it, and that it "
                            "is first-party to microduck-cli"
                        ),
                        "type": "command",
                    },
                    "type_command_required": (
                        "REQUIRED by the culture/colleague backend: core.skill_loader "
                        "silently skips a SKILL.md without `type: command`."
                    ),
                    "sections": list(SKILL_SECTIONS),
                },
                "cite_dont_import": (
                    f"The canonical copy ships in this repo at {SKILL_PATH}. An agent in "
                    "another runtime copies it, or writes it from this recipe; fixes go "
                    "upstream to microduck-cli, never patched locally in a consumer."
                ),
            }
        },
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
