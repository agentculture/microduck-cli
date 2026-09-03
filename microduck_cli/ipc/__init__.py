"""IPC layer: the wire contract robotd and its peers speak, and the client that speaks it.

:mod:`microduck_cli.ipc.proto` is the transcribed protocol table -- constants only, no
I/O. :mod:`microduck_cli.ipc.client` is the transport: a threaded JSON-RPC client over a
unix socket, with a bounded write queue, correlated requests, peek slots for the pushed
notification streams, and named drops on the ``microduck.sense`` logger.

Nothing is re-exported here on purpose, so importing the package stays free of the socket
module for a caller that only wants the table.
"""

from __future__ import annotations

__all__: list[str] = []
