"""Guard + shared helper: sentinel secret values must never leak into output.

Exports :func:`assert_no_secrets`, imported by later tasks' tests that drive
the CLI with a planted sentinel env var / config value and need to assert it
never appears in captured stdout/stderr or in any subprocess argv.
"""

from __future__ import annotations


def assert_no_secrets(
    captured: str,
    argv_log: list[list[str]] | None = None,
    *,
    sentinels: dict[str, str],
) -> None:
    """Fail (raise AssertionError) if any sentinel value leaked.

    Args:
        captured: combined text to scan (e.g. captured stdout + stderr).
        argv_log: optional list of argv lists (e.g. every subprocess command
            that was run) to also scan for sentinel values.
        sentinels: mapping of sentinel name -> secret value that must never
            appear anywhere in ``captured`` or ``argv_log``.
    """
    for name, value in sentinels.items():
        if not value:
            continue
        assert (
            value not in captured
        ), f"sentinel {name!r} (value {value!r}) leaked into captured output"

    if argv_log:
        for argv in argv_log:
            for arg in argv:
                for name, value in sentinels.items():
                    if not value:
                        continue
                    assert (
                        value not in arg
                    ), f"sentinel {name!r} (value {value!r}) leaked into argv {argv!r}"


def test_assert_no_secrets_catches_planted_leak_in_captured_output() -> None:
    sentinels = {"api_key": "sk-super-secret-sentinel-123"}
    leaking_output = "starting up...\nAPI key = sk-super-secret-sentinel-123\ndone.\n"

    try:
        assert_no_secrets(leaking_output, sentinels=sentinels)
    except AssertionError:
        pass
    else:
        raise AssertionError("expected assert_no_secrets to catch the planted leak")


def test_assert_no_secrets_catches_planted_leak_in_argv() -> None:
    sentinels = {"token": "tok-sentinel-abc"}
    argv_log = [["duckctl", "--flag"], ["curl", "-H", "Authorization: tok-sentinel-abc"]]

    try:
        assert_no_secrets("clean output, nothing here", argv_log, sentinels=sentinels)
    except AssertionError:
        pass
    else:
        raise AssertionError("expected assert_no_secrets to catch the leak in argv")


def test_assert_no_secrets_passes_on_clean_capture() -> None:
    sentinels = {"api_key": "sk-super-secret-sentinel-123", "token": "tok-sentinel-abc"}
    clean_output = "starting up...\nAPI key = <redacted>\ndone.\n"
    argv_log = [["duckctl", "--flag"], ["curl", "-H", "Authorization: <redacted>"]]

    assert_no_secrets(clean_output, argv_log, sentinels=sentinels)
