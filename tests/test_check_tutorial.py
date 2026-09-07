"""Tests for docs/tools/check_tutorial.py.

Loaded by path (the script is not part of the installed package) via
importlib, per the task's stated import strategy.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

SCRIPT_PATH = Path(__file__).resolve().parents[1] / "docs" / "tools" / "check_tutorial.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("check_tutorial", SCRIPT_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    # Register before exec: dataclasses' annotation resolution needs the module
    # to be findable via sys.modules while the class body executes.
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


check_tutorial = _load_module()


MAIN_TUTORIAL = """\
# Tutorial

Some intro text mentioning /home/ori as an identity example line.

```bash
$ echo hello
```

```bash
foo bar \\
  --baz
```

```bash nocheck
this-is-not-checked --flag
```

```bash
# this is a comment
echo world
```
"""

MAIN_RECORD = """\
$ echo hello
some other stuff
$ foo bar   --baz
"""


def _write(path: Path, content: str) -> Path:
    path.write_text(content)
    return path


@pytest.fixture
def main_fixture(tmp_path):
    tutorial = _write(tmp_path / "tutorial.md", MAIN_TUTORIAL)
    record = _write(tmp_path / "record.txt", MAIN_RECORD)
    return tutorial, record


def test_extract_commands_hit_miss_continuation_prompt_nocheck_comment(main_fixture):
    tutorial, _record = main_fixture
    commands = check_tutorial.extract_commands(tutorial.read_text())
    assert commands == ["echo hello", "foo bar --baz", "echo world"]


def test_cli_reports_hit_and_miss(main_fixture, capsys):
    tutorial, record = main_fixture
    exit_code = check_tutorial.main([str(tutorial), "--record", str(record)])
    out = capsys.readouterr().out
    assert exit_code == 1
    assert "HIT" in out
    assert "echo hello" in out
    assert "foo bar --baz" in out
    assert "MISS" in out
    assert "echo world" in out
    # the nocheck fence's command must never appear at all
    assert "this-is-not-checked" not in out
    # the comment line itself must never be treated as a command
    assert "# this is a comment" not in out


def test_identity_hit_in_text(main_fixture, capsys):
    tutorial, record = main_fixture
    exit_code = check_tutorial.main([str(tutorial), "--record", str(record)])
    out = capsys.readouterr().out
    assert exit_code == 1
    assert "/home/" in out
    assert "identity" in out.lower()


def test_identity_hit_in_image_filename(tmp_path, capsys):
    tutorial = _write(tmp_path / "tutorial.md", "# Tutorial\n\nNo shell commands here.\n")
    record = _write(tmp_path / "record.txt", "")
    images_dir = tmp_path / "images"
    images_dir.mkdir()
    (images_dir / "secretuser_screenshot.png").write_bytes(b"\x89PNG\r\n\x1a\n" + b"0" * 10)
    (images_dir / "clean_screenshot.png").write_bytes(b"\x89PNG\r\n\x1a\n" + b"0" * 5)

    exit_code = check_tutorial.main(
        [
            str(tutorial),
            "--record",
            str(record),
            "--images-dir",
            str(images_dir),
            "--identity",
            "secretuser",
        ]
    )
    out = capsys.readouterr().out
    assert exit_code == 1
    assert "secretuser_screenshot.png" in out
    assert "IMAGE" in out
    assert "clean_screenshot.png" in out


def test_json_shape(main_fixture, capsys):
    tutorial, record = main_fixture
    exit_code = check_tutorial.main([str(tutorial), "--record", str(record), "--json"])
    out = capsys.readouterr().out
    assert exit_code == 1
    payload = json.loads(out)
    assert set(payload.keys()) == {"commands", "identity_hits", "images", "ok"}
    assert payload["ok"] is False
    assert len(payload["commands"]) == 3
    for entry in payload["commands"]:
        assert set(entry.keys()) == {"command", "hit", "record"}
    hits = {entry["command"]: entry for entry in payload["commands"]}
    assert hits["echo hello"]["hit"] is True
    assert hits["echo hello"]["record"] == str(record)
    assert hits["foo bar --baz"]["hit"] is True
    assert hits["echo world"]["hit"] is False
    assert hits["echo world"]["record"] is None
    assert len(payload["identity_hits"]) >= 1
    assert payload["identity_hits"][0]["source"] == str(tutorial)
    assert "line" in payload["identity_hits"][0]
    assert "text" in payload["identity_hits"][0]


def test_exit_code_zero_when_all_hit_and_no_identity(tmp_path, capsys):
    tutorial = _write(
        tmp_path / "clean.md",
        "# Clean tutorial\n\nNothing sensitive here.\n\n```bash\n$ echo hi\n```\n",
    )
    record = _write(tmp_path / "record.txt", "$ echo hi\n")
    exit_code = check_tutorial.main([str(tutorial), "--record", str(record)])
    out = capsys.readouterr().out
    assert exit_code == 0
    assert "MISS" not in out


def test_exit_code_one_on_miss(main_fixture):
    tutorial, record = main_fixture
    exit_code = check_tutorial.main([str(tutorial), "--record", str(record)])
    assert exit_code == 1


def test_exit_code_two_on_missing_tutorial(tmp_path, capsys):
    missing = tmp_path / "does-not-exist.md"
    record = _write(tmp_path / "record.txt", "echo hi\n")
    exit_code = check_tutorial.main([str(missing), "--record", str(record)])
    err = capsys.readouterr().err
    assert exit_code == 2
    assert err.strip() != ""


def test_exit_code_two_on_missing_record(tmp_path):
    tutorial = _write(tmp_path / "tutorial.md", "# T\n\n```bash\necho hi\n```\n")
    missing_record = tmp_path / "no-such-record.txt"
    exit_code = check_tutorial.main([str(tutorial), "--record", str(missing_record)])
    assert exit_code == 2


def test_multiple_records_first_match_reported(tmp_path):
    tutorial = _write(tmp_path / "tutorial.md", "```bash\necho hi\n```\n")
    record_a = _write(tmp_path / "a.txt", "nothing relevant\n")
    record_b = _write(tmp_path / "b.txt", "$ echo hi\n")
    exit_code = check_tutorial.main(
        [str(tutorial), "--record", str(record_a), "--record", str(record_b)]
    )
    assert exit_code == 0


def test_module_is_importable_as_script():
    # Ensure the module exposes main() for `python docs/tools/check_tutorial.py`.
    assert hasattr(check_tutorial, "main")
    assert callable(check_tutorial.main)


# --- Regression tests for the flattened-substring matching bug -------------------


def test_prefix_collision_not_a_hit(tmp_path):
    # A shorter tutorial command must not match as a prefix/fragment of a longer
    # recorded command.
    tutorial = _write(
        tmp_path / "tutorial.md",
        "```bash\n$ microduck duck init\n```\n",
    )
    record = _write(tmp_path / "record.txt", "$ microduck duck init --apply\n")
    checks = check_tutorial.check_commands(
        check_tutorial.extract_commands(tutorial.read_text()), [record]
    )
    assert len(checks) == 1
    assert checks[0].hit is False
    assert checks[0].record is None


def test_prose_occurrence_not_a_hit(tmp_path):
    # A record sentence mentioning the same words (prose, not a command) must not
    # match as a substring.
    tutorial = _write(
        tmp_path / "tutorial.md",
        "```bash\n$ microduck duck init\n```\n",
    )
    record = _write(
        tmp_path / "record.txt",
        "Before you can proceed, remember that microduck duck init is required.\n",
    )
    checks = check_tutorial.check_commands(
        check_tutorial.extract_commands(tutorial.read_text()), [record]
    )
    assert checks[0].hit is False


def test_cross_line_false_positive_not_a_hit(tmp_path):
    # Tokens split across two adjacent record lines must not combine into a hit.
    tutorial = _write(
        tmp_path / "tutorial.md",
        "```bash\n$ microduck duck init --apply\n```\n",
    )
    record = _write(
        tmp_path / "record.txt",
        "$ microduck duck\ninit --apply\n",
    )
    checks = check_tutorial.check_commands(
        check_tutorial.extract_commands(tutorial.read_text()), [record]
    )
    assert checks[0].hit is False


def test_dollar_prompt_entry_with_trailing_comment_hits(tmp_path):
    tutorial = _write(
        tmp_path / "tutorial.md",
        "```bash\n$ free -g\n```\n",
    )
    record = _write(tmp_path / "record.txt", "$ free -g   # 06:34\n")
    checks = check_tutorial.check_commands(
        check_tutorial.extract_commands(tutorial.read_text()), [record]
    )
    assert checks[0].hit is True
    assert checks[0].record == str(record)


def test_uv_run_prefix_normalization_hits(tmp_path):
    tutorial = _write(
        tmp_path / "tutorial.md",
        "```bash\n$ microduck env doctor\n```\n",
    )
    record = _write(tmp_path / "record.txt", "```bash\nuv run microduck env doctor\n```\n")
    checks = check_tutorial.check_commands(
        check_tutorial.extract_commands(tutorial.read_text()), [record]
    )
    assert checks[0].hit is True


def test_fenced_bash_record_entry_without_prompt_hits(tmp_path):
    # Real records (e.g. operating-the-duck.md, SKILL.md) paste raw shell inside a
    # ```bash fence with no `$ ` prompt at all.
    tutorial = _write(
        tmp_path / "tutorial.md",
        "```bash\n$ microduck rules intent stop\n```\n",
    )
    record = _write(
        tmp_path / "record.txt",
        "```bash\nmicroduck rules intent stop\n```\n",
    )
    checks = check_tutorial.check_commands(
        check_tutorial.extract_commands(tutorial.read_text()), [record]
    )
    assert checks[0].hit is True
