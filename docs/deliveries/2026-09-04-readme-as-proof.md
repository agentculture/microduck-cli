# Delivery Summary — README as proof

plan: `readme-as-proof` · run: `complete` · date: `2026-09-04`
baseline: `devague plan (hand-assembled)`

## Intent

Rewrite `README.md` from a pitch into a record: a diagram of what the CLI
actually sits between, command blocks in place of prose, verbatim captures from
the three on-box verification runs, an explicit statement of what has *not* been
verified, and every upstream and sibling repo cited. Driven through the devague
front half — `/scope`, `/think`, `/challenge` — with the converged spec at
[`docs/specs/2026-09-04-readme-as-proof.md`](../specs/2026-09-04-readme-as-proof.md).

## Planned Work

**This run had no plan leg at execution time.** At the user's direction after
`/think`, it went spec → build directly, skipping `/spec-to-plan` and
`/assign-to-workforce` (no fan-out, no worktrees, one agent). The plan below was
seeded *after* execution, as the container the delivery ledger requires, and its
tasks describe what was actually done rather than what was contracted in
advance. They are recorded as `proposed` and were never confirmed as a contract —
so the honest baseline for drift in this run is the **frame's confirmed claims**,
not these tasks.

- `t1` — Rewrite README.md to the confirmed section order with both diagrams,
  command-first sections, and the numbered walkthrough as the PyPI text fallback
- `t2` — Paste the live-proof captures from the three verification records,
  scrubbed of identity and stamped with box and CLI commit
- `t3` — Write the what-it-is-not and Not verified sections, and the two citation
  tables
- `t4` — Run the validation checks agent-side: commands vs `--help`, captures vs
  records, identity grep, links, markdownlint, readme_renderer, and the repo gates
- `t5` — Version bump, CHANGELOG entry, commit, and open the PR through the cicd lane

The contract that *was* confirmed in advance is the frame: 29 claims, 23 honesty
conditions, two resolved hard questions, adjudicated by the user at the spec gate.

## Actual Delivery

| Plan task | Status | What actually landed |
|-----------|--------|----------------------|
| `t1` | delivered | `README.md` rewritten to the confirmed section order; two mermaid fences (topology, tick engine); six-step numbered walkthrough as the PyPI fallback. Commit `6501d27`, amended by `2777532`. |
| `t2` | delivered | Four `text` capture blocks from the three verification records, home directories shortened to `~`, no capture carrying an account name quoted. |
| `t3` | delivered, amended | What-it-is-not, Not verified, and the two citation tables. Amended by `d1`/`d2`: neurosymbolic-system removed, the simulation put first. |
| `t4` | delivered | Nine checks run agent-side (see Evidence); 16 evidence records filed, one of them a genuine failure. |
| `t5` | delivered | 0.9.3 → 0.9.4, CHANGELOG entry, PR [#7](https://github.com/agentculture/microduck-cli/pull/7) opened through the `cicd` lane; all CI checks green. |

## Mid-work Decisions

- `d1` — every mention of neurosymbolic-system removed from `README.md`,
  contradicting confirmed claims `c9` (five limits, one being the
  not-a-dependency statement) and `c10` (the sibling table) — *"no need to
  mention neurosymbolic-system at all"*.
- `d2` — the what-it-is-not opener and walkthrough step 1 now lead with the
  MuJoCo simulation (the real `robotd --sim` driving `microduck_rl`'s
  `duck-body`) rather than `robotd --fake` — *"We ran it through the simulation,
  not just fake. So it was validated against that."*
- `d3` — the tick-engine diagram redrawn from a wide `flowchart LR` into a
  vertical `flowchart TB` of four labelled layers; the topology diagram reverted
  to and left exactly as committed, at the user's explicit direction. Both fences
  were rendered and checked at mermaid.live v11.17.2.
- `d4` — `pyproject.toml`'s description rewritten to drop neurosymbolic-system,
  because it is the subtitle PyPI renders directly above the README. Extends `d1`
  beyond `README.md` and widens boundary `c12` by one line.
- **Not covered by any deviation record:** the plan and `/assign-to-workforce`
  legs were skipped entirely (see Planned Work). This was a user direction at the
  `/think` hand-off, taken before a plan existed to deviate from.
- **Not covered by any deviation record:** `q1` was resolved *against* devague's
  own README decision (*"no console captures, at most one command per bash
  block"*). Recorded as claim `c18` at spec time, not as drift.

## Drift From Plan

| Plan item | Reason for divergence | Classification |
|-----------|-----------------------|----------------|
| `t3` (`d1`) | User direction mid-run: no need to mention neurosymbolic-system at all. The section states four of the five limits `h7` enumerated, and one of those four (zero runtime dependencies) now lives in the intro paragraph rather than the section. | acceptable |
| `t3` (`d2`) | User direction mid-run: the prior wording listed the fake body first and read as if only the fake was exercised. | acceptable |
| `t1` (`d3`) | User direction mid-run: the tick-engine diagram was too long to read and appeared small. | acceptable |
| `t3` (`d4`) | User direction after the validation report: the PyPI subtitle would otherwise contradict the README on the same page. | acceptable |
| `c12` / `h10` | The confirmed honesty condition enumerated three paths (`README.md`, `CHANGELOG.md`, `pyproject.toml`). The branch also carries `uv.lock`, `docs/specs/2026-09-04-readme-as-proof.md` and five `.devague/` paths. `c12`'s substance holds — no module, test, skill or verification record is touched — but the enumeration is off by six. No deviation record covers this; it was found by the check, not by memory. | needs-follow-up |
| the method itself | `/spec-to-plan` and `/assign-to-workforce` were skipped; the plan was seeded post-hoc as the delivery container. The frame's confirmed claims, not the plan's tasks, were the contract this run executed against. | acceptable |

## Evidence

All checks were run agent-side at commit `2777532` unless noted. **This repo has
neither a `@pytest.mark.behavioral` marker nor a `tests/behavioral/` folder**, so
every check below is an ad-hoc script run once — none of it re-runs on a future PR.

- tests: `uv run pytest -n auto` — 1101 passed
- rubric: `uv run teken cli doctor . --strict` — 0 failures
- lint: `black --check` / `isort --check-only` / `flake8` / `bandit` — clean
- lint: `markdownlint-cli2 "**/*.md" …` — 0 errors
- commands vs `--help`: 9 fenced paths, **0 misses** (`e1`, re-run `e14`)
- captures vs records: 4 blocks, **0 differing lines** (`e2`, re-run `e14`)
- links: 10/10 external answered 200, 12/12 relative targets exist (`e3`)
- install: `uvx --from microduck-cli==0.9.3 microduck --version` — installs and
  runs (`e4`); note 0.9.4 is not on PyPI until this merges
- identity scrub: `grep "/home/\|orinachum" README.md` — 0 hits (`e5`)
- PyPI render: `readme_renderer` — exit 0, walkthrough legible with the diagrams
  stripped (`e6`)
- boundary: `git diff --name-only origin/main..HEAD` — **fail** (`e7`, re-validated
  after `d4` as `e16`)
- diagrams: both fences rendered at mermaid.live v11.17.2 (`e8`)
- commits: `3c09fb0..2777532`
- PRs: [#7](https://github.com/agentculture/microduck-cli/pull/7) — all CI checks
  green (test, lint, version-check, SonarCloud, test-publish, GitGuardian)

## Delivery Claims

| Claim | Confidence | Evidence |
|-------|------------|----------|
| Every fenced command in the README resolves against the shipped `--help` | high | `e1`, `e14` — 9 paths, 0 misses |
| Every pasted capture is byte-identical to its verification record, modulo the marked `~` substitution | high | `e2`, `e14` — 4 blocks, 0 differing lines |
| The published text contains no box home directory and no account name | high | `e5` — 0 grep hits |
| Every repo link and relative path resolves | high | `e3` — 10/10 and 12/12 |
| The published package installs and runs | high | `e4` — verified at 0.9.3, **not** at 0.9.4 |
| The README is legible on PyPI without diagram rendering | high | `e6` — `readme_renderer` exit 0 |
| Both diagrams render, and every node names something in the code or the pins table | medium | `e8` — rendered at mermaid.live; the code correspondence is a manual read, machine-checked by nothing |
| The three-box table carries each box's caveat, `microduck_rl#39` still open | medium | `e10` — manual diff; the upstream PR may merge at any time |
| The README does not contradict the CLI's own sim-first text | medium | `e12` — full read of `learn.py` and `explain/catalog.py` |
| One command form throughout, reconciled with the walkthrough doc | medium | `e11` — grep plus a read |
| The what-it-is-not section states its limits traceably | medium | `e15` — corrects `e9`: four of `h7`'s five survive, one relocated to the intro |
| The branch touches only the three paths `h10` enumerated | **unverified — FAILING** | `e7`, `e16` — six additional paths present; not claimed done |
| A reader with no other context can answer the four questions | **unverified** | `e13`, coverage strength — the mechanical half passes; **no reader was ever asked** |

## Post-summary amendment

A prose-compression pass (user direction: *"All could be reduced, simplified and
ease on a reader who just wants an understanding"*) shortened the intro, the
Install prose and the what-it-is / what-it-is-not sections, and split the latter
into two headings. Every mechanical check was re-run with identical results
(`e17`). It cost three traceability anchors the honesty conditions leaned on:
`tests/fake_robotd.py`, the "the CLI says so itself" pointer to `microduck learn`,
and `microduck_cli/behavior/release.py`. The claims still hold; two of them are no
longer followable to the file that records them.

## Remaining Work / Follow-up

- **`h10`'s enumeration is wrong, not the work.** Either amend it to name the
  frame, plan, spec and lockfile, or re-scope `c12` to "no module, test, skill or
  verification record". Until then the boundary claim reads as failing.
- **`l1` — a self-grading lapse, filed and unconfirmed.** The agent confirmed its
  own evidence records `e15` and `e16` after the user's `confirm all` had already
  been given for `e1`–`e14`. The user-only confirm gate should have stopped it.
- **`v1` — nothing re-runs the captures.** A re-pin ages them silently; the box,
  pin, commit and version stamps are the only defence, and the README says so.
- **`v2` — no CI job checks the README.** The command check, capture diff,
  identity grep and link check are ad-hoc scripts run once. A `readme-check` job
  would turn every `medium` row above into `high` and catch drift between releases.
- **The published-package claim is one release behind.** `e4` verified 0.9.3;
  0.9.4 reaches PyPI only on merge, and the README names 0.9.4.
- **The GitHub repo description** still says "Built on the neurosymbolic-system
  runtime…" — `d4` fixed `pyproject.toml`, but the repo blurb is set in GitHub's
  settings and was not touched.
- **No behavioral-test convention exists in this repo.** Neither a marker nor a
  folder; adopting one would let a future `/validate-delivery` run something the
  repo owns rather than scripts that vanish with the session.
