# README as proof

> microduck-cli's README reads like a proof, not a pitch: one diagram of what the CLI actually is, command blocks over prose, verbatim captures from three verified boxes, a plain statement of what is not verified, and every upstream and sibling repo cited
> instruction: Read the finished README.md cold: it must show a diagram before prose, a runnable block per section, captures with box+pin+commit stamps, an explicit not-verified section, and every upstream and sibling repo linked

## Audience

- Humans meeting microduck-cli cold on GitHub or PyPI first — deciding whether this CLI is real; operator agents second, who are routed on to 'microduck learn', the explain catalog and the operate-microduck skill rather than taught inside the README
  - instruction: Check the first screen introduces no repo jargon (noun group, tick seam, deviation d1) before it is explained, and that the agent-facing route (microduck learn, explain, operate-microduck) appears once, as a pointer

## Before → After

- Before: README.md (109 lines, at 0.9.3) is prose-first: four narrative paragraphs before any command, no diagram anywhere in the repo's markdown, two short command blocks with no output shown, no install-from-PyPI line although microduck-cli 0.9.3 is published, and no pointer to the three on-box verification records that hold the actual evidence
- After: A reader who has only README.md can, in one screen-height each: see a diagram of what the CLI sits between (operator, JSON-RPC socket, robotd, sim/fake/real body), copy a command block that works, read a verbatim capture from a real box with its host, pins and CLI commit stamped beside it, and state plainly what has NOT been verified — no physical duck, no locomotion at the current pin
  - instruction: Time a cold reader: each of the four things (diagram, runnable block, stamped capture, not-verified list) is findable in under thirty seconds of scrolling

## Why it matters

- This CLI's whole claim is that it was built against a real daemon and honestly recorded; a README that asserts that in prose while the evidence sits unlinked in docs/verification/ asks the reader to take it on faith, which is exactly the thing the repo's own honesty discipline refuses

## Requirements

- README.md gains mermaid diagrams of what the CLI actually is: at minimum one topology diagram (agent or human -> microduck-cli -> JSON-RPC over a unix socket -> robotd -> fake / MuJoCo sim / real body) and one of the ONE 50 Hz tick seam (sense -> rules -> arbitrate -> compose -> sink, with the human gate withholding motion)
  - instruction: Keep each fence under 20 lines so GitHub renders it legibly; the facts come from CLAUDE.md's behaviour-engine section and `microduck_cli`/behavior/engine.py, not from invention
  - honesty: Every node and edge in each mermaid fence corresponds to something in the code or the pins table — the socket path, robotd, the three body kinds, and the tick order in `microduck_cli`/behavior/engine.py; the numbered 'Try it in simulation' walkthrough is the single text fallback for the PyPI render and no per-diagram prose equivalent is required (user decision on q1)
- Prose gives way to commands: every section leads with a runnable block, and every command in every block is checked against the shipped --help output at the version README names
  - instruction: Script the check: extract each fenced 'microduck ...' line and assert the verb path exists in 'microduck <noun> --help' before the PR opens
  - honesty: Every fenced microduck command in README.md resolves against the shipped --help at the version README names, checked by a script whose miss count is zero, and the README names that version
- README.md carries live proofs — short verbatim captures copied from docs/verification/, each stamped with the box, the upstream pins, the CLI commit and the microduck-cli version it was recorded at, so a reader can tell when a capture has aged
  - instruction: Copy, never retype: the source records are 2026-09-04-sim-bringup.md (DGX Spark GB10), -thor-sanity.md (Jetson AGX Thor) and -orin-sanity.md (Jetson AGX Orin)
  - honesty: Every capture in README.md is byte-identical to its source in docs/verification/ modulo explicitly marked elision, and each carries the host, the upstream pins or a link to them, the CLI commit and the microduck-cli version it was recorded at
- README.md states what microduck-cli is and is not in one place a skimmer cannot miss: sim-first with no physical duck ever driven from it, locomotion NOT achieved at the current pin, the API-16 deviation d1 that leaves the policy channel unavailable, neurosymbolic-system named as a future import and not a dependency, and zero third-party runtime dependencies
  - instruction: Every one of these five is already recorded somewhere in the repo (README, CLAUDE.md, docs/verification/2026-09-04-sim-bringup.md, pyproject.toml) — cite, do not re-derive
  - honesty: The what-it-is-not section states all five: no physical duck has been driven, locomotion is not achieved at the current pin, deviation d1 leaves the policy channel unavailable on API 16, neurosymbolic-system is not a dependency, and runtime dependencies are empty — each traceable to the file that records it
- README.md cites its sources in two tables: the upstream pins it is validated against (pollen-robotics/microduck sim-remote-io, `microduck_rl` develop, with commits) and the AgentCulture siblings it composes (neurosymbolic-system, reachy-mini-cli, arm101-cli, teken, devague), each with the one thing taken from it and the cite-don't-import policy stated once
  - instruction: docs/upstream-pins.md holds the commit table; do not duplicate the commits in README, link them, so there is one place to re-pin
  - honesty: Every repo link in README.md resolves to a public repository, the upstream commits are linked through docs/upstream-pins.md rather than duplicated, and the cite-don't-import policy is stated once in prose
- Install leads with the published package (uv tool install microduck-cli, or uvx microduck-cli) since 0.9.3 is on PyPI today, with the from-source uv sync path second for contributors
  - instruction: Verify the published version at PR time with the PyPI JSON API rather than pinning a number in prose
  - honesty: The install block's package name and command are verified against the live PyPI release at PR time, and the from-source path still works from a clean clone
- The proof section names all three boxes it was verified on — DGX Spark (GB10), Jetson AGX Thor and Jetson AGX Orin — and carries each box's caveat in the same table as its result: Thor's tiers ran on an uncommitted local torch override whose upstream fix is still open as pollen-robotics/`microduck_rl`#39 (issue #38), so env doctor's `rl_pinned_commit` fails there by design until it merges and this repo re-pins; Orin's SBSA torch wheel carries no `sm_87` kernels, so GPU training is unavailable there and only checks 1-4 pass
  - instruction: One row per box; the caveat column is not a footnote — it sits in the table beside the pass
  - honesty: The three-box table names Thor's override and links `microduck_rl`#39 as still open (re-checked at PR time, since it may have merged), and names Orin's missing `sm_87` kernels; both caveats are traceable to docs/verification/2026-09-04-thor-sanity.md and -orin-sanity.md
- Captures pasted into README.md are scrubbed of identity before they ship: the three records contain box home directories (/home/spark, /home/thor, /home/orin) and the operator's Hugging Face namespace (--namespace orinachum), and README.md is the PyPI long description, so a verbatim paste republishes them to an external index
  - instruction: Redact only the home directory (/home/spark -> ~), mark it as the records mark elisions, and skip any capture line carrying an account name outright
  - honesty: No home directory of a real box and no account name appears in README.md; every redaction is marked, and the redacted text still matches its source line character for character outside the marked span
- The simulation walkthrough states its prerequisites before its first command: two upstream clones (pollen-robotics/microduck at the pinned sim-remote-io commit, `microduck_rl` at develop) and a Rust toolchain, or 'env doctor' fails seven of its thirteen checks and the reader's first experience of the CLI is a wall of FAILs
  - instruction: State it once, immediately above the first sim block, with the env doctor command as the check
  - honesty: A reader who has neither clone can tell from README.md alone, before running anything, that the simulation section needs them and which command reports on them
- README.md names the platform its evidence covers: every verified box is Linux on aarch64 (DGX Spark GB10, Jetson AGX Thor, Jetson AGX Orin); no `x86_64` and no macOS run is recorded, and the CLI speaks unix domain sockets to a daemon built with cargo
  - instruction: Copy the wording from docs/verification/\*.md's box tables rather than generalising
  - honesty: Every platform statement in README.md traces to a box table in docs/verification/; no untested platform is implied as supported
- The what-it-is-not section carries the two hardware-safety facts a reader with a real duck needs before copying anything: motion verbs are gated (a dry run on a pipe moves nothing, --apply is required in agent mode) and 'duck relax' drops torque, which collapses the duck
  - instruction: One line, in the what-it-is-not section, citing the operate-microduck skill
  - honesty: The gate and the relax warning appear above any block containing a motion verb, and both are traceable to duck/gate.py and the operate-microduck skill
- README.md uses one command form throughout and reconciles it with docs/operating-the-duck.md, which writes every command as 'uv run microduck ...' while an install-first README would write bare 'microduck ...'
  - instruction: Pick one form and use it in every block; if the global form is chosen, note the uv run equivalent once
  - honesty: Every fenced command in README.md uses the same invocation form, and that form is consistent with docs/operating-the-duck.md or the difference is explained once

## Honesty conditions

- README.md contains at least one mermaid fence, at least one verbatim capture copied from docs/verification/, and a section stating what has not been verified; markdownlint-cli2 exits 0 on README.md
- Nothing above the first command block uses a term the README has not yet defined, and the README never tries to teach an agent the CLI surface itself — it links microduck learn, the explain catalog and the operate-microduck skill
- The before state is quotable from the merge-base README.md at 0.9.3: 109 lines, first command at line 74, zero mermaid or image fences in the repo's markdown, no PyPI install line, no link to docs/verification/
- A reader given only README.md can name what the CLI talks to, run one block start to finish without another file, cite one capture's box and pin, and list the unverified items
- Every credibility claim the README makes is either shown (a capture) or linked to the record that shows it; no claim about hardware or verification stands on prose alone
- git status on the branch before the PR lists README.md, CHANGELOG.md and pyproject.toml and nothing else
- Every quoted block traces to a line range in a docs/verification record, and no file under docs/verification/ appears in the branch diff
- The four reader questions are answerable from README.md alone; markdownlint-cli2 exits 0 on it; the fenced-command check script reports zero misses
- The README's what-it-is-not list and the CLI's own learn/explain text make no contradictory claim about what has been driven
- No sentence in README.md asserts something that would need a retraction if it turned out wrong; verification claims name their record, and forward-looking statements are marked as such

## Success signals

- A reader given only README.md can name what the CLI talks to and how, run one command block start to finish without opening another file, point at one verbatim capture and say which box and pin it came from, and list what is not verified; markdownlint-cli2 exits 0 on README.md; a script matches every fenced command against --help with zero misses
  - instruction: Hand the README to a reader with no other context and ask the four questions

## Scope / boundaries

- This work changes README.md only, plus the version bump and CHANGELOG entry CI requires; no module under `microduck_cli`/, no test, no skill and no docs/verification record is touched
  - instruction: git status before the PR shows README.md, CHANGELOG.md and pyproject.toml and nothing else
- The three verification records are quoted, never edited or reworded — including their negative findings; a capture that will not fit is elided with an explicit marker and a link, the same convention the records themselves use
  - instruction: Diff any pasted block against the source file to prove it is byte-identical modulo marked elision
- README.md's honesty section may extend but must not contradict the CLI's own sim-first statements, which already ship inside the binary (`microduck_cli`/cli/`_commands`/learn.py line 25 and explain/catalog.py line 79)
  - instruction: Diff README's what-it-is-not list against learn.py line 25 and catalog.py line 79 before the PR

## Non-goals

- README.md does not become a second CLAUDE.md: the seam rules, the noun-adding recipe, the rubric and the worktree conventions stay in CLAUDE.md and are linked, and agent-facing teaching stays in 'microduck learn' and the explain catalog
- No new image, screenshot or GIF asset is committed for this uplift; the repo has no binary assets today and a MuJoCo screenshot would age with every re-pin without a gate to catch it

## Assumptions

- GitHub renders mermaid fences and the repo's .markdownlint-cli2.yaml (default true, MD013 off, no MD040 override) accepts one, so a diagram adds no lint exception — but pyproject.toml line 5 makes README.md the PyPI long description and PyPI's renderer shows a mermaid fence as a plain code block, so every diagram needs a text equivalent beside it
  - instruction: Probe with readme-renderer before the PR the way devague did, and read the HTML without the diagram
- Nothing in tests/ pins README.md's text (`test_lane.py`'s README references are to the upstream `microduck_rl` README, not this one), so the README can be restructured freely without breaking the suite
  - instruction: Re-check with a grep for README across tests/ before assuming it still holds
- A README claim is effectively irreversible once released: pyproject.toml makes README.md the PyPI long description, publish.yml ships a .devN build to TestPyPI on every same-repo PR and the real package on merge, and a released distribution's long description cannot be edited without a new release — so a wrong or overstated claim is retracted by shipping again, not by a git revert
  - instruction: Weigh every sentence as if it cannot be edited after release

## Scope exploration

- `s1` — `README.md (109 lines at 0.9.1, re-read at 0.9.3)`: Prose-first: four narrative paragraphs and two tables before the first command; the two command blocks show no output; the sibling table already exists and is accurate; there is no diagram, no install-from-PyPI line and no link to docs/verification/
  - seeds: `c3`, `c6`, `c10`
- `s2` — `pyproject.toml lines 2-26`: readme = 'README.md' makes it the PyPI long description; both console scripts (microduck, microduck-cli) install; dependencies = \[\] is real, so the zero-runtime-deps claim is checkable from the file
  - seeds: `c9`, `c16`
- `s3` — `docs/verification/2026-09-04-sim-bringup.md (211 lines), -thor-sanity.md, -orin-sanity.md`: Three on-box records exist — DGX Spark GB10, Jetson AGX Thor, Jetson AGX Orin — each stamping host, upstream pins, CLI commit and daemon API 16, with captures copied unchanged and negatives kept (locomotion not achieved at the pin, no physical duck, 12 live-suite checks passing). This is the live-proof source; nothing needs to be re-run to write the README
  - seeds: `c8`, `c13`
- `s4` — `docs/upstream-pins.md (20 lines)`: Holds the pinned upstream commits (microduck sim-remote-io 0cd676d, `microduck_rl` develop 29e887e) and states the nothing-is-copied policy; re-pinning is meant to be one PR touching all rows, so the README must link this table rather than copy the commits
  - seeds: `c10`, `c12`
- `s5` — `gh repo view on all eight linked repos`: Every link the README makes or should make resolves to a public repo (agentculture/neurosymbolic-system, reachy-mini-cli, arm101-cli, teken, devague, microduck-cli; pollen-robotics/microduck, `microduck_rl`); neurosymbolic-system's GitHub description already claims the extraction that has not shipped, which is why the README must say plainly it is not a dependency
  - seeds: `c9`, `c10`
- `s6` — `PyPI JSON API for microduck-cli`: 0.9.3 is published (releases 0.8.0 through 0.9.3), so 'uv tool install microduck-cli' works today and the README's clone-and-uv-sync-only Quickstart understates what a reader can do
  - seeds: `c11`
- `s7` — `../devague/README.md (119 lines) and docs/specs/2026-09-04-coherent-public-face-and-fresh-ledgers.md`: The model to borrow: five sections (Install, Set up, Work with your agent, Why it works, What lands where), exactly one mermaid fence with the flow and the human gates as nodes, a numbered walkthrough as the PyPI text fallback, and an explicit 'what it never does' list. Its final decision was to drop console captures — the opposite of what is wanted here, so the structure transfers and that rule does not
  - seeds: `c6`, `c18`
- `s8` — `CLAUDE.md 'Architecture: the behaviour engine' and microduck_cli/behavior/ (engine, sink, rule_engine, human_gate, compose, liveness)`: The tick-seam facts a diagram needs are already written down: one Sense per tick, arbitrate one owner per channel, compose, write through the sink once, `tick_seam` after the write, the human gate withholding MOTION channels, one process on the control socket. The README diagram transcribes this; the rules and recipes stay in CLAUDE.md
  - seeds: `c6`, `c14`
- `s9` — `.markdownlint-cli2.yaml and .github/workflows/tests.yml`: README.md is linted (not in the ignores list; docs/specs, docs/plans, docs/deliveries and .devague are), the config is default-true with MD013 off and no MD040 override so a mermaid fence passes, and the version-check job fails any PR — docs-only included — whose pyproject version matches origin/main
  - seeds: `c12`, `c16`
- `s10` — `grep -rn README tests/ (only tests/test_lane.py hits)`: Every README reference in the suite is to the upstream `microduck_rl` README the train-lane argv is transcribed from, not to this repo's README; no test reads or pins README.md, so restructuring is test-safe
  - seeds: `c17`
- `s11` — `microduck --help and the noun overviews (env, duck, policy, rules, cli)`: The five noun groups and the introspection verbs the README's table names all exist at 0.9.3, so the table is accurate today — but nothing in CI checks a README command against --help, which is why the check has to be scripted by hand before the PR
  - seeds: `c7`, `c19`
- `s12` — `.claude/skills/operate-microduck/SKILL.md and docs/operating-the-duck.md (117 lines)`: The operator front door already exists in two places — the first-party skill (open the sim, operate, watch via the screenshot recipe, close it down) and the walkthrough doc with each command's exact output; the README should route to them rather than grow a third copy that drifts
  - seeds: `c14`
- `s13` — `challenge pass / adjacent-systems lens: pyproject.toml readme field, .github/workflows/publish.yml, microduck_cli/cli/_commands/learn.py, microduck_cli/explain/catalog.py`: README.md is consumed by three systems beyond GitHub: PyPI (long description, immutable per release), TestPyPI on every same-repo PR, and — indirectly — the CLI's own learn/explain text, which already ships a sim-first statement the README must not contradict
  - seeds: `c27`, `c28`
- `s14` — `challenge pass / security lens: grep for /home/ and account names across docs/verification/`: The three records contain three box home directories and the operator's Hugging Face namespace in a train-lane capture; harmless in-repo, but a verbatim paste republishes them through the PyPI long description
  - seeds: `c22`
- `s15` — `challenge pass / overlooked-actors lens: microduck_cli/env/doctor.py's thirteen checks against a bare box`: A reader with neither upstream clone meets seven FAILs on the first command; the current README's sim block states no prerequisite, so the failure looks like the CLI's rather than the box's
  - seeds: `c23`
- `s16` — `challenge pass / unstated-assumptions lens: the box tables in all three verification records`: Every recorded box is Linux aarch64; nothing in the repo evidences `x86_64` or macOS, and the README has never said which platform its claims cover
  - seeds: `c24`
- `s17` — `challenge pass / failure-modes lens: microduck_cli/duck/gate.py and the operate-microduck skill's hard rules`: A reader who does have a duck could copy a motion block; the gate and the fact that relax collapses the duck are documented in the skill and CLAUDE.md but nowhere a README-only reader would see them
  - seeds: `c25`
- `s18` — `challenge pass / lifecycle lens: docs/operating-the-duck.md against an install-first README`: The walkthrough writes every command as 'uv run microduck ...' (a source checkout); an install-first README writes bare 'microduck ...' — two forms a reader will hit within one click of each other
  - seeds: `c26`
- `s19` — `challenge pass / observability lens: .github/workflows/tests.yml and the lint job`: Clean pass with a gap recorded: markdownlint covers README.md, but nothing verifies its commands or its captures, so the honesty conditions on c7 and c8 rest on a manual script at PR time (parked as v2)
- `s20` — `challenge pass / reversibility, concurrency and migration lenses: git history, .devague state, README.md`: Clean pass in the repo: the change is one tracked file, revertible by git revert, with no schema, state or concurrency surface — the only irreversible edge is the published distribution's long description, which is seeded as c28 rather than left in this clean-pass note

## Decisions

- This README deliberately diverges from devague's own final README decision (docs/specs/2026-09-04-coherent-public-face-and-fresh-ledgers.md: 'no console captures, at most one command per bash block'). devague is a deterministic method CLI a human never types; microduck-cli drives hardware, and its credibility rests on captured output from real boxes — so the structure is borrowed (install, diagram, numbered flow, why it works, what it never does) while the no-captures rule is not
  - instruction: Say so in the PR description so a reviewer comparing the two READMEs does not read it as drift
- README.md is long by design (~250 lines) with the captures inline, in this section order: title + topology diagram, Install, What it is (and is not), Try it in simulation, Proof — three boxes, Not verified, The tick engine (diagram), Cited from / built on. Resolves q1
  - instruction: Follow the section order literally; a reader must meet the diagram before any prose and the 'not verified' section before the citations
- Capture redaction, resolving the c22/c13 tension: home directories are replaced with ~ and the substitution is noted once, and no capture containing the operator's Hugging Face namespace is quoted at all — the HF Jobs dry-run block is described, not pasted
  - instruction: Note the substitution once, beside the first capture; never quote the HF Jobs block

## Hard questions

- risk: The captures age silently: a re-pin or a new release makes them stale and nothing in CI notices, so the version and commit stamps are the only defence (resolved: Stamps only, and say so: each capture carries its box, upstream pins, CLI commit and microduck-cli version, and the proof section states plainly that a re-pin ages them. No CI gate, no second file touched.)
- Does every diagram need a text equivalent inline, or is the numbered walkthrough enough for the PyPI render? (resolved: One numbered walkthrough only: the diagrams stand alone as mermaid fences, and the numbered simulation walkthrough is the sole text fallback for the PyPI render. No per-diagram prose equivalent.)

## Open parks

- [unknown_nonblocking] Whether the captures need a freshness gate: they are stamped with a CLI commit and upstream pins, but nothing re-runs them, so a re-pin silently ages the README
- [unknown_nonblocking] No CI job checks README's fenced commands against --help or its captures against docs/verification/; the check is a manual script run once, at PR time, so the README can drift silently between releases
