"""Tests for :mod:`microduck_cli.trace.capture` — display discovery and frame capture."""

from __future__ import annotations

import ast
import struct
from pathlib import Path

import pytest

from microduck_cli.trace.capture import (
    DisplayEnv,
    ShotOutcome,
    capture_frame,
    discover_display,
    pause_autolock,
)


def _png_bytes(width: int, height: int) -> bytes:
    """A minimal (not-really-valid-past-the-header) PNG with a real IHDR."""
    ihdr = struct.pack(">II", width, height) + b"\x08\x06\x00\x00\x00"
    return b"\x89PNG\r\n\x1a\n" + b"\x00\x00\x00\rIHDR" + ihdr + b"\x00\x00\x00\x00"


class _CompletedProcess:
    def __init__(self, argv, returncode=0, stdout="", stderr=""):
        self.argv = argv
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


class FakeRunner:
    """Records every argv it is called with and fakes a handful of tools."""

    def __init__(self, *, xwininfo_line=None, xdpyinfo_dims=None, gsettings=None, raise_on=None):
        self.calls: list[list[str]] = []
        self._xwininfo_line = xwininfo_line
        self._xdpyinfo_dims = xdpyinfo_dims
        self._gsettings = dict(gsettings or {})
        self._raise_on = raise_on or ()

    def __call__(self, argv, **kwargs):
        self.calls.append(list(argv))
        tool = argv[0]
        if tool in self._raise_on:
            raise OSError(f"{tool} not found")

        if tool == "xwininfo":
            stdout = self._xwininfo_line or ""
            return _CompletedProcess(argv, 0, stdout=stdout)
        if tool == "xdpyinfo":
            if self._xdpyinfo_dims is None:
                return _CompletedProcess(argv, 1, stderr="no display")
            w, h = self._xdpyinfo_dims
            return _CompletedProcess(argv, 0, stdout=f"  dimensions:    {w}x{h} pixels (some mm)")
        if tool == "gnome-screenshot":
            path = argv[argv.index("-f") + 1]
            Path(path).write_bytes(_png_bytes(640, 480))
            return _CompletedProcess(argv, 0)
        if tool in ("xdotool", "wmctrl"):
            return _CompletedProcess(argv, 0)
        if tool == "gsettings" and argv[1] == "get":
            schema, key = argv[2], argv[3]
            value = self._gsettings.get((schema, key))
            if value is None:
                return _CompletedProcess(argv, 1, stderr="no such key")
            return _CompletedProcess(argv, 0, stdout=value + "\n")
        if tool == "gsettings" and argv[1] == "set":
            schema, key, value = argv[2], argv[3], argv[4]
            self._gsettings[(schema, key)] = value
            return _CompletedProcess(argv, 0)
        raise AssertionError(f"unexpected tool: {tool}")


class FakeRunDir:
    def __init__(self, tmp_path: Path):
        self.path = str(tmp_path)
        self.shots = str(tmp_path / "shots")


_XWININFO_LINE = '     0x1600041 "MuJoCo : scene": ("python3" "Python3")  1200x900+0+0  +32+59\n'


# --- capture_frame ----------------------------------------------------------


def test_headless_outcome_without_runner_calls(tmp_path: Path) -> None:
    runner = FakeRunner()
    outcome = capture_frame(FakeRunDir(tmp_path), "shot1", env=None, runner=runner)

    assert outcome == ShotOutcome(ok=False, reason="headless")
    assert runner.calls == []


def test_geometry_recorded_when_no_focus_tool(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("microduck_cli.trace.capture.shutil.which", lambda _name: None)
    runner = FakeRunner(xwininfo_line=_XWININFO_LINE, xdpyinfo_dims=(1920, 1080))
    env = DisplayEnv(display=":0", xauthority="/home/x/.Xauthority")

    outcome = capture_frame(FakeRunDir(tmp_path), "shot1", env=env, runner=runner)

    assert outcome.ok is True
    assert outcome.window is False
    assert outcome.file == "shots/shot1.png"
    assert outcome.geometry == {"x": 32, "y": 59, "w": 1200, "h": 900, "screen": [1920, 1080]}
    assert outcome.size == [640, 480]
    tools_called = [call[0] for call in runner.calls]
    assert "gnome-screenshot" in tools_called
    assert "-w" not in [
        arg for call in runner.calls for arg in call if call[0] == "gnome-screenshot"
    ]


def test_window_true_when_which_finds_xdotool(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "microduck_cli.trace.capture.shutil.which",
        lambda name: "/usr/bin/xdotool" if name == "xdotool" else None,
    )
    runner = FakeRunner(xwininfo_line=_XWININFO_LINE)
    env = DisplayEnv(display=":0", xauthority="/home/x/.Xauthority", dbus="unix:path=/run/bus")

    outcome = capture_frame(FakeRunDir(tmp_path), "shot2", env=env, runner=runner)

    assert outcome.ok is True
    assert outcome.window is True
    assert outcome.geometry is None
    assert outcome.file == "shots/shot2.png"
    screenshot_calls = [c for c in runner.calls if c[0] == "gnome-screenshot"]
    assert screenshot_calls and "-w" in screenshot_calls[0]
    focus_calls = [c for c in runner.calls if c[0] == "xdotool"]
    assert focus_calls


def test_runner_exception_yields_ok_false_with_reason_no_raise(tmp_path: Path) -> None:
    runner = FakeRunner(raise_on={"gnome-screenshot"})
    env = DisplayEnv(display=":0", xauthority="/home/x/.Xauthority")

    outcome = capture_frame(FakeRunDir(tmp_path), "shot3", env=env, runner=runner)

    assert outcome.ok is False
    assert outcome.reason
    assert "gnome-screenshot" in outcome.reason


def test_runner_nonzero_returncode_reported(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("microduck_cli.trace.capture.shutil.which", lambda _name: None)

    class FailingRunner(FakeRunner):
        def __call__(self, argv, **kwargs):
            if argv[0] == "gnome-screenshot":
                return _CompletedProcess(argv, 1, stderr="boom")
            return super().__call__(argv, **kwargs)

    env = DisplayEnv(display=":0", xauthority="/home/x/.Xauthority")
    outcome = capture_frame(FakeRunDir(tmp_path), "shot4", env=env, runner=FailingRunner())

    assert outcome.ok is False
    assert outcome.reason == "boom"


def test_png_size_parsed_from_ihdr(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("microduck_cli.trace.capture.shutil.which", lambda _name: None)
    runner = FakeRunner()
    env = DisplayEnv(display=":0", xauthority="/home/x/.Xauthority")

    outcome = capture_frame(FakeRunDir(tmp_path), "shot5", env=env, runner=runner)

    assert outcome.size == [640, 480]


# --- discover_display --------------------------------------------------------


def test_discover_display_prefers_current_environ() -> None:
    environ = {
        "DISPLAY": ":1",
        "XAUTHORITY": "/home/y/.Xauthority",
        "DBUS_SESSION_BUS_ADDRESS": "unix:path=/run/bus",
    }
    result = discover_display(
        environ=environ, proc_environs=lambda: (_ for _ in ()).throw(AssertionError)
    )

    assert result == DisplayEnv(
        display=":1", xauthority="/home/y/.Xauthority", dbus="unix:path=/run/bus"
    )


def test_discover_display_from_fake_proc_environs() -> None:
    def fake_proc_environs():
        return [
            {"SOME": "OTHER"},
            {"DISPLAY": ":0", "XAUTHORITY": "/home/z/.Xauthority"},
        ]

    result = discover_display(environ={}, proc_environs=fake_proc_environs)

    assert result == DisplayEnv(display=":0", xauthority="/home/z/.Xauthority", dbus=None)


def test_discover_display_returns_none_when_nothing_found() -> None:
    result = discover_display(environ={}, proc_environs=lambda: [])
    assert result is None


def test_discover_display_never_raises_on_proc_environs_failure() -> None:
    def boom():
        raise OSError("no /proc here")

    result = discover_display(environ={}, proc_environs=boom)
    assert result is None


# --- pause_autolock ----------------------------------------------------------


def test_pause_autolock_restores_both_values_even_on_raise() -> None:
    runner = FakeRunner(
        gsettings={
            ("org.gnome.desktop.session", "idle-delay"): "uint32 300",
            ("org.gnome.desktop.screensaver", "lock-enabled"): "true",
        }
    )

    with pytest.raises(ValueError):
        with pause_autolock(runner=runner) as original:
            assert original == {"idle-delay": "uint32 300", "lock-enabled": "true"}
            assert runner._gsettings[("org.gnome.desktop.session", "idle-delay")] == "0"
            assert runner._gsettings[("org.gnome.desktop.screensaver", "lock-enabled")] == "false"
            raise ValueError("boom")

    assert runner._gsettings[("org.gnome.desktop.session", "idle-delay")] == "uint32 300"
    assert runner._gsettings[("org.gnome.desktop.screensaver", "lock-enabled")] == "true"


def test_pause_autolock_yields_empty_and_changes_nothing_on_read_failure() -> None:
    runner = FakeRunner(gsettings={})

    with pause_autolock(runner=runner) as original:
        assert original == {}

    set_calls = [c for c in runner.calls if c[0] == "gsettings" and c[1] == "set"]
    assert set_calls == []


# --- module hygiene -----------------------------------------------------------


def test_no_pillow_or_image_import() -> None:
    source = Path("microduck_cli/trace/capture.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                assert "PIL" not in alias.name and "Image" not in alias.name
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            assert "PIL" not in module and "Image" not in module
    assert "PIL" not in source
    assert "Image" not in source
