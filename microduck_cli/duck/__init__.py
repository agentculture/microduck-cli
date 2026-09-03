"""Duck-domain building blocks (CLI-only surface; no hardware/network I/O).

This package holds pieces the ``duck`` noun (and any other motion-adjacent
verb) composes from — starting with the motion gate in :mod:`microduck_cli.duck.gate`.
See ``microduck_cli/CLAUDE.md`` for why this is CLI-only today: the runtime
tick lives upstream in ``neurosymbolic-system``, which does not ship modules
yet.
"""

from __future__ import annotations
