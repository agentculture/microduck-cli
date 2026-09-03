"""``microduck-cli duck`` — noun group for operating the duck.

Scaffold: only ``overview`` is registered today. Mirrors
:mod:`microduck_cli.cli._commands.cli` — the nested subparsers are built with
``parser_class=type(p)`` so a parse error under this noun keeps the structured
``error:``/``hint:`` contract instead of argparse's default exit 2, and a bare
``microduck-cli duck`` prints this noun's overview.

Verb summaries live in :mod:`microduck_cli.explain.duck` (``VERBS``), which the
global ``overview``/``learn`` surfaces read too — so adding a verb here means
editing this file, ``explain/duck.py`` and ``tests/test_duck.py``, and nothing
else.
"""

from __future__ import annotations

import argparse

from microduck_cli.cli._commands.overview import emit_overview
from microduck_cli.explain.duck import VERBS

_SUBJECT = "microduck-cli duck"
_PURPOSE = (
    "Operate the duck directly, in robotctl's words (init, enable, relax, move, look, do, stop)."
)
_STATUS = "scaffold — only 'overview' is implemented; action verbs land with the duck task"


def duck_sections() -> list[dict[str, object]]:
    """Sections describing the ``duck`` noun (used by ``duck overview``)."""
    return [
        {"title": "Purpose", "items": [_PURPOSE]},
        {"title": "Verbs", "items": list(VERBS)},
        {"title": "Status", "items": [_STATUS]},
    ]


def cmd_duck_overview(args: argparse.Namespace) -> int:
    emit_overview(
        _SUBJECT,
        duck_sections(),
        json_mode=bool(getattr(args, "json", False)),
    )
    return 0


def _no_verb(args: argparse.Namespace) -> int:
    # `microduck-cli duck` with no sub-verb prints the noun's overview.
    return cmd_duck_overview(args)


def register(sub: argparse._SubParsersAction) -> None:
    p = sub.add_parser(
        "duck",
        help="Operate the duck (see 'microduck-cli duck overview').",
    )
    p.add_argument("--json", action="store_true", help="Emit structured JSON.")
    p.set_defaults(func=_no_verb, json=False)
    # `p` is a _CliArgumentParser (the top-level subparsers were built with that
    # parser_class); propagate it so `duck <verb>` parse errors route through the
    # structured error contract instead of argparse's default stderr/exit 2.
    noun_sub = p.add_subparsers(dest="duck_command", parser_class=type(p))
    ov = noun_sub.add_parser("overview", help="Describe the duck noun.")
    ov.add_argument(
        "target",
        nargs="?",
        help="Ignored — overview always describes this noun. Accepted so a stray "
        "path argument never hard-fails.",
    )
    ov.add_argument("--json", action="store_true", help="Emit structured JSON.")
    ov.set_defaults(func=cmd_duck_overview)
