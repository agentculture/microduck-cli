"""Tests for microduck_cli.trace.events — the run-trace event schema.

Covers plan task t1's acceptance criteria: a fixture events.jsonl round-trips
through load()/append() unchanged, a line missing 'lane' is refused with a
CliError naming the line number, import_run copies events + shots and
rewrites /home/<user> to ~ in labels, and no third-party import lives under
microduck_cli/trace (pyproject dependencies stays []).
"""

from __future__ import annotations

import ast
import json
import os
import sys
from pathlib import Path

import pytest

from microduck_cli.cli._errors import CliError
from microduck_cli.trace.events import (
    KINDS,
    LANES,
    Event,
    RunDir,
    append_event,
    import_run,
    load_events,
    new_run_dir,
    remove_empty_run_dir,
    validate_line,
)

FIXTURES = Path(__file__).parent / "fixtures"


def _run_dir(tmp_path: Path) -> RunDir:
    run_dir = RunDir(tmp_path / "a-run")
    run_dir.ensure()
    return run_dir


# --------------------------------------------------------------------------- #
# round trip                                                                  #
# --------------------------------------------------------------------------- #


def test_fixture_round_trips_through_load_and_append(tmp_path):
    fixture = FIXTURES / "trace_events_valid.jsonl"
    original_bytes = fixture.read_bytes()

    run_dir = _run_dir(tmp_path)
    run_dir.events_path.write_bytes(original_bytes)

    events = load_events(run_dir)
    assert len(events) == 5
    assert all(isinstance(event, Event) for event in events)

    # Re-append every loaded event into a fresh run dir and compare bytes.
    replay_dir = RunDir(tmp_path / "replay")
    replay_dir.ensure()
    for event in events:
        append_event(replay_dir, event)

    assert replay_dir.events_path.read_bytes() == original_bytes


def test_event_to_json_round_trips_through_from_dict():
    obj = {
        "t": 1.5,
        "wall": 100.5,
        "lane": "cli",
        "kind": "cmd-start",
        "label": "microduck env up",
        "step": "01-env-up",
    }
    event = Event.from_dict(obj)
    assert event.to_json() == obj


def test_append_event_writes_compact_sorted_json_line(tmp_path):
    run_dir = _run_dir(tmp_path)
    event = Event(t=0.0, wall=0.0, lane="operator", kind="note", label="hello")
    append_event(run_dir, event)
    line = run_dir.events_path.read_text(encoding="utf-8")
    assert line == '{"kind":"note","label":"hello","lane":"operator","t":0.0,"wall":0.0}\n'
    parsed = json.loads(line)
    assert parsed == {"t": 0.0, "wall": 0.0, "lane": "operator", "kind": "note", "label": "hello"}


# --------------------------------------------------------------------------- #
# validation                                                                  #
# --------------------------------------------------------------------------- #


def test_line_missing_lane_is_refused_naming_line_number():
    fixture = FIXTURES / "trace_events_missing_lane.jsonl"
    lines = fixture.read_text(encoding="utf-8").splitlines()
    obj = json.loads(lines[1])

    with pytest.raises(CliError) as exc_info:
        validate_line(obj, 2)

    error = exc_info.value
    assert "2" in error.message
    assert "lane" in error.message


def test_load_events_raises_on_first_bad_line(tmp_path):
    run_dir = _run_dir(tmp_path)
    fixture = FIXTURES / "trace_events_missing_lane.jsonl"
    run_dir.events_path.write_bytes(fixture.read_bytes())

    with pytest.raises(CliError) as exc_info:
        load_events(run_dir)

    assert "2" in exc_info.value.message


def test_validate_line_rejects_unknown_kind():
    obj = {"t": 0.0, "wall": 0.0, "lane": "operator", "kind": "bogus", "label": "x"}
    with pytest.raises(CliError):
        validate_line(obj, 1)


def test_validate_line_rejects_unknown_lane():
    obj = {"t": 0.0, "wall": 0.0, "lane": "bogus", "kind": "note", "label": "x"}
    with pytest.raises(CliError):
        validate_line(obj, 1)


def test_validate_line_rejects_non_numeric_t():
    obj = {"t": "soon", "wall": 0.0, "lane": "operator", "kind": "note", "label": "x"}
    with pytest.raises(CliError):
        validate_line(obj, 1)


def test_validate_line_rejects_non_numeric_wall():
    obj = {"t": 0.0, "wall": "soon", "lane": "operator", "kind": "note", "label": "x"}
    with pytest.raises(CliError):
        validate_line(obj, 1)


def test_validate_line_rejects_oversized_label():
    obj = {"t": 0.0, "wall": 0.0, "lane": "operator", "kind": "note", "label": "x" * 401}
    with pytest.raises(CliError):
        validate_line(obj, 1)


def test_lanes_and_kinds_are_the_documented_sets():
    assert LANES == (
        "operator",
        "cli",
        "engine",
        "robotd",
        "body",
        "train",
        "viewer",
        "checks",
        "microduck",
        "rl",
    )
    assert KINDS == ("cmd-start", "cmd-end", "stderr", "log", "shot", "note")


# --------------------------------------------------------------------------- #
# run-dir layout                                                              #
# --------------------------------------------------------------------------- #


def test_new_run_dir_naming_and_layout(tmp_path):
    now = 1_757_318_400.0  # 2025-09-08T08:00:00Z
    run_dir = new_run_dir(str(tmp_path), now)

    assert run_dir.path == tmp_path / "trace" / "20250908T080000Z"
    assert run_dir.path.is_dir()
    assert run_dir.steps.is_dir()
    assert run_dir.shots.is_dir()
    assert run_dir.meta_path.exists()
    assert run_dir.read_meta() == {"t0_wall": now}


def test_run_dir_write_and_read_meta_round_trip(tmp_path):
    run_dir = _run_dir(tmp_path)
    run_dir.write_meta({"t0_wall": 5.0, "title": "a run"})
    assert run_dir.read_meta() == {"t0_wall": 5.0, "title": "a run"}


def test_new_run_dir_same_second_yields_two_distinct_dirs(tmp_path):
    now = 1_757_318_400.0  # 2025-09-08T08:00:00Z

    first = new_run_dir(str(tmp_path), now)
    second = new_run_dir(str(tmp_path), now)

    assert first.path != second.path
    assert first.path == tmp_path / "trace" / "20250908T080000Z"
    assert second.path == tmp_path / "trace" / "20250908T080000Z-2"
    assert first.meta_path.exists()
    assert second.meta_path.exists()

    third = new_run_dir(str(tmp_path), now)
    assert third.path == tmp_path / "trace" / "20250908T080000Z-3"
    assert third.meta_path.exists()


# --------------------------------------------------------------------------- #
# import_run                                                                  #
# --------------------------------------------------------------------------- #


def test_import_run_copies_events_and_shots_and_redacts_home(tmp_path):
    src = FIXTURES / "trace_import_src"
    dst = RunDir(tmp_path / "imported")

    count = import_run(str(src), dst)

    assert count == 2
    events = load_events(dst)
    assert len(events) == 2
    assert "/home/spark" not in events[0].label
    assert "~/git/microduck-cli" in events[0].label

    copied_shot = dst.shots / "frame1.jpg"
    assert copied_shot.is_file()
    assert copied_shot.read_bytes() == (src / "shots" / "frame1.jpg").read_bytes()


def test_import_run_without_redact_home_keeps_original_label(tmp_path):
    src = FIXTURES / "trace_import_src"
    dst = RunDir(tmp_path / "imported-no-redact")

    import_run(str(src), dst, redact_home=False)

    events = load_events(dst)
    assert "/home/spark" in events[0].label


def test_import_run_writes_minimal_meta_when_source_has_none(tmp_path):
    src = FIXTURES / "trace_import_src"
    dst = RunDir(tmp_path / "imported-meta")

    import_run(str(src), dst)

    meta = dst.read_meta()
    first_event = load_events(dst)[0]
    assert meta["t0_wall"] == pytest.approx(first_event.wall - first_event.t)


def test_import_run_refuses_first_bad_line(tmp_path):
    src_dir = tmp_path / "bad-src"
    src_dir.mkdir()
    (src_dir / "events.jsonl").write_text(
        '{"t":0.0,"wall":0.0,"lane":"operator","kind":"note","label":"ok"}\n'
        '{"t":0.1,"wall":0.1,"kind":"note","label":"missing lane"}\n',
        encoding="utf-8",
    )
    dst = RunDir(tmp_path / "bad-dst")

    with pytest.raises(CliError) as exc_info:
        import_run(str(src_dir), dst)

    assert "2" in exc_info.value.message
    # Nothing should have been written on failure.
    assert not dst.events_path.exists()


def test_import_run_missing_events_file_raises_cli_error(tmp_path):
    src_dir = tmp_path / "empty-src"
    src_dir.mkdir()
    dst = RunDir(tmp_path / "empty-dst")

    with pytest.raises(CliError):
        import_run(str(src_dir), dst)


def test_import_run_refuses_source_equals_destination(tmp_path):
    src = FIXTURES / "trace_import_src"
    dst = RunDir(src)

    with pytest.raises(CliError) as exc_info:
        import_run(str(src), dst)

    error = exc_info.value
    assert "source and destination are the same directory" in error.message
    assert "--out" in error.remediation


def test_import_run_clears_stale_shots_in_existing_destination(tmp_path):
    src = FIXTURES / "trace_import_src"
    dst = RunDir(tmp_path / "existing-dst")
    dst.ensure()
    stray = dst.shots / "stale.jpg"
    stray.write_bytes(b"old frame")

    import_run(str(src), dst)

    assert not stray.exists()
    assert (dst.shots / "frame1.jpg").is_file()


def test_import_run_malformed_meta_raises_and_leaves_destination_untouched(tmp_path):
    src_dir = tmp_path / "bad-meta-src"
    src_dir.mkdir()
    (src_dir / "events.jsonl").write_text(
        '{"t":0.0,"wall":0.0,"lane":"operator","kind":"note","label":"ok"}\n',
        encoding="utf-8",
    )
    (src_dir / "meta.json").write_text("{not valid json", encoding="utf-8")

    dst = RunDir(tmp_path / "bad-meta-dst")
    dst.ensure()
    dst.events_path.write_text(
        '{"t":0.0,"wall":0.0,"lane":"operator","kind":"note","label":"pre-existing"}\n',
        encoding="utf-8",
    )
    original_events = dst.events_path.read_bytes()

    with pytest.raises(CliError) as exc_info:
        import_run(str(src_dir), dst)

    assert "meta.json" in exc_info.value.message
    assert dst.events_path.read_bytes() == original_events


def test_import_run_meta_not_an_object_raises_cli_error(tmp_path):
    src_dir = tmp_path / "list-meta-src"
    src_dir.mkdir()
    (src_dir / "events.jsonl").write_text(
        '{"t":0.0,"wall":0.0,"lane":"operator","kind":"note","label":"ok"}\n',
        encoding="utf-8",
    )
    (src_dir / "meta.json").write_text("[1, 2, 3]", encoding="utf-8")

    dst = RunDir(tmp_path / "list-meta-dst")

    with pytest.raises(CliError) as exc_info:
        import_run(str(src_dir), dst)

    assert "not a JSON object" in exc_info.value.message


def test_import_run_unreadable_events_raises_cli_error(tmp_path):
    src_dir = tmp_path / "dir-events-src"
    src_dir.mkdir()
    (src_dir / "events.jsonl").mkdir()  # a directory, not a file

    dst = RunDir(tmp_path / "dir-events-dst")

    with pytest.raises(CliError):
        import_run(str(src_dir), dst)


@pytest.mark.skipif(os.geteuid() == 0, reason="root bypasses file permissions")
def test_import_run_permission_denied_events_raises_cli_error(tmp_path):
    src_dir = tmp_path / "chmod-src"
    src_dir.mkdir()
    events_path = src_dir / "events.jsonl"
    events_path.write_text(
        '{"t":0.0,"wall":0.0,"lane":"operator","kind":"note","label":"ok"}\n',
        encoding="utf-8",
    )
    events_path.chmod(0o000)
    dst = RunDir(tmp_path / "chmod-dst")

    try:
        with pytest.raises(CliError):
            import_run(str(src_dir), dst)
    finally:
        events_path.chmod(0o644)


def test_import_run_redacts_home_in_meta_when_src_has_meta(tmp_path):
    src_dir = tmp_path / "meta-redact-src"
    src_dir.mkdir()
    (src_dir / "events.jsonl").write_text(
        '{"t":0.0,"wall":0.0,"lane":"operator","kind":"note","label":"ok"}\n',
        encoding="utf-8",
    )
    (src_dir / "meta.json").write_text(
        json.dumps({"t0_wall": 0.0, "note": "seen at /home/someone/work"}),
        encoding="utf-8",
    )
    dst = RunDir(tmp_path / "meta-redact-dst")

    import_run(str(src_dir), dst)

    meta = dst.read_meta()
    assert "/home/someone" not in meta["note"]
    assert "~/work" in meta["note"]


def test_import_run_without_redact_home_keeps_meta_verbatim(tmp_path):
    src_dir = tmp_path / "meta-noredact-src"
    src_dir.mkdir()
    (src_dir / "events.jsonl").write_text(
        '{"t":0.0,"wall":0.0,"lane":"operator","kind":"note","label":"ok"}\n',
        encoding="utf-8",
    )
    (src_dir / "meta.json").write_text(
        json.dumps({"t0_wall": 0.0, "note": "seen at /home/someone/work"}),
        encoding="utf-8",
    )
    dst = RunDir(tmp_path / "meta-noredact-dst")

    import_run(str(src_dir), dst, redact_home=False)

    meta = dst.read_meta()
    assert "/home/someone" in meta["note"]


# --------------------------------------------------------------------------- #
# remove_empty_run_dir                                                        #
# --------------------------------------------------------------------------- #


def test_remove_empty_run_dir_removes_a_fresh_new_run_dir(tmp_path):
    run_dir = new_run_dir(str(tmp_path), 1_757_318_400.0)

    removed = remove_empty_run_dir(run_dir)

    assert removed is True
    assert not run_dir.path.exists()


def test_remove_empty_run_dir_refuses_one_with_events(tmp_path):
    run_dir = new_run_dir(str(tmp_path), 1_757_318_400.0)
    append_event(run_dir, Event(t=0.0, wall=0.0, lane="operator", kind="note", label="hello"))

    removed = remove_empty_run_dir(run_dir)

    assert removed is False
    assert run_dir.path.exists()
    assert run_dir.events_path.exists()


def test_remove_empty_run_dir_refuses_nonexistent_dir(tmp_path):
    run_dir = RunDir(tmp_path / "does-not-exist")

    removed = remove_empty_run_dir(run_dir)

    assert removed is False


# --------------------------------------------------------------------------- #
# no third-party imports under microduck_cli/trace                           #
# --------------------------------------------------------------------------- #


def _stdlib_module_names() -> set[str]:
    names = set(sys.stdlib_module_names)
    names.add("__future__")
    return names


def test_no_third_party_imports_under_trace_package():
    package_root = Path(__file__).parent.parent / "microduck_cli" / "trace"
    stdlib = _stdlib_module_names()
    violations = []

    for path in sorted(package_root.rglob("*.py")):
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                modules = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                if node.level and node.level > 0:
                    continue  # relative import within microduck_cli.trace
                modules = [node.module] if node.module else []
            else:
                continue
            for module in modules:
                root = module.split(".")[0]
                if root == "microduck_cli":
                    continue
                if root not in stdlib:
                    violations.append(f"{path.relative_to(package_root.parent.parent)}: {module}")

    assert not violations, f"third-party imports found under trace/: {violations}"


def test_pyproject_dependencies_stays_empty():
    pyproject = Path(__file__).parent.parent / "pyproject.toml"
    text = pyproject.read_text(encoding="utf-8")
    assert "dependencies = []" in text
