"""The first-party ``operate-microduck`` skill, and the ``learn`` recipe for it.

Two halves that must not drift apart: the skill file that ships in this repo
(frontmatter shape included — ``type: command`` is load-bearing, the
culture/colleague backend's ``core.skill_loader`` silently skips a ``SKILL.md``
without it), and the "Authoring the operator skill" section ``microduck-cli
learn`` prints so an agent in another runtime can recreate it.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from microduck_cli.cli import main
from microduck_cli.cli._commands.learn import (
    SKILL_NAME,
    SKILL_PATH,
    SKILL_SECTIONS,
    _as_json_payload,
)

REPO_ROOT = Path(__file__).resolve().parent.parent
SKILL_FILE = REPO_ROOT / SKILL_PATH

_SECTION_TITLE = f"Authoring the operator skill ({SKILL_NAME})"


def _frontmatter(text: str) -> str:
    """The YAML frontmatter block of a ``SKILL.md``, without its fences."""
    assert text.startswith("---\n"), "SKILL.md must open with YAML frontmatter"
    _, block, _ = text.split("---\n", 2)
    return block


# --- the shipped skill ------------------------------------------------------


def test_skill_file_exists() -> None:
    assert SKILL_FILE.is_file(), f"missing first-party skill: {SKILL_PATH}"


def test_skill_frontmatter_declares_name_description_and_type() -> None:
    block = _frontmatter(SKILL_FILE.read_text(encoding="utf-8"))
    assert f"name: {SKILL_NAME}" in block
    assert "description:" in block
    # Load-bearing: core.skill_loader silently skips a SKILL.md without it.
    assert "type: command" in block


def test_skill_description_names_the_triggers() -> None:
    block = _frontmatter(SKILL_FILE.read_text(encoding="utf-8"))
    description = block.split("description:", 1)[1]
    for trigger in ("operate the duck", "open the simulation", "close the sim"):
        assert trigger in description


@pytest.mark.parametrize("section", SKILL_SECTIONS)
def test_skill_body_carries_every_recipe_section(section: str) -> None:
    body = SKILL_FILE.read_text(encoding="utf-8").split("---\n", 2)[2]
    assert f"## {section}" in body


# --- learn teaches how to create it -----------------------------------------


def test_learn_text_carries_the_recipe(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["learn"]) == 0
    out = capsys.readouterr().out
    assert _SECTION_TITLE in out
    assert SKILL_PATH in out
    assert "type: command" in out
    assert "Never overwrite an existing skill" in out
    for section in SKILL_SECTIONS:
        assert section in out


def test_learn_json_carries_the_recipe(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["learn", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    skill = payload["skills"][SKILL_NAME]
    assert skill["canonical_path"] == SKILL_PATH
    assert skill["first_party"] is True
    assert skill["origin"] == "microduck-cli"
    assert skill["recipe"]["frontmatter"]["type"] == "command"
    assert skill["recipe"]["scripts"] is False
    assert skill["recipe"]["sections"] == SKILL_SECTIONS
    assert any("Never overwrite" in rule for rule in skill["consent"])


def test_learn_still_satisfies_the_rubric(capsys: pytest.CaptureFixture[str]) -> None:
    """The rubric strings the added section must not displace."""
    assert main(["learn"]) == 0
    out = capsys.readouterr().out
    assert len(out) >= 200
    assert "Purpose" in out
    assert "Commands" in out
    assert "Exit-code policy" in out
    assert "--json" in out
    assert "explain" in out


def test_json_payload_is_serialisable() -> None:
    assert json.loads(json.dumps(_as_json_payload()))["skills"][SKILL_NAME]
