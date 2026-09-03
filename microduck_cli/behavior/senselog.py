"""Per-stage sensory logging — one grep-able stderr line per sensed event or drop.

Every layer that consumes a sense (the JSON-RPC client, the rule engine, the
engine's tick seam) emits ONE fixed-shape line for what it handled and, more
importantly, for what it *dropped* and why. A layer whose drops are invisible is
indistinguishable from one that silently no-ops, which is exactly the failure
mode this module exists to prevent.

Line shape (fixed, parseable, never reformatted)::

    [SENSE stage=<stage> source=<source> event=<event>] <detail>

A drop reuses the same shape with the *reason* in the ``event=`` slot, so a
named drop reason (``ipc-queue-full``, ``human-driving``, ``cooldown``,
``tick-driver-fault``) is greppable exactly like a handled event::

    [SENSE stage=ipc source=robotd event=ipc-queue-full] dropped reason=ipc-queue-full

**stdout is never written.** :func:`install_logging` attaches exactly one
``StreamHandler`` bound to ``sys.stderr`` to the ``microduck`` logger, so a verb
that streams JSONL on stdout stays byte-pure no matter how loud the sense layer
gets. The handler resolves ``sys.stderr`` at emit time rather than capturing it
at construction, which keeps a redirected stream (a test's ``capsys``, a shell
``2>``) working and lets :func:`install_logging` be genuinely idempotent: a
second call reuses the handler it already installed instead of doubling every
line.

Pure standard library; imports nothing else in the package.
"""

from __future__ import annotations

import logging
import sys

#: The process-wide logger root this package logs under. :func:`install_logging`
#: attaches its single handler here, so every ``microduck.*`` child logger is
#: covered by one installation.
ROOT_LOGGER_NAME = "microduck"

#: The dedicated sense logger. Named once here so a consumer can raise or silence
#: sense logging alone (``logging.getLogger("microduck.sense").setLevel(...)``)
#: without touching the rest of the CLI's logging.
LOGGER_NAME = "microduck.sense"

logger = logging.getLogger(LOGGER_NAME)

#: The one line format. Consumers grep it; changing it is a breaking change.
LINE_FORMAT = "[SENSE stage=%s source=%s event=%s] %s"

#: Marks the handler this module installed, so a second :func:`install_logging`
#: recognises its own work instead of adding a duplicate.
_HANDLER_MARKER = "_microduck_senselog_handler"


class _StderrHandler(logging.StreamHandler):
    """A ``StreamHandler`` that resolves ``sys.stderr`` on every emit.

    ``logging.StreamHandler(sys.stderr)`` binds the stream object that existed at
    construction time. That breaks two things this module cares about: a test
    that swaps ``sys.stderr`` after installation sees nothing, and a caller that
    reinstalls to "fix" it gets duplicate lines instead. Resolving the stream
    late makes one installed handler correct forever.
    """

    def __init__(self) -> None:
        super().__init__()
        setattr(self, _HANDLER_MARKER, True)

    @property
    def stream(self):  # type: ignore[override]
        return sys.stderr

    @stream.setter
    def stream(self, _value) -> None:
        """Ignore ``StreamHandler``'s own assignment; the stream is always stderr."""


def install_logging(level: int = logging.INFO) -> logging.Handler:
    """Attach exactly ONE stderr handler to the ``microduck`` logger; return it.

    Idempotent: calling this twice (two composition roots, a CLI verb that
    installs and an engine that installs again) reuses the handler already
    present and only updates the level, so no event is ever logged twice.
    Propagation to the root logger is disabled so a host application's own root
    handler cannot re-emit — possibly onto stdout — what we already wrote to
    stderr.
    """
    root = logging.getLogger(ROOT_LOGGER_NAME)
    root.setLevel(level)
    root.propagate = False
    for handler in root.handlers:
        if getattr(handler, _HANDLER_MARKER, False):
            handler.setLevel(level)
            return handler
    handler = _StderrHandler()
    handler.setLevel(level)
    handler.setFormatter(logging.Formatter("%(message)s"))
    root.addHandler(handler)
    return handler


def stage(stage: str, source: str, event: str, detail: str = "") -> None:
    """Emit one INFO line for a sense that a pipeline stage handled.

    :param stage: the pipeline stage (``"ipc"``, ``"rule"``, ``"tick"``).
    :param source: where the sense came from (``"robotd"``, ``"tof"``, ``"pad"``).
    :param event: what happened (``"state"``, ``"fired"``, a rule id).
    :param detail: free-form human detail; may be empty.
    """
    logger.info(LINE_FORMAT, stage, source, event, detail)


def drop(stage: str, source: str, reason: str, detail: str = "") -> None:
    """Emit one INFO line for a sense that was deliberately DROPPED.

    The *reason* lands in the ``event=`` slot (so ``grep 'event=cooldown'`` finds
    every cooldown drop) and is repeated in the detail as ``dropped reason=…``,
    which is the shape a human scanning the log reads. ``detail`` is appended
    after a colon when given.

    :param stage: the pipeline stage where the drop happened.
    :param source: where the dropped sense came from.
    :param reason: the NAMED reason (``"ipc-queue-full"``, ``"human-driving"``).
    :param detail: optional extra context.
    """
    text = f"dropped reason={reason}"
    if detail:
        text = f"{text}: {detail}"
    logger.info(LINE_FORMAT, stage, source, reason, text)
