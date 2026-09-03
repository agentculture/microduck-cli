"""Motion gate: the arm101-style TTY / dry-run / --apply tri-state (CLI-only).

Any verb that would send a motion or power call to the robot (``init``,
``relax``, ``stop``, installing a community-published policy, …) must route
through :func:`consent` before it opens a socket or fires a call. The
tri-state mirrors ``../arm101-cli/arm101/cli/_commands/calibrate.py`` and
``set_baudrate.py`` (read, not copied — see ``microduck_cli/CLAUDE.md``
"``../arm101-cli`` — the hardware-safety patterns at this maturity"):

* a TTY without ``--apply`` -> :attr:`Consent.PROMPT` (confirm interactively);
* a non-TTY without ``--apply`` -> :attr:`Consent.DRY_RUN` (print a
  zero-side-effect plan, send nothing);
* ``--apply``, under any TTY state -> :attr:`Consent.APPLY` (proceed —
  non-interactive/agent mode).

No socket, subprocess or motion call is ever opened from this module —
it only classifies which of the three modes a caller is in and renders
the plan/prompt text. Handlers built on top of it raise
:class:`~microduck_cli.cli._errors.CliError`; this module never calls
``sys.exit``.

Upstream safety sentences
--------------------------
The four ``SAFETY_*`` constants are quoted **verbatim** from the
``pollen-robotics/microduck`` docs pinned in ``docs/upstream-pins.md``
(``sim-remote-io`` @ ``0cd676d6fbb6e90a762c84aa63abe7a02dbc9495``), with one
noted exception (:data:`SAFETY_COMMUNITY_POLICY`, see its comment). Re-quote
them by hand if the pin in ``docs/upstream-pins.md`` ever moves — do not
paraphrase.
"""

from __future__ import annotations

import sys
from enum import Enum
from typing import Callable

from microduck_cli.cli._output import emit_diagnostic

# ---------------------------------------------------------------------------
# Upstream safety sentences (verbatim quotes; do not paraphrase)
# ---------------------------------------------------------------------------

# Quoted verbatim from docs/robot/cheatsheet.md, "### Power to the joints
# (`robotd`)", at pollen-robotics/microduck commit
# 0cd676d6fbb6e90a762c84aa63abe7a02dbc9495 (pinned in docs/upstream-pins.md).
SAFETY_INIT = (
    "`init` powers the joints and ramps to the home pose over about two "
    "seconds — it moves every joint, so have the robot on its stand."
)

# Quoted verbatim from docs/robot/cheatsheet.md, "### Power to the joints
# (`robotd`)", at pollen-robotics/microduck commit
# 0cd676d6fbb6e90a762c84aa63abe7a02dbc9495 (pinned in docs/upstream-pins.md).
SAFETY_RELAX = (
    "`relax` cuts power and the robot collapses if nothing holds it, "
    "which is why it wants `--yes`."
)

# Quoted verbatim from docs/robot/duckctl.md, "## The console", at
# pollen-robotics/microduck commit 0cd676d6fbb6e90a762c84aa63abe7a02dbc9495
# (pinned in docs/upstream-pins.md).
SAFETY_STOP = (
    "`stop` zeroes the intents the page is sending. It is not an emergency "
    "stop — nothing in this system cuts servo power from a browser — "
    "and the button is a plain one for that reason."
)

# Quoted verbatim from docs/robot/cheatsheet.md, "#### Trying somebody
# else's" (under "### Policies and skills"), on the pollen-robotics/microduck
# *main* branch. NOTE: this section does not exist yet at the pinned
# sim-remote-io commit (0cd676d6fbb6e90a762c84aa63abe7a02dbc9495) — it was
# added upstream after that branch's fork point. Quoted from main because it
# is the only place this warning exists; re-pin and re-quote once
# sim-remote-io merges (see docs/upstream-pins.md "Risks").
SAFETY_COMMUNITY_POLICY = (
    "Nothing a stranger publishes is verified by anybody. What makes it "
    "safe to try is the joint clamps, the fall reflex and the shape gate "
    "— not the description. Have the robot on its stand the first time."
)

# hint: text for the --apply path (repo's `hint:` remediation convention —
# CliError.remediation, rendered by _output.emit_error as "hint: <text>").
# Use as (or fold into) the remediation of a CliError raised when a caller
# needs to be told how to move from DRY_RUN to APPLY.
HINT_APPLY = (
    "re-run with --apply to send these calls in non-interactive (agent) mode, "
    "or run this command in a terminal to confirm interactively."
)


class Consent(Enum):
    """The three motion-gate outcomes; see module docstring."""

    PROMPT = "prompt"
    DRY_RUN = "dry_run"
    APPLY = "apply"


def consent(apply: bool, stdin_isatty: bool | None = None) -> Consent:
    """Classify which gate mode a motion verb is running under.

    Parameters
    ----------
    apply:
        The verb's ``--apply`` flag.
    stdin_isatty:
        Injectable for tests; defaults to ``sys.stdin.isatty()``.

    Returns
    -------
    Consent
        * :attr:`Consent.APPLY` whenever *apply* is true, on a TTY or not —
          the operator (or agent) has already said "go".
        * :attr:`Consent.PROMPT` when *apply* is false and stdin is a TTY —
          confirm interactively.
        * :attr:`Consent.DRY_RUN` when *apply* is false and stdin is not a
          TTY — print a zero-side-effect plan and send nothing.

    This function performs no I/O and opens no socket; it only classifies.
    """
    if apply:
        return Consent.APPLY
    is_tty = sys.stdin.isatty() if stdin_isatty is None else stdin_isatty
    return Consent.PROMPT if is_tty else Consent.DRY_RUN


def confirm_on_tty(question: str, input_fn: Callable[[str], str] = input) -> bool:
    """Prompt *question* on stderr and return True only for an explicit yes.

    *question* is written to stderr via :func:`~microduck_cli.cli._output.emit_diagnostic`
    so it never lands on stdout (the output contract reserves stdout for
    results). *input_fn* reads the answer — injected in tests, defaults to
    the builtin :func:`input`. Only ``"y"`` or ``"yes"`` (case-insensitive,
    surrounding whitespace ignored) return ``True``; anything else —
    including EOF — returns ``False``. This helper never raises; a caller
    that wants to hard-fail on refusal raises its own
    :class:`~microduck_cli.cli._errors.CliError`.
    """
    emit_diagnostic(question)
    try:
        answer = input_fn("")
    except EOFError:
        return False
    return answer.strip().lower() in ("y", "yes")


# Verb -> the safety sentence that applies to it. Falls back to the
# community-policy sentence (the most general "protect yourself, not the
# robot" warning) for any verb this table doesn't name explicitly.
_SAFETY_BY_VERB: dict[str, str] = {
    "init": SAFETY_INIT,
    "relax": SAFETY_RELAX,
    "stop": SAFETY_STOP,
    "policy-install": SAFETY_COMMUNITY_POLICY,
}


def _safety_sentence(verb: str) -> str:
    """Return the applicable ``SAFETY_*`` sentence for *verb*."""
    return _SAFETY_BY_VERB.get(verb, SAFETY_COMMUNITY_POLICY)


#: Public alias of :func:`_safety_sentence` — ``cli/_commands/duck.py`` imports
#: this name rather than the private one.
safety_sentence = _safety_sentence


def render_dry_run(plan: dict) -> str:
    """Render a zero-side-effect dry-run plan for *plan* (never sends anything).

    *plan* is a plain dict describing what an ``--apply`` run would do:

    ``verb``
        The motion verb (``"init"``, ``"relax"``, ``"stop"``,
        ``"policy-install"``, …) — selects the safety sentence.
    ``target`` / ``duck``
        The duck being addressed (name).
    ``socket``
        The socket path that would be dialed, if resolved.
    ``calls``
        An iterable of the JSON-RPC calls that WOULD be sent (strings).
    ``apply_command``
        The exact command line that re-runs this with ``--apply``.

    All keys are optional; missing ones render as ``"?"`` or are omitted.
    This function performs no I/O itself — it only builds text.
    """
    verb = plan.get("verb", "?")
    target = plan.get("target") or plan.get("duck") or "?"
    socket = plan.get("socket")
    calls = list(plan.get("calls") or [])
    apply_command = plan.get("apply_command", "")

    lines = [
        f"## Dry-run plan: {verb}",
        "",
        f"- **target** : {target}",
    ]
    if socket:
        lines.append(f"- **socket** : {socket}")
    lines.append("")
    lines.append("### Calls that WOULD be sent")
    lines.append("")
    if calls:
        for call in calls:
            lines.append(f"  - {call}")
    else:
        lines.append("  - (none)")
    lines += [
        "",
        f"Safety: {_safety_sentence(verb)}",
        "",
        "No sockets opened, no calls sent; this is a read-only preview.",
    ]
    if apply_command:
        lines.append(f"Re-run to apply: {apply_command}")
    return "\n".join(lines)
