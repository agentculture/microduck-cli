"""Tests for microduck_cli/trace/serve.py — a stdlib static server over a run dir."""

from __future__ import annotations

import urllib.request
from pathlib import Path

from microduck_cli.trace.events import RunDir
from microduck_cli.trace.serve import serve


def test_serve_returns_fetchable_url_and_stops(tmp_path: Path) -> None:
    rd = RunDir(tmp_path / "run")
    rd.ensure()
    (rd.path / "index.html").write_text("<title>stub</title>", encoding="utf-8")
    handle = serve(rd, port=0)
    try:
        assert handle.url.startswith("http://127.0.0.1:")
        assert handle.url.endswith("/index.html")
        with urllib.request.urlopen(handle.url, timeout=5) as resp:  # nosec B310 - loopback
            assert resp.status == 200
            assert resp.read() == b"<title>stub</title>"
    finally:
        handle.stop()
    assert not handle.thread.is_alive()


def test_serve_str_is_url(tmp_path: Path) -> None:
    rd = RunDir(tmp_path / "run")
    rd.ensure()
    handle = serve(rd, port=0)
    try:
        assert str(handle) == handle.url
    finally:
        handle.stop()
