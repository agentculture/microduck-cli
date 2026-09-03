"""Guard: no write-mode open of robotd.toml / updater.toml under microduck_cli/.

CLAUDE.md / plan t7: this CLI-only surface is config, discovery, inventory,
dry-run planning — it must never write live daemon/updater config files.
AST-based scan (not regex on comments).
"""

from __future__ import annotations

import ast
from pathlib import Path

_GUARDED_SUFFIXES = ("robotd.toml", "updater.toml")

# Modes on open()/os.open() considered "write mode".
_WRITE_MODE_CHARS = {"w", "a", "x", "+"}


def _string_value(node: ast.AST) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


def _path_ends_with_guarded_name(value: str) -> bool:
    return any(value.endswith(suffix) for suffix in _GUARDED_SUFFIXES)


def _open_call_mode_is_write(node: ast.Call, *, default_write: bool) -> bool:
    """Determine if a builtin open()/Path.open() call uses a write mode.

    ``default_write`` covers Path.write_text()-style APIs that always write.
    """
    if default_write:
        return True

    mode_arg = None
    if len(node.args) >= 2:
        mode_arg = node.args[1]
    else:
        for kw in node.keywords:
            if kw.arg == "mode":
                mode_arg = kw.value
                break

    if mode_arg is None:
        # open()'s default mode is "r" -- not a write.
        return False

    mode_value = _string_value(mode_arg)
    if mode_value is None:
        # Can't statically determine the mode; be conservative and flag it.
        return True

    return any(ch in _WRITE_MODE_CHARS for ch in mode_value)


def scan_for_config_writes(root: Path) -> list[str]:
    """Scan every .py file under ``root``, flagging writes to guarded config files.

    Flags any ``open()``, ``Path.open()``, or ``Path.write_text()`` call whose
    first-argument (or, for ``.write_text``, the receiver's constructed path)
    string literal ends with ``robotd.toml`` or ``updater.toml``, when opened
    in a write mode (or unconditionally, for ``write_text``).
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

        # Track simple string-literal variable assignments so a path built as
        # `p = ".../robotd.toml"` then `open(p, "w")` is still caught.
        literal_vars: dict[str, str] = {}
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign):
                value = _string_value(node.value)
                if value is not None:
                    for target in node.targets:
                        if isinstance(target, ast.Name):
                            literal_vars[target.id] = value

        def resolve_str(node: ast.AST) -> str | None:
            value = _string_value(node)
            if value is not None:
                return value
            if isinstance(node, ast.Name) and node.id in literal_vars:
                return literal_vars[node.id]
            return None

        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func

            # open(path, mode) or Path(...).open(mode)
            is_builtin_open = isinstance(func, ast.Name) and func.id == "open"
            is_path_open = isinstance(func, ast.Attribute) and func.attr == "open"
            is_write_text = isinstance(func, ast.Attribute) and func.attr == "write_text"

            if is_builtin_open:
                target_arg = node.args[0] if node.args else None
                target = resolve_str(target_arg) if target_arg is not None else None
                if target and _path_ends_with_guarded_name(target):
                    if _open_call_mode_is_write(node, default_write=False):
                        violations.append(
                            f"{path}: write-mode open() of guarded config "
                            f"{target!r} (line {node.lineno})"
                        )
            elif is_path_open:
                # func.value is the Path expression; try to resolve a literal
                # constructed via Path("...") or a plain variable holding one.
                target = None
                base = func.value
                if isinstance(base, ast.Call) and isinstance(base.func, ast.Name):
                    if base.func.id in ("Path", "PurePath") and base.args:
                        target = resolve_str(base.args[0])
                elif isinstance(base, ast.BinOp) and isinstance(base.op, ast.Div):
                    right = resolve_str(base.right)
                    if right is not None:
                        target = right
                if target and _path_ends_with_guarded_name(target):
                    if _open_call_mode_is_write(node, default_write=False):
                        violations.append(
                            f"{path}: write-mode .open() of guarded config "
                            f"{target!r} (line {node.lineno})"
                        )
            elif is_write_text:
                base = func.value
                target = None
                if isinstance(base, ast.Call) and isinstance(base.func, ast.Name):
                    if base.func.id in ("Path", "PurePath") and base.args:
                        target = resolve_str(base.args[0])
                elif isinstance(base, ast.BinOp) and isinstance(base.op, ast.Div):
                    right = resolve_str(base.right)
                    if right is not None:
                        target = right
                elif isinstance(base, ast.Name) and base.id in literal_vars:
                    target = literal_vars[base.id]
                if target and _path_ends_with_guarded_name(target):
                    violations.append(
                        f"{path}: .write_text() of guarded config {target!r} "
                        f"(line {node.lineno})"
                    )

    return violations


def _write_module(path: Path, body: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")


def test_scan_flags_open_write_mode(tmp_path: Path) -> None:
    pkg = tmp_path / "violator_pkg"
    _write_module(pkg / "__init__.py", "")
    _write_module(
        pkg / "bad.py",
        "fh = open('/etc/microduck/robotd.toml', 'w')\nfh.write('x')\n",
    )

    violations = scan_for_config_writes(pkg)
    assert violations, "expected write-mode open() of robotd.toml to be flagged"


def test_scan_flags_path_write_text(tmp_path: Path) -> None:
    pkg = tmp_path / "violator_pkg2"
    _write_module(pkg / "__init__.py", "")
    _write_module(
        pkg / "bad.py",
        "from pathlib import Path\n\nPath('/etc/microduck/updater.toml').write_text('x')\n",
    )

    violations = scan_for_config_writes(pkg)
    assert violations, "expected Path.write_text() of updater.toml to be flagged"


def test_scan_ignores_read_mode(tmp_path: Path) -> None:
    pkg = tmp_path / "clean_pkg"
    _write_module(pkg / "__init__.py", "")
    _write_module(
        pkg / "ok.py",
        "fh = open('/etc/microduck/robotd.toml', 'r')\n",
    )

    violations = scan_for_config_writes(pkg)
    assert violations == []


def test_scan_passes_on_real_microduck_cli() -> None:
    root = Path(__file__).resolve().parents[1] / "microduck_cli"
    violations = scan_for_config_writes(root)
    assert violations == []
