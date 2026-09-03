"""IPC protocol layer: the wire contract robotd and its peers speak.

Currently holds only :mod:`microduck_cli.ipc.proto`, the transcribed protocol table.
No I/O, no sockets, no transport lives here yet -- see docs/plans for later tasks.
"""

from __future__ import annotations

__all__: list[str] = []
