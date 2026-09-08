"""Display-owner discovery and viewer-frame capture for the run-trace tool.

The MuJoCo window lives on the box's graphical session, not in the process
running this CLI. :func:`discover_display` finds the display owner's
``DISPLAY``/``XAUTHORITY``/``DBUS_SESSION_BUS_ADDRESS`` (the same recipe the
``operate-microduck`` skill's "Watch it" section documents by hand), and
:func:`capture_frame` uses them to take one screenshot of the viewer window
without ever hard-coding a session.

Stdlib only. No image library: PNG dimensions are read directly from the
IHDR chunk (``_read_png_size``) rather than decoding the image.

Everything here is seam-injected and never raises: a missing display, a
missing tool, or a failing subprocess all degrade to a reported outcome
rather than an exception, because a screenshot is inherently best-effort.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess  # nosec B404 - list-argv subprocess calls only, see gate below
from collections.abc import Iterable, Mapping
from contextlib import contextmanager
from dataclasses import dataclass

#: The MuJoCo viewer window's title, matched verbatim in ``xwininfo``/focus tools.
_WINDOW_TITLE = "MuJoCo : scene"

# xwininfo -tree lines look like:
#   0x1600041 "MuJoCo : scene": ("python3" "Python3")  1200x900+0+0  +32+59
# The first WxH+x+y pair is relative to the parent; the second (+AX+AY) is the
# absolute screen position the contract asks us to record.
_XWININFO_RE = re.compile(r"(\d+)x(\d+)\+-?\d+\+-?\d+\s+\+(-?\d+)\+(-?\d+)")

# xdpyinfo prints a line such as "  dimensions:    1920x1080 pixels (...)".
_XDPYINFO_RE = re.compile(r"dimensions:\s+(\d+)x(\d+)\s+pixels")

_PNG_MAGIC = b"\x89PNG\r\n\x1a\n"

_GSETTINGS_KEYS: dict[str, tuple[str, str]] = {
    "idle-delay": ("org.gnome.desktop.session", "idle-delay"),
    "lock-enabled": ("org.gnome.desktop.screensaver", "lock-enabled"),
}
_AUTOLOCK_OFF: dict[str, str] = {
    "idle-delay": "0",
    "lock-enabled": "false",
}


@dataclass(frozen=True)
class DisplayEnv:
    """The display owner's session environment needed to reach its X display."""

    display: str
    xauthority: str
    dbus: str | None = None

    def as_env(self, base: Mapping[str, str]) -> dict[str, str]:
        """Overlay ``DISPLAY``/``XAUTHORITY``/``DBUS_SESSION_BUS_ADDRESS`` onto ``base``."""
        merged = dict(base)
        merged["DISPLAY"] = self.display
        merged["XAUTHORITY"] = self.xauthority
        if self.dbus:
            merged["DBUS_SESSION_BUS_ADDRESS"] = self.dbus
        return merged


def _read_comm(pid: str) -> str | None:
    try:
        with open(f"/proc/{pid}/comm", encoding="utf-8") as handle:
            return handle.read().strip()
    except OSError:
        return None


def _read_proc_environ(pid: str) -> dict[str, str] | None:
    try:
        with open(f"/proc/{pid}/environ", "rb") as handle:
            raw = handle.read()
    except OSError:
        return None
    env: dict[str, str] = {}
    for chunk in raw.split(b"\0"):
        if not chunk or b"=" not in chunk:
            continue
        key, _, value = chunk.partition(b"=")
        env[key.decode("utf-8", "replace")] = value.decode("utf-8", "replace")
    return env


def _default_proc_environs() -> Iterable[dict[str, str]]:
    """The environ of every ``gnome-shell`` process, read from ``/proc``."""
    found: list[dict[str, str]] = []
    try:
        pids = [entry for entry in os.listdir("/proc") if entry.isdigit()]
    except OSError:
        return found
    for pid in pids:
        if _read_comm(pid) != "gnome-shell":
            continue
        env = _read_proc_environ(pid)
        if env is not None:
            found.append(env)
    return found


def discover_display(
    *,
    environ: Mapping[str, str] = os.environ,
    proc_environs=None,
) -> DisplayEnv | None:
    """Find the graphical session's display environment, or ``None`` if headless.

    If the *current* process environment already carries ``DISPLAY`` and
    ``XAUTHORITY`` (e.g. this CLI is already running inside the graphical
    session), those win outright. Otherwise ``proc_environs()`` is asked for
    candidate environments — by default, the environ of every ``gnome-shell``
    process — and the first one carrying both ``DISPLAY`` and ``XAUTHORITY``
    is used. Never raises; any failure reads as "no display found".
    """
    try:
        display = environ.get("DISPLAY")
        xauthority = environ.get("XAUTHORITY")
        if display and xauthority:
            return DisplayEnv(
                display=display,
                xauthority=xauthority,
                dbus=environ.get("DBUS_SESSION_BUS_ADDRESS"),
            )

        candidates = (proc_environs or _default_proc_environs)()
        for candidate in candidates:
            display = candidate.get("DISPLAY")
            xauthority = candidate.get("XAUTHORITY")
            if display and xauthority:
                return DisplayEnv(
                    display=display,
                    xauthority=xauthority,
                    dbus=candidate.get("DBUS_SESSION_BUS_ADDRESS"),
                )
    except Exception:  # noqa: BLE001 - discovery is always best-effort
        return None
    return None


@dataclass(frozen=True)
class ShotOutcome:
    """The result of one :func:`capture_frame` call."""

    ok: bool
    file: str | None = None
    window: bool | None = None
    size: list[int] | None = None
    geometry: dict[str, object] | None = None
    reason: str | None = None


def _window_geometry(runner, env: Mapping[str, str]) -> dict[str, int] | None:
    """Best-effort absolute geometry of the viewer window via ``xwininfo``."""
    try:
        result = runner(
            ["xwininfo", "-root", "-tree"],
            capture_output=True,
            text=True,
            env=dict(env),
        )
    except Exception:  # noqa: BLE001
        return None
    if result.returncode != 0:
        return None
    for line in result.stdout.splitlines():
        if _WINDOW_TITLE not in line:
            continue
        match = _XWININFO_RE.search(line)
        if match:
            w, h, x, y = match.groups()
            return {"x": int(x), "y": int(y), "w": int(w), "h": int(h)}
    return None


def _screen_size(runner, env: Mapping[str, str]) -> list[int] | None:
    """Best-effort full-screen pixel size via ``xdpyinfo``."""
    try:
        result = runner(["xdpyinfo"], capture_output=True, text=True, env=dict(env))
    except Exception:  # noqa: BLE001
        return None
    if result.returncode != 0:
        return None
    match = _XDPYINFO_RE.search(result.stdout)
    if not match:
        return None
    return [int(match.group(1)), int(match.group(2))]


def _focus_window(runner, env: Mapping[str, str]) -> None:
    """Best-effort: bring the viewer window to the front before the shot."""
    try:
        if shutil.which("xdotool"):
            runner(
                ["xdotool", "search", "--name", _WINDOW_TITLE, "windowactivate", "--sync"],
                capture_output=True,
                text=True,
                env=dict(env),
            )
        elif shutil.which("wmctrl"):
            runner(["wmctrl", "-a", _WINDOW_TITLE], capture_output=True, text=True, env=dict(env))
    except Exception:  # nosec B110 - focusing is never load-bearing; the shot proceeds unfocused
        pass


def _read_png_size(path: str) -> list[int] | None:
    """The ``[width, height]`` of a PNG, read from its IHDR chunk. No image library."""
    try:
        with open(path, "rb") as handle:
            header = handle.read(24)
    except OSError:
        return None
    if len(header) < 24 or header[:8] != _PNG_MAGIC:
        return None
    width = int.from_bytes(header[16:20], "big")
    height = int.from_bytes(header[20:24], "big")
    return [width, height]


def capture_frame(
    run_dir,
    name: str,
    *,
    env: DisplayEnv | None,
    runner=subprocess.run,
) -> ShotOutcome:
    """Capture one frame of the MuJoCo viewer window.

    ``env is None`` means no graphical session was found: this returns a
    ``headless`` outcome without calling ``runner`` at all. Otherwise this
    tries, in order: (1) the window's absolute geometry via ``xwininfo``;
    (2) if ``xdotool`` or ``wmctrl`` is on ``PATH``, focus the window and take
    a window-only shot with ``gnome-screenshot -w``; (3) otherwise a
    full-screen shot, carrying the ``xwininfo`` geometry (plus the screen size
    from ``xdpyinfo``, when available) so the page can crop it. Never raises —
    any subprocess failure or exception is reported as ``ok=False``.
    """
    if env is None:
        return ShotOutcome(ok=False, reason="headless")

    proc_env = env.as_env(os.environ)
    shots_dir = run_dir.shots
    try:
        os.makedirs(shots_dir, exist_ok=True)
    except OSError:
        pass
    path = os.path.join(shots_dir, f"{name}.png")
    rel_file = os.path.join("shots", f"{name}.png")

    wininfo = _window_geometry(runner, proc_env)
    has_focus_tool = bool(shutil.which("xdotool") or shutil.which("wmctrl"))

    try:
        if has_focus_tool:
            _focus_window(runner, proc_env)
            result = runner(
                ["gnome-screenshot", "-w", "-f", path],
                capture_output=True,
                text=True,
                env=proc_env,
            )
            window = True
            geometry = None
        else:
            result = runner(
                ["gnome-screenshot", "-f", path],
                capture_output=True,
                text=True,
                env=proc_env,
            )
            window = False
            if wininfo is not None:
                geometry = dict(wininfo)
                geometry["screen"] = _screen_size(runner, proc_env)
            else:
                geometry = None
    except Exception as exc:  # noqa: BLE001 - a screenshot is best-effort
        return ShotOutcome(ok=False, reason=str(exc))

    if result.returncode != 0:
        reason = (result.stderr or "").strip() or f"gnome-screenshot exited {result.returncode}"
        return ShotOutcome(ok=False, reason=reason)

    size = _read_png_size(path)
    return ShotOutcome(ok=True, file=rel_file, window=window, size=size, geometry=geometry)


@contextmanager
def pause_autolock(runner=subprocess.run):
    """Pause the desktop's idle-lock while a trace run is capturing frames.

    On enter, reads ``org.gnome.desktop.session idle-delay`` and
    ``org.gnome.desktop.screensaver lock-enabled`` via ``gsettings get``, sets
    both off, and yields the two original strings verbatim (e.g.
    ``{"idle-delay": "uint32 300", "lock-enabled": "true"}``). Restores those
    exact strings in a ``finally``, so a raising body still leaves the desktop
    as it found it. If the initial reads fail, yields ``{}`` and changes
    nothing — pausing the lock is never load-bearing for a trace run.
    """
    original: dict[str, str] = {}
    try:
        for field, (schema, key) in _GSETTINGS_KEYS.items():
            result = runner(["gsettings", "get", schema, key], capture_output=True, text=True)
            if result.returncode != 0:
                raise RuntimeError(result.stderr or f"gsettings get {schema} {key} failed")
            original[field] = result.stdout.strip()
    except Exception:  # noqa: BLE001 - no gsettings means nothing to pause
        original = {}

    if original:
        for field, (schema, key) in _GSETTINGS_KEYS.items():
            try:
                runner(
                    ["gsettings", "set", schema, key, _AUTOLOCK_OFF[field]],
                    capture_output=True,
                    text=True,
                )
            except Exception:  # nosec B110 - best-effort pause; restore still runs regardless
                pass

    try:
        yield dict(original)
    finally:
        if original:
            for field, (schema, key) in _GSETTINGS_KEYS.items():
                try:
                    runner(
                        ["gsettings", "set", schema, key, original[field]],
                        capture_output=True,
                        text=True,
                    )
                except Exception:  # nosec B110 - restore is best-effort per key, never fatal
                    pass
