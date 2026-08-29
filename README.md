# microduck-cli

Agent-agnostic CLI for controlling the MicroDuck robot. Any agent, any human,
one CLI. Built on the [neurosymbolic-system](https://github.com/agentculture/neurosymbolic-system)
runtime and following the
[reachy-mini-cli](https://github.com/agentculture/reachy-mini-cli) architecture.

## Status: scaffold

**There is no MicroDuck control code yet.** What ships today is the
AgentCulture mesh-agent baseline — an agent-first introspection CLI, a mesh
identity, the vendored guildmaster skill kit, and a build/CI/deploy pipeline.
The duck domain is the destination; the CLI below is the chassis it grows on.

The `neurosymbolic-system` runtime it is meant to import (senses, rules,
arbitration and motion on one 50 Hz tick) is itself still a scaffold, so it is
**not** a dependency of this package yet.

## What you get

- **An agent-first CLI** cited from [teken](https://github.com/agentculture/teken)
  (`afi-cli`) — the runtime package has no third-party dependencies.
- **A mesh identity** — `culture.yaml` (`suffix` + `backend`) and the matching
  prompt file (`AGENTS.colleague.md` for this agent's `backend: colleague`).
- **The vendored guildmaster skill kit** under `.claude/skills/`, cite-don't-import.
  See [`docs/skill-sources.md`](docs/skill-sources.md).
- **A build + deploy baseline** — pytest, lint, the agent-first rubric gate, and
  PyPI Trusted Publishing wired into GitHub Actions.

## Quickstart

The installed console script is **`microduck`** (not `microduck-cli` — that is
the distribution name and the name the CLI prints in its own output):

```bash
uv sync
uv run pytest -n auto                 # run the test suite
uv run microduck whoami               # identity from culture.yaml
uv run microduck learn                # self-teaching prompt (add --json)
uv run teken cli doctor . --strict    # the agent-first rubric gate CI runs
```

## CLI

| Verb | What it does |
|------|--------------|
| `whoami` | Report this agent's nick, version, backend, and model from `culture.yaml`. |
| `learn` | Print a structured self-teaching prompt. |
| `explain <path>` | Markdown docs for any noun/verb path. |
| `overview` | Read-only descriptive snapshot of the agent. |
| `doctor` | Check the agent-identity invariants (prompt-file-present, backend-consistency). |
| `cli overview` | Describe the CLI surface itself. |

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
