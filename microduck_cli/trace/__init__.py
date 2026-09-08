"""microduck_cli.trace — the run-trace tool.

One command re-runs an operator or tutorial session, traces every command,
daemon log line and viewer frame with wall-clock stamps, and regenerates the
MicroDuck Run Trace page (a standalone local HTML file and a Claude Artifact
fragment) from what it recorded. See
``docs/plans/2026-09-08-run-trace-tool.md`` for the plan and
``docs/plans/2026-09-08-run-trace-tool.contract.md`` for the module contract
every sibling module in this package builds to.

This package is **stdlib-only**. A module here may import
:mod:`microduck_cli.cli._errors` for :class:`~microduck_cli.cli._errors.CliError`
and the exit-code constants, and nothing else from :mod:`microduck_cli.cli` —
and no third-party dependency at all, so ``pyproject.toml``'s
``dependencies = []`` stays empty because of this package too.
"""

from __future__ import annotations
