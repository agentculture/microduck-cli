"""Tests for the motion gate helper (microduck_cli/duck/gate.py)."""

from __future__ import annotations

import os
import pty

import pytest

from microduck_cli.duck.gate import (
    HINT_APPLY,
    SAFETY_COMMUNITY_POLICY,
    SAFETY_INIT,
    SAFETY_RELAX,
    SAFETY_STOP,
    Consent,
    confirm_on_tty,
    consent,
    render_dry_run,
)

# ---------------------------------------------------------------------------
# consent() tri-state
# ---------------------------------------------------------------------------


def test_consent_apply_true_is_apply_regardless_of_tty() -> None:
    assert consent(apply=True, stdin_isatty=True) is Consent.APPLY
    assert consent(apply=True, stdin_isatty=False) is Consent.APPLY


def test_consent_non_tty_without_apply_is_dry_run() -> None:
    assert consent(apply=False, stdin_isatty=False) is Consent.DRY_RUN


def test_consent_tty_without_apply_is_prompt() -> None:
    assert consent(apply=False, stdin_isatty=True) is Consent.PROMPT


def test_consent_tty_via_real_pty() -> None:
    """A pty-based test: stdin is a genuine tty (not a mocked isatty()).

    Uses the stdlib ``pty`` module to open a real pseudo-terminal pair and
    swaps it in for ``sys.stdin`` for the duration of the call, so
    ``consent(apply=False)`` (default ``stdin_isatty=None``) resolves via a
    real ``sys.stdin.isatty()`` call against an actual tty, not a fake.
    """
    import sys

    primary_fd, secondary_fd = pty.openpty()
    try:
        assert os.isatty(secondary_fd)
        with os.fdopen(secondary_fd, "r", closefd=True) as tty_stdin:
            old_stdin = sys.stdin
            sys.stdin = tty_stdin
            try:
                assert sys.stdin.isatty()
                result = consent(apply=False)
            finally:
                sys.stdin = old_stdin
        assert result is Consent.PROMPT

        # With --apply, the same tty yields APPLY, not PROMPT.
        primary_fd2, secondary_fd2 = pty.openpty()
        try:
            with os.fdopen(secondary_fd2, "r", closefd=True) as tty_stdin2:
                old_stdin = sys.stdin
                sys.stdin = tty_stdin2
                try:
                    result_apply = consent(apply=True)
                finally:
                    sys.stdin = old_stdin
            assert result_apply is Consent.APPLY
        finally:
            os.close(primary_fd2)
    finally:
        os.close(primary_fd)


def test_consent_non_tty_via_pipe() -> None:
    """A real, non-tty stdin (a pipe) resolves to DRY_RUN without --apply."""
    import sys

    read_fd, write_fd = os.pipe()
    try:
        os.close(write_fd)
        with os.fdopen(read_fd, "r", closefd=True) as pipe_stdin:
            assert not os.isatty(pipe_stdin.fileno())
            old_stdin = sys.stdin
            sys.stdin = pipe_stdin
            try:
                result = consent(apply=False)
            finally:
                sys.stdin = old_stdin
        assert result is Consent.DRY_RUN
    finally:
        pass


# ---------------------------------------------------------------------------
# confirm_on_tty()
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("answer", ["y", "yes", "Y", "YES", " yes \n"])
def test_confirm_on_tty_accepts_explicit_yes(answer: str) -> None:
    assert confirm_on_tty("proceed?", input_fn=lambda _: answer) is True


@pytest.mark.parametrize("answer", ["n", "no", "", "sure", "ye", "yess"])
def test_confirm_on_tty_rejects_anything_else(answer: str) -> None:
    assert confirm_on_tty("proceed?", input_fn=lambda _: answer) is False


def test_confirm_on_tty_eof_is_false() -> None:
    def raise_eof(_: str) -> str:
        raise EOFError

    assert confirm_on_tty("proceed?", input_fn=raise_eof) is False


def test_confirm_on_tty_writes_question_to_stderr(capsys: pytest.CaptureFixture[str]) -> None:
    confirm_on_tty("Really do it?", input_fn=lambda _: "y")
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "Really do it?" in captured.err


# ---------------------------------------------------------------------------
# render_dry_run(): safety sentences and zero-side-effect framing
# ---------------------------------------------------------------------------


def test_render_dry_run_init_contains_exact_safety_sentence() -> None:
    text = render_dry_run(
        {
            "verb": "init",
            "target": "duck-a",
            "socket": "/tmp/duck-a.sock",
            "calls": ["robot.init"],
            "apply_command": "microduck duck init duck-a --apply",
        }
    )
    assert SAFETY_INIT in text
    assert "duck-a" in text
    assert "robot.init" in text
    assert "microduck duck init duck-a --apply" in text
    assert "No sockets opened, no calls sent" in text


def test_render_dry_run_relax_contains_exact_safety_sentence() -> None:
    text = render_dry_run({"verb": "relax", "target": "duck-a", "calls": ["robot.relax"]})
    assert SAFETY_RELAX in text


def test_render_dry_run_stop_contains_exact_safety_sentence() -> None:
    text = render_dry_run({"verb": "stop", "target": "duck-a", "calls": ["robot.stop"]})
    assert SAFETY_STOP in text


def test_render_dry_run_community_policy_contains_exact_safety_sentence() -> None:
    text = render_dry_run(
        {
            "verb": "policy-install",
            "target": "duck-a",
            "calls": ["robot.loadPolicy"],
        }
    )
    assert SAFETY_COMMUNITY_POLICY in text


def test_render_dry_run_never_lists_a_real_call_without_verb_context() -> None:
    text = render_dry_run({"verb": "init"})
    assert "?" in text  # missing target renders as "?" rather than KeyError
    assert "(none)" in text  # missing calls renders as an explicit "none"


def test_render_dry_run_unknown_verb_falls_back_to_community_policy_sentence() -> None:
    text = render_dry_run({"verb": "mystery-verb"})
    assert SAFETY_COMMUNITY_POLICY in text


# ---------------------------------------------------------------------------
# hint string
# ---------------------------------------------------------------------------


def test_hint_apply_mentions_apply_flag() -> None:
    assert "--apply" in HINT_APPLY
