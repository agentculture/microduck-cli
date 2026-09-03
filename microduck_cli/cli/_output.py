"""stdout / stderr helpers with a strict split (stable-contract).

Rule: **results go to stdout, diagnostics and errors go to stderr.** Agents
parsing output can rely on this invariant. JSON mode routes structured
payloads to the same streams — never mixes them.
"""

from __future__ import annotations

import json
import sys
from typing import Any, TextIO

from microduck_cli.cli._errors import CliError

#: The parser's ``prog`` — and so the ONE spelling every command line this CLI
#: generates for a human or an agent must use: a dry-run's ``apply_command``, a
#: remediation that says "run …", a help string naming a sibling verb. Both console
#: scripts (``microduck`` and ``microduck-cli``) run the same parser, so a generated
#: line is copy-pasteable either way; what a reader must never see is two different
#: names for the same CLI in one session.
PROG = "microduck-cli"


def emit_result(data: Any, *, json_mode: bool, stream: TextIO | None = None) -> None:
    """Write a command result to stdout (or ``stream``)."""
    s = stream if stream is not None else sys.stdout
    if json_mode:
        json.dump(data, s, ensure_ascii=False)
        s.write("\n")
        return
    text = data if isinstance(data, str) else str(data)
    s.write(text)
    if not text.endswith("\n"):
        s.write("\n")


def emit_error(err: CliError, *, json_mode: bool, stream: TextIO | None = None) -> None:
    """Write a :class:`CliError` to stderr.

    Text mode renders as two lines when a remediation is present::

        error: <message>
        hint: <remediation>

    The ``hint:`` prefix is required by the agent-first error rubric.
    """
    s = stream if stream is not None else sys.stderr
    if json_mode:
        json.dump(err.to_dict(), s, ensure_ascii=False)
        s.write("\n")
        return
    s.write(f"error: {err.message}\n")
    if err.remediation:
        s.write(f"hint: {err.remediation}\n")


def emit_diagnostic(message: str, *, stream: TextIO | None = None) -> None:
    """Write a human diagnostic (progress, summary) to stderr."""
    s = stream if stream is not None else sys.stderr
    s.write(message if message.endswith("\n") else message + "\n")
