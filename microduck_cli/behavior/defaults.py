"""The shipped default rules — a package resource, read once through ``importlib.resources``.

``microduck_cli/behavior/default_rules.toml`` ships inside the wheel as
ordinary package data (see the note in ``pyproject.toml``'s
``[tool.hatch.build.targets.wheel]`` on why no extra include rule was needed).
This module is the single place that reads it: :func:`load_shipped_rules`
parses it through the SAME gate every other rules file goes through
(:meth:`~microduck_cli.behavior.rules.RulesConfig.from_dict` via
:func:`~microduck_cli.behavior.rules.load_rules`'s sibling below), so a
malformed shipped file fails exactly like a malformed operator file would —
never a silently-different code path for "our own" TOML.

This module performs exactly one read (the resource text) and one parse; it
opens no other file, no socket, and imports nothing from
:mod:`microduck_cli.cli` beyond the shared error type propagated by
:mod:`microduck_cli.behavior.rules`.
"""

from __future__ import annotations

import tomllib
from importlib import resources

from microduck_cli.behavior.rules import RulesConfig

__all__ = ["SHIPPED_RESOURCE", "load_shipped_rules", "shipped_rule_ids"]

#: The resource file name, relative to this package — the single name every
#: caller (this module, ``pyproject.toml`` documentation, tests) should use.
SHIPPED_RESOURCE = "default_rules.toml"


def _shipped_text() -> str:
    """Read the packaged resource's raw text, however it is installed."""
    return resources.files(__package__).joinpath(SHIPPED_RESOURCE).read_text(encoding="utf-8")


def load_shipped_rules() -> RulesConfig:
    """Parse + validate the shipped ``default_rules.toml`` into a :class:`RulesConfig`.

    Goes through the exact same :meth:`RulesConfig.from_dict` gate as any
    operator-authored rules file — this module adds no leniency of its own.
    A malformed shipped resource is a packaging bug and is reported the same
    way a malformed box-local file would be: a :class:`~microduck_cli.cli._errors.CliError`.
    """
    data = tomllib.loads(_shipped_text())
    return RulesConfig.from_dict(data)


def shipped_rule_ids() -> tuple[str, ...]:
    """The ids of every rule (react + inhibit) the shipped file defines, in file order."""
    config = load_shipped_rules()
    return tuple(rule.id for rule in (*config.react, *config.inhibit))
