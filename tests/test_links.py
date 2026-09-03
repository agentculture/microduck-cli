"""Upstream-link lockstep: every robot/training explain body and remediation
carries a URL into ``pollen-robotics/microduck`` or ``pollen-robotics/microduck_rl``.

Three checks, matching t22's acceptance criteria:

(a) every explain entry under the four domain nouns (``env``, ``duck``,
    ``policy``, ``rules``) contains a ``pollen-robotics`` URL;
(b) every ``CliError`` remediation raised by those nouns' command modules,
    plus ``microduck_cli/env/doctor.py`` and ``microduck_cli/train/lane.py``,
    that MENTIONS a robot/training topic keyword also carries a
    ``pollen-robotics`` URL — a remediation about pure CLI usage (``re-run
    with --apply``, ``run --help``) is exempt because it never mentions a
    topic keyword in the first place;
(c) no explain body restates a ``robotctl`` command table: more than three
    consecutive lines starting with ``robotctl`` or ``    robotctl`` fails.

(b) is implemented as a light AST walk, not a full symbolic interpreter: a
``CliError(...)``/``CliError`` construction's ``remediation`` argument is
resolved as far as string literals, f-string literal segments, and
references to simple module-level ``str`` constants (the pattern every
remediation in this codebase already uses to carry a URL — ``D1_REMEDIATION``,
``_UPSTREAM_SIM_DOC``, ``CHEATSHEET`` ...) go; anything else (a local
variable, a runtime value) is left unresolved. That is enough to catch every
remediation this repo actually writes, without executing the modules'
handlers.
"""

from __future__ import annotations

import ast
import importlib
import inspect
from pathlib import Path

import pytest

from microduck_cli.explain import duck as explain_duck
from microduck_cli.explain import env as explain_env
from microduck_cli.explain import policy as explain_policy
from microduck_cli.explain import rules as explain_rules

_POLLEN_MARKER = "github.com/pollen-robotics/"

_NOUN_EXPLAIN_MODULES = {
    "env": explain_env,
    "duck": explain_duck,
    "policy": explain_policy,
    "rules": explain_rules,
}

# The command modules whose CliError remediations are in scope, plus the two
# named-by-t22 modules that are not command modules at all.
_TARGET_MODULES = [
    "microduck_cli.cli._commands.env",
    "microduck_cli.cli._commands.duck",
    "microduck_cli.cli._commands.policy",
    "microduck_cli.cli._commands.rules",
    "microduck_cli.env.doctor",
    "microduck_cli.train.lane",
]

# A remediation is in scope for the URL requirement only if it mentions one of
# these topics (case-insensitive substring). Anything else — "re-run with
# --apply", "pass a JSON object", "run --help" — is pure CLI usage and exempt.
_TOPIC_KEYWORDS = (
    "robotd",
    "robotctl",
    "duck-sim",
    "policy",
    "skill",
    "train",
    "smoke",
    "wandb",
    "hf",
    "cargo",
    "onnxruntime",
)


# ---------------------------------------------------------------------------
# (a) explain entries carry an upstream link
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("noun", sorted(_NOUN_EXPLAIN_MODULES))
def test_every_noun_explain_entry_carries_a_pollen_robotics_url(noun: str) -> None:
    module = _NOUN_EXPLAIN_MODULES[noun]
    missing = [
        " ".join(path) or "(root)"
        for path, body in module.ENTRIES.items()
        if _POLLEN_MARKER not in body
    ]
    assert not missing, f"{noun}: explain entries with no pollen-robotics URL: {missing}"


# ---------------------------------------------------------------------------
# (c) no explain body restates a robotctl command table
# ---------------------------------------------------------------------------


def _max_consecutive_robotctl_lines(body: str) -> int:
    run = 0
    best = 0
    for line in body.splitlines():
        if line.startswith("robotctl") or line.startswith("    robotctl"):
            run += 1
            best = max(best, run)
        else:
            run = 0
    return best


@pytest.mark.parametrize("noun", sorted(_NOUN_EXPLAIN_MODULES))
def test_no_explain_body_restates_a_robotctl_command_table(noun: str) -> None:
    module = _NOUN_EXPLAIN_MODULES[noun]
    offenders = [
        " ".join(path) or "(root)"
        for path, body in module.ENTRIES.items()
        if _max_consecutive_robotctl_lines(body) > 3
    ]
    assert not offenders, (
        f"{noun}: explain bodies restating a robotctl command table (link the cheatsheet "
        f"instead): {offenders}"
    )


# ---------------------------------------------------------------------------
# (b) CliError remediations that mention a robot/training topic carry a URL
# ---------------------------------------------------------------------------


def _resolve_str(node: ast.AST, namespace: dict[str, object]) -> str:
    """Best-effort static resolution of a (possibly f-string) string expression.

    Resolves string literals, f-string literal segments, ``+`` concatenation,
    ``X.format(...)`` (the base object only — format args are usually not
    strings), and references to module-level string constants. Anything else
    (a local variable, a call whose result isn't a known constant) resolves to
    ``""`` — that is a deliberate under-approximation: it never invents text
    that isn't actually in the source, so it can only under-report keyword
    matches, never over-report a missing URL.
    """
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.JoinedStr):
        return "".join(_resolve_str(value, namespace) for value in node.values)
    if isinstance(node, ast.FormattedValue):
        return _resolve_str(node.value, namespace)
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        return _resolve_str(node.left, namespace) + _resolve_str(node.right, namespace)
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
        if node.func.attr == "format":
            return _resolve_str(node.func.value, namespace)
        return ""
    if isinstance(node, ast.Name) and node.id in namespace:
        value = namespace[node.id]
        return value if isinstance(value, str) else ""
    if isinstance(node, ast.Attribute):
        # e.g. proto.ROBOT_SUBSCRIBE — not resolved; local/imported symbol.
        return ""
    return ""


def _cli_error_remediations(module_name: str) -> list[tuple[int, str]]:
    """(lineno, resolved remediation text) for every CliError(...) construction."""
    module = importlib.import_module(module_name)
    source = inspect.getsource(module)
    tree = ast.parse(source)
    namespace = dict(vars(module))
    found: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        name = func.id if isinstance(func, ast.Name) else getattr(func, "attr", None)
        if name != "CliError":
            continue
        remediation_node = None
        for kw in node.keywords:
            if kw.arg == "remediation":
                remediation_node = kw.value
                break
        if remediation_node is None and len(node.args) >= 3:
            remediation_node = node.args[2]
        if remediation_node is None:
            continue
        found.append((node.lineno, _resolve_str(remediation_node, namespace)))
    return found


@pytest.mark.parametrize("module_name", _TARGET_MODULES)
def test_topic_cli_error_remediations_carry_a_pollen_robotics_url(module_name: str) -> None:
    offenders = []
    for lineno, text in _cli_error_remediations(module_name):
        lowered = text.lower()
        mentioned = [kw for kw in _TOPIC_KEYWORDS if kw in lowered]
        if mentioned and _POLLEN_MARKER not in text:
            offenders.append(f"{module_name}:{lineno} (mentions {mentioned}): {text!r}")
    assert not offenders, "remediations missing a pollen-robotics URL:\n  " + "\n  ".join(offenders)


def test_target_modules_exist_on_disk() -> None:
    # Sanity check the module list itself doesn't silently drift, e.g. after a rename.
    repo_root = Path(__file__).resolve().parents[1]
    for module_name in _TARGET_MODULES:
        rel = Path(*module_name.split(".")).with_suffix(".py")
        assert (repo_root / rel).is_file(), f"{module_name} not found at {rel}"
