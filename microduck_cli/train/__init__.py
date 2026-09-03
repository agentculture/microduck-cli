"""Train lane: argv builders for the upstream `microduck_rl` tooling.

Everything here builds `list[str]` argv for a subprocess `uv run ...`
invocation; nothing in this package imports `microduck_rl`, `mjlab_microduck`,
`mjlab`, `torch` or `warp`. See :mod:`microduck_cli.train.lane` for the
builders and the smoke-gate, and :mod:`microduck_cli.train.artifacts` for the
append-only ledger of what a run produced.
"""
