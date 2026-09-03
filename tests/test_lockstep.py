"""Docs-lockstep test: every registered verb must be documented in three places.

Adding a verb to the parser without (a) an explain catalog entry, (b) a line in
the canonical overview verb list, and (c) a mention in ``learn``'s text *and*
JSON payload makes the docs drift silently. :func:`lockstep_problems` walks the
built parser and reports every such gap, naming the offending verb path; the
tests below assert the real parser is clean and — by registering a throwaway
verb — that the checker actually catches a gap.
"""

from __future__ import annotations

import argparse

import pytest

from microduck_cli.cli import _build_parser
from microduck_cli.cli._commands.learn import _TEXT, _as_json_payload
from microduck_cli.cli._commands.overview import verb_lines
from microduck_cli.explain.catalog import ENTRIES, split_verb


def _subparser_actions(parser: argparse.ArgumentParser) -> list[argparse._SubParsersAction]:
    return [a for a in parser._actions if isinstance(a, argparse._SubParsersAction)]


def verb_paths(
    parser: argparse.ArgumentParser, prefix: tuple[str, ...] = ()
) -> list[tuple[str, ...]]:
    """Every command path registered under ``parser``, nouns and verbs alike."""
    paths: list[tuple[str, ...]] = []
    for action in _subparser_actions(parser):
        for name, child in action.choices.items():
            path = prefix + (name,)
            paths.append(path)
            paths.extend(verb_paths(child, path))
    return paths


def _line_covers(line: str, path: tuple[str, ...]) -> bool:
    tokens = split_verb(line)[0].split()
    return tuple(tokens[: len(path)]) == path


def lockstep_problems(parser: argparse.ArgumentParser) -> list[str]:
    """Documentation gaps for the verbs registered on ``parser``, named by path."""
    payload = _as_json_payload()
    json_paths = [tuple(entry["path"]) for entry in payload["commands"]]
    lines = verb_lines()
    problems: list[str] = []
    for path in verb_paths(parser):
        display = " ".join(path)
        if path not in ENTRIES:
            problems.append(f"{display}: no explain catalog entry (add it to explain/)")
        if not any(_line_covers(line, path) for line in lines):
            problems.append(f"{display}: no line in the overview verb list")
        if f"microduck-cli {display}" not in _TEXT:
            problems.append(f"{display}: not mentioned in the learn text")
        if not any(jp[: len(path)] == path for jp in json_paths):
            problems.append(f"{display}: not in the learn --json command list")
    return problems


def test_every_registered_verb_is_documented() -> None:
    problems = lockstep_problems(_build_parser())
    assert not problems, "docs out of lockstep:\n  " + "\n  ".join(problems)


def test_nouns_and_their_overviews_are_registered() -> None:
    paths = set(verb_paths(_build_parser()))
    for noun in ("env", "duck", "policy", "rules"):
        assert (noun,) in paths
        assert (noun, "overview") in paths


def test_checker_catches_an_undocumented_top_level_verb() -> None:
    parser = _build_parser()
    sub = _subparser_actions(parser)[0]
    p = sub.add_parser("zzz-throwaway", help="throwaway verb with no docs")
    p.set_defaults(func=lambda args: 0)
    problems = lockstep_problems(parser)
    assert any("zzz-throwaway" in problem for problem in problems)
    assert any("no explain catalog entry" in problem for problem in problems)


def test_checker_catches_an_undocumented_noun_verb() -> None:
    parser = _build_parser()
    env_parser = _subparser_actions(parser)[0].choices["env"]
    noun_sub = _subparser_actions(env_parser)[0]
    p = noun_sub.add_parser("zzz-verb", help="throwaway env verb with no docs")
    p.set_defaults(func=lambda args: 0)
    problems = lockstep_problems(parser)
    assert any(problem.startswith("env zzz-verb:") for problem in problems)


@pytest.mark.parametrize("noun", ["env", "duck", "policy", "rules"])
def test_noun_overview_never_hard_fails_on_stray_positional(
    noun: str, capsys: pytest.CaptureFixture[str]
) -> None:
    from microduck_cli.cli import main

    rc = main([noun, "overview", "/no/such/path/here"])
    assert rc == 0
    assert capsys.readouterr().out.strip()
