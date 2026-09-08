"""Tests for docs/traces/tutorial.sidecar.toml.

Asserts the sidecar parses as TOML and that, merged against a small embedded
tutorial fixture carrying three of its exact command strings, every sidecar
key aimed at those commands matches — no unmatched entries. This does not
depend on the Jetson AI Lab fork's path; the full-tutorial match is verified
manually (see docs/plans/2026-09-08-run-trace-tool.md task t9).
"""

from __future__ import annotations

import tomllib
from pathlib import Path

from microduck_cli.trace.plan import dump_plan, from_tutorial, load_plan

SIDECAR_PATH = Path(__file__).resolve().parents[1] / "docs" / "traces" / "tutorial.sidecar.toml"

# Three exact command strings lifted verbatim from the sidecar, embedded in a
# minimal fenced-bash fixture so this test does not depend on the fork's mdx.
FIXTURE_TUTORIAL = """\
# Tutorial fixture

## Step 6: Bring up the sim

```bash
microduck env up --sim
```

## Step 7: Stand the duck up

```bash
microduck duck init --apply
```

## Step 10: Check memory

```bash
free -g
```
"""


def test_sidecar_parses_as_toml() -> None:
    text = SIDECAR_PATH.read_text(encoding="utf-8")
    data = tomllib.loads(text)
    assert isinstance(data, dict)
    assert "microduck env up --sim" in data
    assert "microduck duck init --apply" in data
    assert "free -g" in data


def test_sidecar_matches_three_embedded_commands() -> None:
    text = SIDECAR_PATH.read_text(encoding="utf-8")
    sidecar = tomllib.loads(text)

    plan = from_tutorial(FIXTURE_TUTORIAL, sidecar=sidecar)

    # The three commands in the fixture must all have matched: none of them
    # appear in meta["unmatched"].
    unmatched = set(plan.meta.get("unmatched", []))
    for cmd in ("microduck env up --sim", "microduck duck init --apply", "free -g"):
        assert cmd not in unmatched

    by_cmd = {step.cmd: step for step in plan.steps}

    env_up = by_cmd["microduck env up --sim"]
    assert env_up.retry == 1
    assert env_up.timeout_s == 200
    assert env_up.shot == "s0-after-env-up"
    assert env_up.sleep_after == 4

    duck_init = by_cmd["microduck duck init --apply"]
    assert duck_init.sleep_after == 8
    assert duck_init.shot == "s1-init"

    free_g = by_cmd["free -g"]
    assert free_g.lane == "operator"


def test_sidecar_merged_plan_survives_dump_and_reload() -> None:
    text = SIDECAR_PATH.read_text(encoding="utf-8")
    sidecar = tomllib.loads(text)

    plan = from_tutorial(FIXTURE_TUTORIAL, sidecar=sidecar)
    dumped = dump_plan(plan)
    reloaded = load_plan(dumped)

    assert [s.cmd for s in reloaded.steps] == [s.cmd for s in plan.steps]
