# microduck-cli

Agent-agnostic CLI for controlling the MicroDuck robot. Any agent, any human,
one CLI. Built on the [neurosymbolic-system](https://github.com/agentculture/neurosymbolic-system)
runtime and following the
[reachy-mini-cli](https://github.com/agentculture/reachy-mini-cli) architecture.

## What ships today

The agent-first introspection CLI (`whoami`, `learn`, `explain`, `overview`,
`doctor`, `cli overview`) plus four domain nouns with real action verbs, all
exercised against [`pollen-robotics/microduck`](https://github.com/pollen-robotics/microduck)'s
own simulator and fake daemon:

| Noun | Verbs |
|------|-------|
| `env` | `overview`, `doctor`, `up`, `down`, `status`, `hosts` — bring up / doctor / tear down the simulator (`duck-body` + `robotd --sim`) or a fake `robotd --fake` stand-in. |
| `duck` | `overview`, `health`, `version`, `monitor`, `init`, `relax`, `enable`, `do`, `mode`, `look`, `stop`, `move`, `quack`, `configure`, `record` — operate one duck directly, in `robotctl`'s own words. |
| `policy` | `overview`, `list`, `load`, `reset`, `add`, `remove`, `search`, `check`, `update`, `pad bindings/bind/reset`, `smoke`, `train`, `play`, `export`, `publish`, `infer`, `install` — the policy lifecycle plus the `microduck_rl` train/smoke/export/publish/infer lane. |
| `rules` | `overview`, `list`, `check`, `engine overview/run/start/stop/status`, `intent` — the data-only rules layer and the one 50 Hz tick engine that evaluates it. |

**Sim-first, and it stays that way until t23 verifies on real hardware: no
physical MicroDuck has been driven from this CLI yet.** Every verb above is
built and tested against `robotd --fake`/`--sim` and the in-process fake
daemon (`tests/fake_robotd.py`) — see
[`docs/operating-the-duck.md`](docs/operating-the-duck.md) for the six-command
walkthrough and [`docs/upstream-pins.md`](docs/upstream-pins.md) for the
exact upstream commits this CLI is validated against.

**Approved deviation d1** — `policy`'s `robot.policies` / `robot.loadPolicy` /
`robot.setSkill` channel needs a daemon reporting API >= 18
(`pollen-robotics/microduck` `main`); the pinned `sim-remote-io` build answers
API 16 and has no policy channel at all, so those verbs report that plainly
and fall back to `robot.subscribe` where they can (see `policy` module
docstring for the full d1 note).

The `neurosymbolic-system` runtime this CLI is meant to import (senses,
rules, arbitration and motion composed onto one 50 Hz tick) is itself still a
bare scaffold, so it is **not** a dependency of this package yet — the tick
engine lives in `microduck_cli/behavior/` in the meantime, written behind the
seams the eventual extraction needs (see [`CLAUDE.md`](CLAUDE.md)).

## What you get

- **An agent-first CLI** cited from [teken](https://github.com/agentculture/teken)
  (`afi-cli`) — the runtime package has **zero third-party runtime
  dependencies** (`dependencies = []`); `teken`, pytest and the lint stack are
  dev-only.
- **A mesh identity** — `culture.yaml` (`suffix` + `backend`) and the matching
  prompt file (`AGENTS.colleague.md` for this agent's `backend: colleague`).
- **The vendored guildmaster skill kit** under `.claude/skills/`, cite-don't-import.
  See [`docs/skill-sources.md`](docs/skill-sources.md).
- **A build + deploy baseline** — pytest, lint, the agent-first rubric gate, and
  PyPI Trusted Publishing wired into GitHub Actions.

## Quickstart

Both console scripts install — **`microduck`** (short) and **`microduck-cli`**
(the distribution name, and the prog name the CLI prints in its own output).
They are the same entry point, so either works everywhere below:

```bash
uv sync
uv run pytest -n auto                 # run the test suite
uv run microduck whoami               # identity from culture.yaml
uv run microduck learn                # self-teaching prompt (add --json)
uv run teken cli doctor . --strict    # the agent-first rubric gate CI runs
```

## Try it in simulation

No physical duck required — this brings up `robotd --fake` and walks it
through a rules-engine tick. See
[`docs/operating-the-duck.md`](docs/operating-the-duck.md) for the full
walkthrough (each command's exact output, and what to do when a check fails).

```bash
uv run microduck env doctor            # is this box ready? (clones, cargo, port, venv)
uv run microduck env up --fake         # bring up robotd --fake and wait for healthy
uv run microduck duck health           # ask the fake robot's own verdict on itself
uv run microduck rules engine run --max-ticks 50 --apply  # run the tick engine briefly
uv run microduck rules intent stop     # inject one intent through the ONE registry
uv run microduck env down              # tear the stack back down
```

Every command supports `--json`. Results go to stdout, errors/diagnostics to
stderr (never mixed). Exit codes: `0` success, `1` user error, `2` environment
error, `3+` reserved.

## Sibling projects

microduck-cli is built by composing three siblings rather than inventing a
fourth architecture:

| Repo | Role here |
|------|-----------|
| [`neurosymbolic-system`](https://github.com/agentculture/neurosymbolic-system) | The robot runtime to **import** once it ships — never re-implement the tick loop in this repo. |
| [`reachy-mini-cli`](https://github.com/agentculture/reachy-mini-cli) | The architecture to follow: noun groups, one tick seam for all senses, the single-SDK-owner model. |
| [`arm101-cli`](https://github.com/agentculture/arm101-cli) | The hardware-safety patterns: gated motion (`--apply` / dry-run / TTY confirm), release-on-abnormal-exit, hardware deps behind an extra. |

## Contributing

See [`CLAUDE.md`](CLAUDE.md) for the full conventions: the CLI contracts, what
to take from each sibling, version-bump-every-PR, the `cicd` PR lane, worktree
layout, and memory discipline.

## License

Apache 2.0 — see [`LICENSE`](LICENSE).
