"""``microduck-cli rules`` — noun group for the rules layer.

Scaffold: only ``overview`` is registered today. Mirrors
:mod:`microduck_cli.cli._commands.cli` — the nested subparsers are built with
``parser_class=type(p)`` so a parse error under this noun keeps the structured
``error:``/``hint:`` contract instead of argparse's default exit 2, and a bare
``microduck-cli rules`` prints this noun's overview.

Verb summaries live in :mod:`microduck_cli.explain.rules` (``VERBS``), which the
global ``overview``/``learn`` surfaces read too — so adding a verb here means
editing this file, ``explain/rules.py`` and ``tests/test_rules.py``, and nothing
else.
"""

from __future__ import annotations

import argparse

from microduck_cli.cli._commands.overview import emit_overview
from microduck_cli.explain.rules import VERBS

_SUBJECT = "microduck-cli rules"
_PURPOSE = (
    "The data-only rules layer (events -> rules -> actions) and the engine that evaluates it."
)
_STATUS = "scaffold — only 'overview' is implemented; action verbs land with the rules task"


def rules_sections() -> list[dict[str, object]]:
    """Sections describing the ``rules`` noun (used by ``rules overview``)."""
    return [
        {"title": "Purpose", "items": [_PURPOSE]},
        {"title": "Verbs", "items": list(VERBS)},
        {"title": "Status", "items": [_STATUS]},
    ]


def cmd_rules_overview(args: argparse.Namespace) -> int:
    emit_overview(
        _SUBJECT,
        rules_sections(),
        json_mode=bool(getattr(args, "json", False)),
    )
    return 0


def _no_verb(args: argparse.Namespace) -> int:
    # `microduck-cli rules` with no sub-verb prints the noun's overview.
    return cmd_rules_overview(args)


def register(sub: argparse._SubParsersAction) -> None:
    p = sub.add_parser(
        "rules",
        help="The data-only rules layer (see 'microduck-cli rules overview').",
    )
    p.add_argument("--json", action="store_true", help="Emit structured JSON.")
    p.set_defaults(func=_no_verb, json=False)
    # `p` is a _CliArgumentParser (the top-level subparsers were built with that
    # parser_class); propagate it so `rules <verb>` parse errors route through the
    # structured error contract instead of argparse's default stderr/exit 2.
    noun_sub = p.add_subparsers(dest="rules_command", parser_class=type(p))
    ov = noun_sub.add_parser("overview", help="Describe the rules noun.")
    ov.add_argument(
        "target",
        nargs="?",
        help="Ignored — overview always describes this noun. Accepted so a stray "
        "path argument never hard-fails.",
    )
    ov.add_argument("--json", action="store_true", help="Emit structured JSON.")
    ov.set_defaults(func=cmd_rules_overview)
