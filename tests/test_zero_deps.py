"""Guard: microduck-cli ships zero third-party runtime dependencies.

CLAUDE.md hard constraint: ``dependencies = []`` on purpose, and no hardware
extra may sneak a name like ``mjlab``, ``warp-lang``, ``torch`` or
``better-actuator-models`` into ``[project.optional-dependencies]`` either —
those are exactly the kind of heavyweight simulation/ML/actuator libraries
that would break "introspection works on a bare box with no robot attached."
"""

from __future__ import annotations

import tomllib
from pathlib import Path

_FORBIDDEN_NAMES = {"mjlab", "warp-lang", "torch", "better-actuator-models"}


def _load_project_table(pyproject_path: Path) -> dict:
    """Parse ``pyproject.toml`` at ``pyproject_path`` and return ``[project]``."""
    with pyproject_path.open("rb") as fh:
        data = tomllib.load(fh)
    return data.get("project", {})


def check_zero_deps(pyproject_path: Path) -> list[str]:
    """Return a list of violation messages for the given pyproject.toml.

    Empty list means the file satisfies the zero-deps + no-forbidden-extra
    contract.
    """
    violations: list[str] = []
    project = _load_project_table(pyproject_path)

    dependencies = project.get("dependencies", [])
    if dependencies != []:
        violations.append(f"[project].dependencies must be [], got {dependencies!r}")

    optional = project.get("optional-dependencies", {})
    for extra_name, specs in optional.items():
        for spec in specs:
            # A dependency spec may carry version/marker suffixes (e.g. "torch>=2.0");
            # compare on the bare distribution name only.
            name = spec.split(";")[0]
            for sep in ("==", ">=", "<=", "~=", "!=", ">", "<", "[", " "):
                name = name.split(sep)[0]
            name = name.strip().lower()
            if name in _FORBIDDEN_NAMES:
                violations.append(
                    f"[project.optional-dependencies.{extra_name}] names forbidden "
                    f"dependency {name!r} (from spec {spec!r})"
                )

    return violations


def _write_pyproject(path: Path, body: str) -> Path:
    pyproject = path / "pyproject.toml"
    pyproject.write_text(body, encoding="utf-8")
    return pyproject


def test_check_zero_deps_flags_nonempty_dependencies(tmp_path: Path) -> None:
    bad = _write_pyproject(
        tmp_path,
        """
[project]
name = "violator"
version = "0.0.0"
dependencies = ["requests>=2.0"]
""",
    )
    violations = check_zero_deps(bad)
    assert violations, "expected a violation for non-empty dependencies"
    assert any("dependencies" in v for v in violations)


def test_check_zero_deps_flags_forbidden_extra(tmp_path: Path) -> None:
    bad = _write_pyproject(
        tmp_path,
        """
[project]
name = "violator"
version = "0.0.0"
dependencies = []

[project.optional-dependencies]
sim = ["mjlab>=1.0", "numpy"]
""",
    )
    violations = check_zero_deps(bad)
    assert violations, "expected a violation for a forbidden optional dependency"
    assert any("mjlab" in v for v in violations)


def test_check_zero_deps_passes_on_real_pyproject() -> None:
    root = Path(__file__).resolve().parents[1]
    violations = check_zero_deps(root / "pyproject.toml")
    assert violations == []
