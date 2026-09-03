"""Resolve a duck name to its control sockets.

Follows the naming convention of upstream's simulation harness,
``pollen-robotics/microduck``'s ``scripts/duck-sim`` (pinned commit in
``docs/upstream-pins.md``; nothing from that script is copied here, only its
documented layout): each duck named ``<name>`` under a state directory has a
robot-control socket at ``<state>/<name>.sock`` and a depth-sensor socket at
``<state>/<name>-tof.sock``.

The gamepad is the odd one out: ``pad.input`` is served by ``padd`` on its own
socket (``/run/padd/pad.sock``, or ``DUCK_PAD_SOCKET``) on a real duck, and the
simulation harness has no padd at all — see :func:`pad_socket_path`, which
answers ``None`` in that case rather than inventing a path.

Everything here is pure: the environment mapping and the directory listing
are both injected, and nothing here ever opens a socket, spawns a
subprocess, or otherwise touches the network.
"""

from __future__ import annotations

import os
from collections.abc import Callable, Mapping
from dataclasses import dataclass

from microduck_cli.cli._errors import EXIT_ENV_ERROR, EXIT_USER_ERROR, CliError

#: ``DUCK_SIM_STATE`` default, matching ``scripts/duck-sim``'s ``$HOME/.cache/duck-sim``.
DEFAULT_STATE_DIR = "~/.cache/duck-sim"

#: The unix domain socket path limit (``sockaddr_un.sun_path``) on Linux is ~108
#: bytes including the trailing NUL. We refuse anything past 100 bytes so there
#: is headroom for the name suffixes (``-tof.sock``) upstream appends internally.
SOCKET_PATH_BYTE_LIMIT = 100
_UNIX_SUN_PATH_BYTES = 108

_SOCK_SUFFIX = ".sock"
_TOF_SUFFIX = "-tof.sock"

#: Where ``padd`` serves ``pad.input`` on a real duck. Upstream routes the pad
#: channel to padd's own socket, NOT to robotd's — anything that subscribed
#: ``pad.input`` on the robot socket would be asking the wrong daemon. The
#: simulation harness ships no padd at all, which is why :func:`pad_socket_path`
#: answers ``None`` rather than inventing a sim path for it.
DEFAULT_PAD_SOCKET = "/run/padd/pad.sock"

#: Overrides :data:`DEFAULT_PAD_SOCKET`: the only way to point the pad link at a
#: box that serves it somewhere other than ``/run/padd``.
PAD_SOCKET_ENV = "DUCK_PAD_SOCKET"

#: The letters ``scripts/duck-sim`` hands out, in order: ``duck-a`` … ``duck-p``.
#: Sixteen is upstream's own ceiling (its ``down`` sweeps indices 0..15).
DUCK_LETTERS = "abcdefghijklmnop"

ListDir = Callable[[str], list[str]]
Exists = Callable[[str], bool]


def pad_socket_path(
    env: Mapping[str, str] | None = None,
    *,
    exists: Exists = os.path.exists,
) -> str | None:
    """The socket ``padd`` serves ``pad.input`` on, or ``None`` when there is none.

    ``DUCK_PAD_SOCKET`` wins; otherwise :data:`DEFAULT_PAD_SOCKET`. Either way the
    path is answered only when it is actually there — a sim box has no padd, and a
    caller must be able to tell "no gamepad daemon here" from "the gamepad is
    quiet". ``exists`` is injected, so this stays as pure as the rest of the
    module.
    """
    env = env or {}
    candidate = env.get(PAD_SOCKET_ENV) or DEFAULT_PAD_SOCKET
    return candidate if exists(candidate) else None


def duck_name(index: int) -> str:
    """Return the ``duck-<letter>`` name upstream gives the duck at ``index``.

    ``duck_name(0) == "duck-a"``. Raises ``CliError`` (exit 1) past the
    sixteen names ``scripts/duck-sim`` knows how to start and stop.
    """
    if index < 0 or index >= len(DUCK_LETTERS):
        raise CliError(
            code=EXIT_USER_ERROR,
            message=(
                f"duck index {index} is outside 0..{len(DUCK_LETTERS) - 1}; upstream's "
                f"simulation harness names at most {len(DUCK_LETTERS)} ducks"
            ),
            remediation=f"ask for at most {len(DUCK_LETTERS)} ducks",
        )
    return f"duck-{DUCK_LETTERS[index]}"


@dataclass(frozen=True)
class DuckAddress:
    """The resolved sockets for one duck, and how they were chosen."""

    name: str
    socket_path: str
    tof_socket_path: str
    state_dir: str
    source: str


def _state_dir(env: Mapping[str, str]) -> str:
    raw = env.get("DUCK_SIM_STATE") or DEFAULT_STATE_DIR
    if raw.startswith("~"):
        home = env.get("HOME")
        if home:
            return home + raw[1:]
        return os.path.expanduser(raw)
    return raw


def _sock_paths(state_dir: str, name: str) -> tuple[str, str]:
    return (
        os.path.join(state_dir, f"{name}{_SOCK_SUFFIX}"),
        os.path.join(state_dir, f"{name}{_TOF_SUFFIX}"),
    )


def _duck_name_from_sock(filename: str) -> str | None:
    if filename.endswith(_TOF_SUFFIX):
        return filename[: -len(_TOF_SUFFIX)]
    if filename.endswith(_SOCK_SUFFIX):
        return filename[: -len(_SOCK_SUFFIX)]
    return None


def _all_sockets_present(state_dir: str, listdir: ListDir) -> list[str] | None:
    """Return every ``*.sock`` entry in ``state_dir``, or ``None`` if it's unreadable."""
    try:
        entries = listdir(state_dir)
    except OSError:
        return None
    return sorted(e for e in entries if e.endswith(_SOCK_SUFFIX) or e.endswith(_TOF_SUFFIX))


def _no_such_duck(name: str, state_dir: str, sockets: list[str] | None) -> CliError:
    if not sockets:
        listing = f"the state directory {state_dir} is empty or missing"
    else:
        listing = f"sockets present in {state_dir}: {', '.join(sockets)}"
    return CliError(
        code=EXIT_USER_ERROR,
        message=f"no such duck '{name}' ({listing})",
        remediation=(
            "pass --duck/--socket naming one of the ducks present, or start a duck "
            "under this state directory first"
        ),
    )


def _check_length(path: str) -> None:
    length = len(path.encode("utf-8"))
    if length > SOCKET_PATH_BYTE_LIMIT:
        raise CliError(
            code=EXIT_ENV_ERROR,
            message=(
                f"socket path {path!r} is {length} bytes, over the "
                f"{SOCKET_PATH_BYTE_LIMIT}-byte limit this CLI enforces (unix sockets cap "
                f"sun_path at ~{_UNIX_SUN_PATH_BYTES} bytes including the trailing NUL)"
            ),
            remediation="set DUCK_SIM_STATE to a shorter state directory",
        )


def _explicit_socket_address(socket: str, name: str | None, state_dir: str) -> DuckAddress:
    """The address for an explicit ``--socket`` path, which wins over every other input."""
    resolved_name = _duck_name_from_sock(os.path.basename(socket)) or (name or "")
    tof_path = os.path.join(state_dir, f"{resolved_name}{_TOF_SUFFIX}") if resolved_name else socket
    address = DuckAddress(
        name=resolved_name,
        socket_path=socket,
        tof_socket_path=tof_path,
        state_dir=state_dir,
        source="--socket",
    )
    _check_length(address.socket_path)
    _check_length(address.tof_socket_path)
    return address


def _duck_from_state_dir(sockets: list[str] | None, state_dir: str) -> tuple[str, str]:
    """``(name, source)`` for the duck to use when nothing named one.

    The alphabetically first robot socket in the state directory wins, and
    *source* records whether it was the only candidate.
    """
    robot_names = sorted(
        {
            _duck_name_from_sock(s)
            for s in (sockets or [])
            if s.endswith(_SOCK_SUFFIX) and not s.endswith(_TOF_SUFFIX)
        }
    )
    if not robot_names:
        raise _no_such_duck("<unspecified>", state_dir, sockets)
    source = (
        "state-dir-single"
        if len(robot_names) == 1
        else f"state-dir-first-of-{len(robot_names)}-alphabetical"
    )
    return str(robot_names[0]), source


def resolve(
    name: str | None = None,
    *,
    socket: str | None = None,
    env: Mapping[str, str] | None = None,
    listdir: ListDir,
) -> DuckAddress:
    """Resolve a duck to its sockets.

    Precedence: an explicit ``socket`` path wins outright; otherwise ``name``
    (typically ``--duck``) or ``DUCK_SIM_DUCK`` in ``env`` selects a duck by
    name; otherwise the single ``*.sock`` (excluding ``*-tof.sock``) present
    in the state directory is used, picking the first alphabetically (and
    recording that in ``source``) when there is more than one.

    Raises ``CliError`` (exit 1) for an unknown/missing duck, and ``CliError``
    (exit 2) when a resolved socket path is too long for a unix socket.
    Pure — no network, no subprocess, no socket connect; ``listdir`` and
    ``env`` are both injected.
    """
    env = env or {}
    state_dir = _state_dir(env)

    if socket:
        return _explicit_socket_address(socket, name, state_dir)

    chosen_name = name or env.get("DUCK_SIM_DUCK")
    source = "--duck" if name else "DUCK_SIM_DUCK"
    sockets = _all_sockets_present(state_dir, listdir)

    if not chosen_name:
        chosen_name, source = _duck_from_state_dir(sockets, state_dir)
    elif not sockets or f"{chosen_name}{_SOCK_SUFFIX}" not in sockets:
        raise _no_such_duck(chosen_name, state_dir, sockets)

    socket_path, tof_socket_path = _sock_paths(state_dir, chosen_name)
    _check_length(socket_path)
    _check_length(tof_socket_path)

    return DuckAddress(
        name=chosen_name,
        socket_path=socket_path,
        tof_socket_path=tof_socket_path,
        state_dir=state_dir,
        source=source,
    )
