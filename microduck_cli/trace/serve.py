"""Serve a run directory as a local static site — ``index.html`` and friends.

Stdlib only: :class:`http.server.ThreadingHTTPServer` with a
:class:`~http.server.SimpleHTTPRequestHandler` rooted at the run dir. Bound to
the loopback interface — and *only* there unless the caller passes
``allow_remote=True``, since the server has no auth and a run dir is a whole
session's output; the caller decides how long to keep it up
(:meth:`ServeHandle.stop` shuts it down).
"""

from __future__ import annotations

import functools
import http.server
import threading
import webbrowser
from dataclasses import dataclass

from microduck_cli.cli._errors import EXIT_USER_ERROR, CliError
from microduck_cli.trace.events import RunDir

__all__ = ["ServeHandle", "serve"]

# Plain HTTP is deliberate and safe *because* the bind is loopback-only: nothing
# leaves the box, so TLS here would be theatre — it would need a certificate no
# browser can verify for 127.0.0.1 and would buy no confidentiality. The scheme
# lives in this constant (rather than inline in the URL) so the choice is stated
# once, next to the loopback guard that makes it true.
_SCHEME = "http"  # NOSONAR - loopback-only static server; TLS would be theatre and needs a cert
#: Hosts that keep the server on this machine. Anything else needs ``allow_remote``.
_LOOPBACK_HOSTS = frozenset({"127.0.0.1", "::1", "0:0:0:0:0:0:0:1", "localhost", ""})


def _check_host(host: str, allow_remote: bool) -> None:
    """Refuse a bind that would expose the run dir off-box unless asked to."""
    if allow_remote or host.strip().lower() in _LOOPBACK_HOSTS:
        return
    raise CliError(
        EXIT_USER_ERROR,
        f"refusing to serve on {host}: the trace server is loopback-only",
        "bind 127.0.0.1 (the default), or pass allow_remote=True if you really "
        "mean to expose the run dir over the network",
    )


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
    run_dir: RunDir,
    *,
    port: int = 0,
    host: str = "127.0.0.1",
    open_browser: bool = False,
    allow_remote: bool = False,
) -> ServeHandle:
    """Serve *run_dir* over HTTP in a daemon thread and return its handle.

    ``port=0`` lets the OS pick a free port; the chosen one is in the returned
    URL, which points at ``index.html``. ``open_browser=True`` asks the default
    browser to open that URL (best effort).

    *host* must be a loopback address (``127.0.0.1``, ``::1``, ``localhost``):
    a run dir holds a whole session's output and this server has no auth, so a
    non-loopback bind raises :class:`~microduck_cli.cli._errors.CliError`
    (:data:`~microduck_cli.cli._errors.EXIT_USER_ERROR`) unless the caller
    passes ``allow_remote=True`` to say it means it.
    """
    _check_host(host, allow_remote)
    handler = functools.partial(_QuietHandler, directory=str(run_dir.path))
    server = http.server.ThreadingHTTPServer((host, port), handler)
    server.daemon_threads = True
    bound_host, bound_port = server.server_address[:2]
    shown = f"[{bound_host}]" if ":" in str(bound_host) else bound_host
    url = f"{_SCHEME}://{shown}:{bound_port}/index.html"
    thread = threading.Thread(target=server.serve_forever, name="trace-serve", daemon=True)
    thread.start()
    if open_browser:
        webbrowser.open(url)
    return ServeHandle(url=url, server=server, thread=thread)
