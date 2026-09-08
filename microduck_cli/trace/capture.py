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
# absolute screen position the contract asks us to record. A window left of
# or above the primary monitor has a negative coordinate, which xwininfo
# renders either as a bare sign ("-300") or a doubled one ("+-300") depending
# on version — ``_COORD`` matches both.
_COORD = r"[+-]-?\d+"
_XWININFO_RE = re.compile(rf"(\d+)x(\d+){_COORD}{_COORD}\s+({_COORD})({_COORD})")

# xdpyinfo prints a line such as "  dimensions:    1920x1080 pixels (...)".
_XDPYINFO_RE = re.compile(r"dimensions:\s+(\d+)x(\d+)\s+pixels")

_PNG_MAGIC = b"\x89PNG\r\n\x1a\n"

#: A shot ``name`` is joined under ``shots/`` as a single path segment — no
#: separators, no leading ``.``, no absolute form — so ``../x`` or ``/abs``
#: can never escape the run directory.
_SHOT_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,80}$")

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
            w, h, x_raw, y_raw = match.groups()
            # A doubled sign ("+-300") is redundant with the leading token;
            # stripping the "+" leaves a plain int()-parseable string in
            # every case ("-300", "-300"; "+70" -> "70").
            x = int(x_raw.replace("+", ""))
            y = int(y_raw.replace("+", ""))
            return {"x": x, "y": y, "w": int(w), "h": int(h)}
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


def _focus_window(runner, env: Mapping[str, str]) -> bool:
    """Best-effort: bring the viewer window to the front before the shot.

    Returns ``True`` only when the focus command actually succeeded — for
    ``xdotool`` that means ``search`` printed at least one window id *and*
    the follow-up ``windowactivate`` exited 0; for ``wmctrl`` it means the
    single activate call exited 0. ``False`` tells the caller to fall back to
    the full-screen + geometry path instead of trusting whatever window
    happens to be active.
    """
    try:
        if shutil.which("xdotool"):
            search = runner(
                ["xdotool", "search", "--name", _WINDOW_TITLE],
                capture_output=True,
                text=True,
                env=dict(env),
            )
            window_ids = [line.strip() for line in search.stdout.splitlines() if line.strip()]
            if search.returncode != 0 or not window_ids:
                return False
            activate = runner(
                ["xdotool", "windowactivate", "--sync", window_ids[0]],
                capture_output=True,
                text=True,
                env=dict(env),
            )
            return activate.returncode == 0
        if shutil.which("wmctrl"):
            result = runner(
                ["wmctrl", "-a", _WINDOW_TITLE], capture_output=True, text=True, env=dict(env)
            )
            return result.returncode == 0
    except Exception:  # noqa: BLE001 - focusing is never load-bearing
        return False
    return False


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


def _resolve_shot_path(shots_dir: str, name: str) -> str | None:
    """The ``.png`` path *name* names inside *shots_dir*, or ``None`` if it escapes.

    Two independent gates, because either alone is bypassable: *name* must match
    :data:`_SHOT_NAME_RE`, **and** the resolved path must still land inside the
    resolved ``shots_dir`` (symlinks are followed before the check, so a linked
    ``shots/`` cannot redirect a write outside the run directory).
    """
    if not _SHOT_NAME_RE.match(name):
        return None
    path = os.path.join(shots_dir, f"{name}.png")
    resolved_dir = os.path.realpath(shots_dir)
    resolved_path = os.path.realpath(path)
    try:
        contained = os.path.commonpath([resolved_dir, resolved_path]) == resolved_dir
    except ValueError:  # different drives on Windows-style paths
        contained = False
    return path if contained else None


def capture_frame(
    run_dir,
    name: str,
    *,
    env: DisplayEnv | None,
    runner=subprocess.run,
) -> ShotOutcome:
    """Capture one frame of the MuJoCo viewer window.

    ``env is None`` means no graphical session was found: this returns a
    ``headless`` outcome without calling ``runner`` at all. ``name`` is
    validated against :data:`_SHOT_NAME_RE` (and the resolved target path is
    re-checked to land inside ``run_dir.shots``) before anything else runs —
    an invalid name is reported as ``ok=False`` and never reaches ``runner``.
    Otherwise this tries, in order: (1) the window's absolute geometry via
    ``xwininfo``; (2) if ``xdotool`` or ``wmctrl`` is on ``PATH`` *and*
    actually focuses the window, a window-only shot with
    ``gnome-screenshot -w``; (3) otherwise a full-screen shot, carrying the
    ``xwininfo`` geometry (plus the screen size from ``xdpyinfo``, when
    available) so the page can crop it. If neither the focus attempt nor the
    geometry lookup finds the viewer window at all, this reports ``ok=False``
    rather than a misleading frame. Never raises — any subprocess failure or
    exception is reported as ``ok=False``.
    """
    if env is None:
        return ShotOutcome(ok=False, reason="headless")

    shots_dir = run_dir.shots
    path = _resolve_shot_path(shots_dir, name)
    if path is None:
        return ShotOutcome(ok=False, reason=f"invalid frame name: {name!r}")

    proc_env = env.as_env(os.environ)
    try:
        os.makedirs(shots_dir, exist_ok=True)
    except OSError:
        pass
    rel_file = os.path.join("shots", f"{name}.png")

    wininfo = _window_geometry(runner, proc_env)
    has_focus_tool = bool(shutil.which("xdotool") or shutil.which("wmctrl"))

    focused = has_focus_tool and _focus_window(runner, proc_env)

    if not focused and wininfo is None:
        return ShotOutcome(ok=False, reason="no MuJoCo viewer window found")

    try:
        if focused:
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
            geometry = dict(wininfo)
            geometry["screen"] = _screen_size(runner, proc_env)
    except Exception as exc:  # noqa: BLE001 - a screenshot is best-effort
        return ShotOutcome(ok=False, reason=str(exc))

    if result.returncode != 0:
        reason = (result.stderr or "").strip() or f"gnome-screenshot exited {result.returncode}"
        return ShotOutcome(ok=False, reason=reason)

    size = _read_png_size(path)
    if size is None:
        # rc 0 with no readable PNG happens (a killed compositor, a full disk, a
        # truncated write). Reporting ok=True would put a broken <img> on the page
        # and claim a frame that isn't there, so the husk goes and the outcome says
        # what actually happened.
        _discard(path)
        return ShotOutcome(ok=False, reason="screenshot produced no readable PNG")
    return ShotOutcome(ok=True, file=rel_file, window=window, size=size, geometry=geometry)


def _discard(path: str) -> None:
    """Remove a zero-byte or truncated screenshot; an absent file is already fine."""
    try:
        os.remove(path)
    except OSError:
        # Cleanup is best-effort: the outcome already reports ok=False, and a
        # file we cannot remove is not worth failing a run over.
        pass


def _gsettings_set(runner, proc_env, field: str, value: str) -> bool:
    """One ``gsettings set``; ``False`` when it fails or raises. Never propagates."""
    schema, key = _GSETTINGS_KEYS[field]
    try:
        result = runner(
            ["gsettings", "set", schema, key, value],
            capture_output=True,
            text=True,
            env=proc_env,
        )
    except Exception:  # noqa: BLE001 - best-effort; the other key still gets a try
        return False
    return result.returncode == 0


def _gsettings_read_all(runner, proc_env) -> dict[str, str]:
    """Every autolock setting's current string, or ``{}`` if any read fails.

    All-or-nothing on purpose: a partial read cannot be restored faithfully, so
    a single failure (nonzero exit or a raised call — no ``gsettings`` at all)
    means the pause does not happen and nothing is touched.
    """
    original: dict[str, str] = {}
    try:
        for field, (schema, key) in _GSETTINGS_KEYS.items():
            result = runner(
                ["gsettings", "get", schema, key],
                capture_output=True,
                text=True,
                env=proc_env,
            )
            if result.returncode != 0:
                raise RuntimeError(result.stderr or f"gsettings get {schema} {key} failed")
            original[field] = result.stdout.strip()
    except Exception:  # noqa: BLE001 - no gsettings means nothing to pause
        return {}
    return original


def _gsettings_turn_off(runner, proc_env, original: dict[str, str]) -> dict[str, str]:
    """Switch each setting off; returns the originals of the ones that moved.

    Per-key, not all-or-nothing: a key whose ``set`` failed is left out, so the
    restore never puts back a value that was never changed.
    """
    changed: dict[str, str] = {}
    if original:
        for field in _GSETTINGS_KEYS:
            if _gsettings_set(runner, proc_env, field, _AUTOLOCK_OFF[field]):
                changed[field] = original[field]
    return changed


def _gsettings_restore(runner, proc_env, changed: dict[str, str]) -> None:
    """Put every changed setting back to its exact original string."""
    for field, value in changed.items():
        _gsettings_set(runner, proc_env, field, value)


@contextmanager
def pause_autolock(runner=subprocess.run, *, env: DisplayEnv | None = None):
    """Pause the desktop's idle-lock while a trace run is capturing frames.

    ``gsettings`` talks to the session bus, so over SSH — where this CLI's
    own environment carries no ``DBUS_SESSION_BUS_ADDRESS`` — every call must
    run with the *display owner's* environment or it silently targets
    nothing while the run still reports "paused". Pass the same
    :class:`DisplayEnv` :func:`discover_display` returned; it is applied
    (via :meth:`DisplayEnv.as_env`) to every read, set and restore call.

    On enter, reads ``org.gnome.desktop.session idle-delay`` and
    ``org.gnome.desktop.screensaver lock-enabled`` via ``gsettings get``, sets
    both off, and yields the two original strings verbatim (e.g.
    ``{"idle-delay": "uint32 300", "lock-enabled": "true"}``) — but *only* when
    every ``gsettings set`` actually reported success. The yielded mapping is
    the caller's evidence, not its intent: it is non-empty only when the
    desktop really is paused, so ``{}`` covers all three ways a pause fails to
    happen — a failing read (nonzero exit or a raised exception, in which case
    no ``set`` runs at all), a failing set, and a set that raised.

    Restore is independent of that verdict: whatever keys were successfully
    changed are put back to their exact original strings in a ``finally``, so a
    raising body still leaves the desktop as it found it and a key whose ``set``
    failed is never "restored" to a value it was never moved from.
    """
    proc_env = env.as_env(os.environ) if env is not None else None
    original = _gsettings_read_all(runner, proc_env)
    changed = _gsettings_turn_off(runner, proc_env, original)
    paused = dict(original) if len(changed) == len(_GSETTINGS_KEYS) else {}
    try:
        yield paused
    finally:
        _gsettings_restore(runner, proc_env, changed)
