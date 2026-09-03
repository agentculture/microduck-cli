"""Sense logging (t12): one fixed stderr line per event, never stdout, never doubled."""

from __future__ import annotations

import logging

import pytest

from microduck_cli.behavior import senselog


@pytest.fixture(autouse=True)
def _clean_logger():
    """Start every test from a logger with no handlers, and leave none behind."""
    root = logging.getLogger(senselog.ROOT_LOGGER_NAME)
    saved = list(root.handlers)
    root.handlers.clear()
    yield
    root.handlers.clear()
    root.handlers.extend(saved)


def test_stage_emits_exactly_one_line_in_the_fixed_format(capsys):
    senselog.install_logging()
    senselog.stage("ipc", "robotd", "state", "subscribed")
    captured = capsys.readouterr()
    assert captured.err == "[SENSE stage=ipc source=robotd event=state] subscribed\n"
    assert captured.out == ""


def test_drop_puts_the_reason_in_the_event_slot_and_names_it_in_the_detail(capsys):
    senselog.install_logging()
    senselog.drop("ipc", "robotd", "ipc-queue-full", "outbound queue at 64")
    line = capsys.readouterr().err.strip()
    assert line == (
        "[SENSE stage=ipc source=robotd event=ipc-queue-full] "
        "dropped reason=ipc-queue-full: outbound queue at 64"
    )


def test_drop_without_detail_still_names_the_reason(capsys):
    senselog.install_logging()
    senselog.drop("rule", "fallen", "cooldown")
    line = capsys.readouterr().err.strip()
    assert line == "[SENSE stage=rule source=fallen event=cooldown] dropped reason=cooldown"


def test_repeated_install_logging_never_duplicates_a_line(capsys):
    first = senselog.install_logging()
    second = senselog.install_logging()
    third = senselog.install_logging(logging.DEBUG)
    assert first is second is third
    root = logging.getLogger(senselog.ROOT_LOGGER_NAME)
    assert len(root.handlers) == 1

    senselog.stage("tick", "engine", "started", "50 Hz")
    captured = capsys.readouterr()
    assert captured.err.count("[SENSE") == 1
    assert captured.out == ""


def test_nothing_is_written_without_install_logging(capsys):
    senselog.stage("tick", "engine", "quiet", "no handler installed")
    captured = capsys.readouterr()
    assert captured.out == ""
    # No handler installed by us; logging must not fall through to stdout.
    assert "[SENSE" not in captured.out


def test_install_logging_disables_propagation_so_a_root_handler_cannot_echo(capsys):
    senselog.install_logging()
    assert logging.getLogger(senselog.ROOT_LOGGER_NAME).propagate is False


def test_lines_follow_a_swapped_stderr(capsys):
    """The handler resolves ``sys.stderr`` at emit time, so capture always works."""
    senselog.install_logging()
    senselog.stage("tick", "engine", "one", "first capture")
    assert "first capture" in capsys.readouterr().err
    senselog.stage("tick", "engine", "two", "second capture")
    assert "second capture" in capsys.readouterr().err
