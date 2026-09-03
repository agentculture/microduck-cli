"""Guard: no hardware serial/actuator imports or /dev/tty opens under microduck_cli/.

CLAUDE.md: hardware deps belong behind a lazy-imported extra, and this repo
has none yet. Duck motion has not landed, so nothing under ``microduck_cli/``
should import a serial/actuator library or touch a ``/dev/tty*`` device path
directly. AST-based (not regex on comments), per the plan/task brief.
"""

from __future__ import annotations

import ast
from pathlib import Path

_FORBIDDEN_MODULES = {"serial", "pyserial", "rustypot", "dynamixel_sdk"}
_OPEN_CALL_NAMES = {"open"}
_OS_OPEN_ATTRS = {"open"}
_SERIAL_CTOR_ATTRS = {"Serial"}


def _root_module(name: str) -> str:
    return name.split(".")[0]


def _string_starts_with_dev_tty(node: ast.AST) -> bool:
    return (
        isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and node.value.startswith("/dev/tty")
    )


def scan_for_hardware_paths(root: Path) -> list[str]:
    """Scan every .py file under ``root`` and return violation messages.

    Flags:
      - ``import serial`` / ``import pyserial`` / ``import rustypot`` /
        ``import dynamixel_sdk`` (any form, including ``from x import y``)
      - a string literal starting with ``/dev/tty`` passed as an argument to
        ``open()``, ``os.open()``, or ``serial.Serial()``.
    """
    violations: list[str] = []

    for path in sorted(root.rglob("*.py")):
        try:
            source = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        try:
            tree = ast.parse(source, filename=str(path))
        except SyntaxError:
            continue

        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if _root_module(alias.name) in _FORBIDDEN_MODULES:
                        violations.append(
                            f"{path}: forbidden import of {alias.name!r} " f"(line {node.lineno})"
                        )
            elif isinstance(node, ast.ImportFrom):
                if node.module and _root_module(node.module) in _FORBIDDEN_MODULES:
                    violations.append(
                        f"{path}: forbidden import from {node.module!r} " f"(line {node.lineno})"
                    )
            elif isinstance(node, ast.Call):
                func = node.func
                is_open_call = isinstance(func, ast.Name) and func.id in _OPEN_CALL_NAMES
                is_os_open_call = (
                    isinstance(func, ast.Attribute)
                    and func.attr in _OS_OPEN_ATTRS
                    and isinstance(func.value, ast.Name)
                    and func.value.id == "os"
                )
                is_serial_ctor_call = (
                    isinstance(func, ast.Attribute)
                    and func.attr in _SERIAL_CTOR_ATTRS
                    and isinstance(func.value, ast.Name)
                    and func.value.id == "serial"
                )
                if is_open_call or is_os_open_call or is_serial_ctor_call:
                    args = list(node.args) + [kw.value for kw in node.keywords]
                    if any(_string_starts_with_dev_tty(arg) for arg in args):
                        violations.append(
                            f"{path}: opens a /dev/tty* path literal (line {node.lineno})"
                        )

    return violations


def _write_module(path: Path, body: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")


def test_scan_flags_forbidden_import(tmp_path: Path) -> None:
    pkg = tmp_path / "violator_pkg"
    _write_module(pkg / "__init__.py", "")
    _write_module(pkg / "bad.py", "import serial\n\nserial.Serial('/dev/ttyUSB0')\n")

    violations = scan_for_hardware_paths(pkg)
    assert violations, "expected the forbidden import/open to be flagged"
    assert any("serial" in v for v in violations)


def test_scan_flags_dev_tty_open(tmp_path: Path) -> None:
    pkg = tmp_path / "violator_pkg2"
    _write_module(pkg / "__init__.py", "")
    _write_module(pkg / "bad.py", "fh = open('/dev/ttyACM0', 'rb')\n")

    violations = scan_for_hardware_paths(pkg)
    assert violations, "expected the /dev/tty open() to be flagged"
    assert any("/dev/tty" in v for v in violations)


def test_scan_flags_os_open_dev_tty(tmp_path: Path) -> None:
    pkg = tmp_path / "violator_pkg3"
    _write_module(pkg / "__init__.py", "")
    _write_module(pkg / "bad.py", "import os\n\nos.open('/dev/ttyUSB1', os.O_RDWR)\n")

    violations = scan_for_hardware_paths(pkg)
    assert violations, "expected os.open('/dev/tty...') to be flagged"


def test_scan_passes_on_real_microduck_cli() -> None:
    root = Path(__file__).resolve().parents[1] / "microduck_cli"
    violations = scan_for_hardware_paths(root)
    assert violations == []
