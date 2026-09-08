"""Serve a run directory as a local static site — ``index.html`` and friends.

Stdlib only: :class:`http.server.ThreadingHTTPServer` with a
:class:`~http.server.SimpleHTTPRequestHandler` rooted at the run dir. Bound to
the loopback interface by default; the caller decides how long to keep it up
(:meth:`ServeHandle.stop` shuts it down).
"""

from __future__ import annotations

import functools
import http.server
import threading
import webbrowser
from dataclasses import dataclass

from microduck_cli.trace.events import RunDir

__all__ = ["ServeHandle", "serve"]


class _QuietHandler(http.server.SimpleHTTPRequestHandler):
    """The stdlib handler with its per-request stderr chatter silenced."""

    def log_message(self, format: str, *args: object) -> None:  # noqa: A002 - stdlib name
        return None


@dataclass
class ServeHandle:
    """A running server: its URL, the server object and the thread driving it."""

    url: str
    server: http.server.ThreadingHTTPServer
    thread: threading.Thread

    def stop(self) -> None:
        """Shut the server down and join its thread (bounded wait)."""
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)

    def __str__(self) -> str:
        return self.url


def serve(
    run_dir: RunDir, *, port: int = 0, host: str = "127.0.0.1", open_browser: bool = False
) -> ServeHandle:
    """Serve *run_dir* over HTTP in a daemon thread and return its handle.

    ``port=0`` lets the OS pick a free port; the chosen one is in the returned
    URL, which points at ``index.html``. ``open_browser=True`` asks the default
    browser to open that URL (best effort).
    """
    handler = functools.partial(_QuietHandler, directory=str(run_dir.path))
    server = http.server.ThreadingHTTPServer((host, port), handler)
    server.daemon_threads = True
    bound_host, bound_port = server.server_address[:2]
    url = f"http://{bound_host}:{bound_port}/index.html"
    thread = threading.Thread(target=server.serve_forever, name="trace-serve", daemon=True)
    thread.start()
    if open_browser:
        webbrowser.open(url)
    return ServeHandle(url=url, server=server, thread=thread)
