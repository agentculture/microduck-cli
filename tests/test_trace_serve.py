"""Tests for microduck_cli/trace/serve.py — a stdlib static server over a run dir."""

from __future__ import annotations

import urllib.request
from pathlib import Path

import pytest

from microduck_cli.cli._errors import EXIT_USER_ERROR, CliError
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


def test_serve_refuses_a_non_loopback_host(tmp_path: Path) -> None:
    rd = RunDir(tmp_path / "run")
    rd.ensure()
    with pytest.raises(CliError) as excinfo:
        serve(rd, port=0, host="0.0.0.0")  # nosec B104 - the point of the test
    assert excinfo.value.code == EXIT_USER_ERROR
    assert "0.0.0.0" in excinfo.value.message
    assert excinfo.value.remediation


def test_serve_accepts_loopback_aliases(tmp_path: Path) -> None:
    rd = RunDir(tmp_path / "run")
    rd.ensure()
    handle = serve(rd, port=0, host="localhost")
    try:
        assert handle.url.startswith("http://")
        assert handle.url.endswith("/index.html")
    finally:
        handle.stop()


def test_serve_binds_remote_host_only_when_opted_in(tmp_path: Path) -> None:
    rd = RunDir(tmp_path / "run")
    rd.ensure()
    handle = serve(rd, port=0, host="0.0.0.0", allow_remote=True)  # nosec B104 - explicit opt-in
    try:
        assert handle.server.server_address[0] == "0.0.0.0"  # nosec B104 - assertion, not a bind
    finally:
        handle.stop()


def test_serve_str_is_url(tmp_path: Path) -> None:
    rd = RunDir(tmp_path / "run")
    rd.ensure()
    handle = serve(rd, port=0)
    try:
        assert str(handle) == handle.url
    finally:
        handle.stop()
