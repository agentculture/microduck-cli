# Changelog

All notable changes to this project will be documented in this file.

Format follows [Keep a Changelog](https://keepachangelog.com/). This project
adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.9.3] - 2026-09-04

### Added

- On-box verification on NVIDIA Jetson AGX Thor
  (`docs/verification/2026-09-04-thor-sanity.md`): the six Spark checks run
  headless on Thor at the same upstream pins — provisioning, the `--fake` live
  suite (11 passed), the MuJoCo live suite (12 passed, walking still `xfail`),
  the 64-env train smoke (`ok: true`, 144 s) and the HF Jobs dry run — with
  the outputs excerpted unchanged (elisions marked), plus the devague frame,
  spec and plan that drove it (`thor-sanity`).

### Changed

- `env doctor`'s `host_class` remediation on Jetson AGX Thor now states the
  verified recipe instead of calling the path unverified: torch from NVIDIA's
  `sbsa/cu130` index plus the `nvpl-blas`, `nvpl-lapack` and
  `nvidia-cudss-cu13` wheels that wheel links but does not declare, via a
  local override of the `microduck_rl` clone. `torch_source_applies` stays
  `False` because upstream's `pytorch-cu129` source was never exercised there.
  (`env/hosts.py`, `tests/test_hosts.py`).
- `env doctor`'s `host_class` remediation on Jetson AGX Orin now states the
  verified outcome from the Orin record instead of "unverified": GPU training
  is not available there at this pin (the only torch 2.9.1 cp312 CUDA-13 wheel
  has no `sm_87` kernels), while the sim, the fake body and the rules layer
  work. Only an unidentified Jetson still reads "unverified".
- `HostInfo.verified` points a verified host class at its on-box record — GB10
  at the Spark record, Jetson AGX Thor and Jetson AGX Orin at theirs, every
  other class `None` — and `env doctor` appends `verified on-box: …` to the
  `host_class` line. All three boxes the CLI targets locally now carry their
  evidence.
- The Spark record's "Jetson Thor / AGX Orin — not attempted" bullet now links
  the Thor record.

## [0.9.2] - 2026-09-04

### Added

- docs/verification/2026-09-04-orin-sanity.md — the six Spark sanity checks re-run on a Jetson AGX Orin: checks 1-4, the gates, and the live suite (12 passed, walking sentinel xfailed) all pass; check 5 fails with a recorded finding — no torch wheel with sm_87 kernels exists for cp312+2.9.1 on the Jetson indexes, so GPU training on AGX Orin is not available at this pin. Includes the Orin RL-venv recipe (SBSA cu130 index + venv-local nvidia/nvpl libs) and the bare uv-run re-lock gotcha.
- docs/specs and docs/plans 2026-09-04-orin-sanity.md — the challenged devague spec (scope + think + challenge, vLLM co-tenancy finding) and its converged six-task plan, with the .devague frame/plan state.

## [0.9.1] - 2026-09-04

### Changed

- Recorded the PR #3 review wave in the devague delivery ledger
  (`.devague/deliveries/microduck-cli-env-teach-operate-rules.json`): four
  amended behavioral deltas (`b10`–`b13`) covering the gated `rules engine
  start` / `rules intent` path, `duck record`'s own pad and ToF links, the
  smoke-gate commit check and its siblings, and the SonarCloud round (the
  text-mode `duck quack` crash, `SignalExit` unwinding through `TickBus`).

## [0.9.0] - 2026-09-04

### Added

- The duck domain, end to end, on a simulated MicroDuck: four noun groups — `env` (doctor / up / down / status / hosts for the MuJoCo twin or the `--fake` body, built from the pinned `pollen-robotics/microduck` `sim-remote-io` commit), `duck` (robotctl's verbs — health, monitor, version, init, relax, enable, do, mode, look, stop, quack, configure --list — plus `move` and `record`), `policy` (list / load / add / remove / reset, robotctl-line verbs for updater-only ops, and the `microduck_rl` train lane: smoke / train / play / export / publish / infer / install), and `rules` (list / check / engine run|start|stop|status / intent).
- A stdlib-only JSON-RPC 2.0 client for robotd's unix socket (`microduck_cli/ipc/`): writer/reader threads, bounded queue, `hello` + joint-table check, peek slots for the state stream, named drops; the protocol table is transcribed from the pinned `duck-ipc-proto` (API_VERSION 16).
- The behaviour engine (`microduck_cli/behavior/`), built extraction-first behind the seams neurosymbolic-system will take: 50 Hz tick with `tick_seam` and a fault-isolating `TickBus`, heartbeat liveness, stderr-only senselog, data-only rules (schema_version, per-id two-layer merge, tombstones), the single `KindRegistry` admission path for rule-fired and agent-injected intents, `RobotSink` wire encoding with no client-side filtering, release-on-abnormal-exit (never `relax`), the human-driving gate (a person on the pad owns motion), a passive idle base, three shipped safety rules, skills validation for both daemon APIs, and JSONL record/replay.
- Gated motion the arm101 way on every verb that moves or de-energises the duck: TTY prompt, non-TTY dry-run plan with zero sends, `--apply` to proceed; upstream's safety sentences verbatim.
- An in-process fake robotd for tests (`tests/fake_robotd.py`) aligned to the probed daemon, four static guard tests (zero deps, no hardware paths, no config writes, no secrets in output), a lockstep docs test, an upstream-links test, and an opt-in live suite (`tests/live`, `MICRODUCK_LIVE=1 uv run pytest -m live -n0`) that operates the CLI against the real daemon on both bodies; an optional `real-daemon` CI job runs it.
- `docs/upstream-pins.md` (the pinned upstream commits), `docs/operating-the-duck.md` (the six-command walkthrough), `docs/verification/2026-09-04-sim-bringup.md` (the on-box record), the devague spec, plan, delivery summary and ledger under `docs/specs`, `docs/plans`, `docs/deliveries` and `.devague/`, and the vendored `validate-delivery` skill.
- Both console scripts install: `microduck` and `microduck-cli`.

### Changed

- CLAUDE.md records decision c20: the engine lives in this repo now and neurosymbolic-system is extracted from it later; the package inventory and the cicd triage prose follow.
- `explain` is split per noun with the verb lists driving `overview` and `learn`; the root entry describes the MicroDuck CLI, not a template.

### Fixed

- Nothing from a prior release — this is the first duck release. Defects found by the live runs before merge: `achieved_hz` reported work capacity instead of cadence; the intent yaw axis was named `wz` instead of the wire's `vyaw`; the train lane ignored `DUCK_SIM_RL` and leaked a raw FileNotFoundError; `robot.subscribe`'s `hz` must be an integer; `duck look` required `--y`.

## [0.8.0] - 2026-08-29

### Changed

- **`CLAUDE.md` re-initialized from the seed into a real runtime prompt** (`/init`).
  Replaces the self-initializing placeholder with: what the repo actually is today
  (the mesh-agent scaffold — **no MicroDuck control code exists yet**), the
  `microduck` vs `microduck-cli` console-script gotcha, the common command + lint
  stack, the agent-first CLI contracts (registration, error, output, explain
  catalog, identity/`doctor`) and the rubric shapes that must not be "simplified"
  away, hard constraints, CI/release, the vendored-skill rule, and the workflow
  conventions (worktrees in `../.worktrees.microduck-cli/`, in-repo public eidetic
  memory, PR flow).
- **A "three sibling repos" section** naming what to take from each and what not
  to: `neurosymbolic-system` is the runtime to **import** — but it is itself still
  a bare scaffold, so it is not a dependency yet and a tick loop must never be
  re-implemented here; `reachy-mini-cli` is the architecture (noun groups, one tick
  seam, single-SDK-owner model); `arm101-cli` is the hardware-safety baseline
  (gated motion, release-on-abnormal-exit, hardware deps behind an extra).
- **README rewritten** — a `Status: scaffold` section stating plainly that no duck
  control code ships yet, a corrected quickstart (`uv run microduck …`; the old
  `uv run microduck-cli …` line failed with "Failed to spawn"), a sibling-projects
  table, and the removal of the template's "Make it your own" rename instructions.
- **`cicd` skill adapted to this repo's landed stack** — the upstream
  "Greenfield-aware steps" no-ops become unconditional "Pre-PR steps
  (microduck-cli)", the triage defaults name this repo's real false-positive class
  (scaffold complaints; a second runtime loop that belongs in
  `neurosymbolic-system`), and the mesh-ping paragraph names microduck-cli.
  `SKILL.md` prose only — no script bodies touched — and recorded as a tracked
  divergence in `docs/skill-sources.md`.
- **`.claude/skills.local.yaml.example`** lists the three robot-family siblings
  under `sibling_projects`, so `cicd`'s alignment-delta step sees them.

## [0.7.0] - 2026-08-24

### Added

- **`resume <task-id|last> [--detach]` verb** in `ask-colleague` — pick a cut / timed-out / SIGTERM'd run back up from its persisted artifact, continuing on the original `colleague/<id>` work branch.
- **Per-seat thinking effort** in `ask-colleague` — `--effort` (acting seat), `--seat-effort S=R` (any seat), `--role NAME` (colleague#416). Rule of thumb: `--effort off` for small well-specified briefs, default for ordinary work, `xhigh` for open-ended judgement.
- **Review diff front-loading** — `ask-colleague review` embeds a filtered, bounded diff directly in the prompt instead of relying on the colleague run to fetch it.

### Changed

- **`ask-colleague` re-vendored byte-verbatim from `agentculture/colleague` @ 1.63.0** (cite-don't-import) — all five files (`SKILL.md`, `scripts/ask-colleague.sh`, `prompts/{explore,review,write}.md`). Every repo scaffolded from this template (`guild create` instantiates it) shipped the Qwen3.6-era wrapper until now.
- **Default colleague model is `unsloth/Qwen3.8-27B-NVFP4`** (was the Qwen3.6 pin). The lobes gateway on `:8001` no longer serves 3.6, so the previous default only worked via colleague's auto-refresh warning path.
- **`docs/skill-sources.md` ledger row** for `ask-colleague` updated to the 1.63.0 sync (was `2026-06-12 (colleague 1.7.0, direct)`) and its verb list extended with `plan` / `resume` / the pilot verbs.

## [0.6.1] - 2026-07-20

### Added

- **Worktree location convention** in `CLAUDE.md` — every worktree you create
  by hand (workforce fan-out lanes, scratch checkouts) lives in
  `../.worktrees.microduck-cli/<name>/`, one
  repo-named directory beside the checkout, replacing a shared `../worktrees/`
  folder. This workspace holds many sibling projects, so a generic shared
  folder accumulates orphaned trees from several repos at once with nothing
  indicating ownership — a stale-tree sweep can't tell a live lane from junk.
  Matches the convention already documented in sibling repo `reachy-mini-cli`.
  Adds branch-prefix guidance (scope the prefix to the work; plain `agent/*`
  collides with leftovers from earlier fan-outs and fails `git worktree add
  -b`), and notes that the vendored `assign-to-workforce` skill uses both the
  shared path *and* `agent/<task-id>` branches in its fan-out example — it is
  cited verbatim and must not be edited, so both are overridden when following
  it. Teardown guidance names `git worktree remove <path>` as the verb that
  actually deletes a worktree; `git worktree prune` only clears metadata for
  directories that are already gone. Tool-managed throwaways are explicitly
  out of scope: `ask-colleague`'s read-only verbs create a detached worktree
  under `${TMPDIR:-/tmp}` and reap it on an EXIT trap, so they never persist
  to need an owner.

## [0.6.0] - 2026-07-18

### Added

- **Four devague-origin skills re-vendored into `.claude/skills/`**
  (cite-don't-import), synced to the fixed devague source
  (devague#74/#75/#76):
  - `challenge` — a risk-scaled blind-spot discovery pass that runs between
    `/think` and `/spec-to-plan`, routing findings back through the existing
    deterministic moves as human-adjudicated proposals.
  - `scope` — the idea→scope leg that surveys the surfaces an idea touches
    before framing, seeding the Announcement Frame with provenance-backed
    boundary/non-goal/assumption claims.
  - `deviate` — stops an in-flight `assign-to-workforce` run when execution
    must diverge from the confirmed plan and records the divergence as a
    first-class, append-only deviation record.
  - `summarize-delivery` — closes the loop after an `assign-to-workforce`
    run with a planned-vs-actual accountability artifact.

  These four originate in `devague` and are re-broadcast via guildmaster; see
  `docs/skill-sources.md` for provenance.

## [0.5.0] - 2026-06-24

### Added

- **Memory-discipline "Conventions and workflow" section in `CLAUDE.md`** — a
  per-task *recall-before / remember-after* convention (scope localized to this
  repo's nick) so the vendored `remember` / `recall` skills are actually used,
  not just present: `/recall` before non-trivial work to build on prior
  decisions instead of re-deriving them, and `/remember` when a non-obvious
  decision, constraint, fix-and-why, or hard-won gotcha surfaces. The section
  documents this repo's memory as **in-repo and public** — records resolve to
  `<repo-root>/.eidetic/memory` (committed, team- and mesh-shared). Inserted
  idempotently (skipped if already present), slotted under an existing
  "Conventions and workflow" heading when one exists, else appended.

### Changed

- **Refreshed the `remember` + `recall` wrappers from eidetic-cli 0.10.0**
  (cite-don't-import) — picks up eidetic's **project-local store default**: the
  files backend now resolves per record by visibility — PUBLIC records inside a
  git repo go to `<repo-root>/.eidetic/memory` (committed, team-shared), PRIVATE
  records (or any record outside a repo) go to `$HOME/.eidetic/memory` (never
  committed), an explicit `EIDETIC_DATA_DIR` still wins, and recall reads both
  stores and merges. Also carries the 0.9.3 hardening (interactive-stdin guard,
  `help` as a search term, SIGPIPE-safe suffix parsing). **Recipe policy
  override (the wrappers here are NOT byte-verbatim):** the injected default
  visibility is flipped from eidetic's `private` to **`public`**, so a plain
  `/remember` lands the note in `./.eidetic/memory` in this repo, kept as part
  of the repo — pass `--visibility private` to route a record to `$HOME`
  instead. `remember` drives `eidetic remember` (idempotent upsert of one JSON
  record or an NDJSON batch on stdin); `recall` drives `eidetic recall` with
  four search modes (exact / approximate / keyword / hybrid). Each `SKILL.md` is
  localized only in the illustrative `--scope <nick>` examples (Provenance keeps
  "First-party to eidetic-cli"). Runtime dep: the `eidetic` CLI on PATH (else a
  local eidetic-cli checkout with `uv`) — **`eidetic >= 0.10.0`** for the
  in-repo routing; on an older CLI the public records still work but are stored
  in `$HOME/.eidetic/memory` instead of in-repo. Propagated by rollout-cli's
  `eidetic-memory` recipe.

## [0.4.0] - 2026-06-23

### Added

- **Vendored the `remember` + `recall` memory skills from eidetic-cli**
  (cite-don't-import) — the write/read halves of eidetic's shared
  `$HOME/.eidetic/memory` surface, so this agent (Claude and its colleague
  backend) can persist facts across sessions and recall them later, sharing
  one store.
  `remember` drives `eidetic remember` (idempotent upsert of one JSON record or
  an NDJSON batch on stdin, dedup by id + content hash); `recall` drives
  `eidetic recall` with four search modes — exact / approximate / keyword /
  hybrid — each hit carrying text, full provenance metadata, a relevance score,
  and a freshness signal. The `.sh` wrappers are byte-verbatim from eidetic-cli
  (their first-party origin); each `SKILL.md` is localized only in the
  illustrative `--scope <nick>` examples (Provenance keeps "First-party to
  eidetic-cli"). Both default to this agent's PRIVATE scope, reading the suffix
  from `culture.yaml`. Runtime dep: the `eidetic` CLI on PATH (else a local
  eidetic-cli checkout with `uv`). Propagated by rollout-cli's `eidetic-memory`
  recipe.

## [0.3.4] - 2026-06-20

### Fixed

- Identity docs and self-description strings still claimed `backend: claude`
  (prompt file `CLAUDE.md`), but this template was promoted to a colleague
  resident in #14/#15: `culture.yaml` declares `backend: colleague` (Qwen) with
  `AGENTS.colleague.md` as the resident prompt. Corrected the stale claim in
  `CLAUDE.md` (Identity section), `README.md`, `docs/skill-sources.md`, and the
  two CLI description strings (`overview` artifacts and `explain doctor`). The
  `doctor` backend→prompt-file mapping and the tests were already on
  `colleague`; this aligns the prose and self-description with them.

## [0.3.3] - 2026-06-20

### Fixed

- pyproject.toml: correct the `license` field and PyPI classifier from MIT to
  Apache-2.0 to match the `LICENSE` file. The README License section was already
  corrected in 0.3.2, but the package metadata was missed; the built wheel now
  reports `License-Expression: Apache-2.0`.

## [0.3.2] - 2026-06-18

### Added

- ask-colleague skill: `monitor`/`guide`/`stop` pilot verbs plus a `--watch`
  flag to dispatch, watch the live feed of, send mid-flight guidance to, and
  cooperatively stop a running colleague flight (re-vendored from colleague).

### Changed

- README: correct the License section from MIT to Apache 2.0 to match the
  `LICENSE` file.

## [0.3.1] - 2026-06-13

### Changed

- CLAUDE.md: add a convention to reach for the `ask-colleague` skill reflexively
  for explore/review/write/grade — read-only `review`/`explore` are always safe;
  side-effecting `write` needs the user's go-ahead.

## [0.3.0] - 2026-06-13

### Added

- AGENTS.colleague.md resident prompt file (backend colleague <-> AGENTS.colleague.md)

### Changed

- Promote agent identity to a colleague resident: culture.yaml backend
  claude -> colleague with a pinned model. The `doctor` backend-consistency
  map gains `colleague` -> AGENTS.colleague.md.

## [0.2.1] - 2026-06-12

### Changed

- **Re-vendored the `ask-colleague` skill from colleague (now 1.7.0, up from the
  0.39.2 sync)** — the wrapper had drifted multiple releases behind origin. Picks
  up the `clean` verb (reap stale/corrupt `colleague/*` branches + orphaned
  `.colleague/` artifacts a crashed run left behind), the `--json` flag on every
  verb (result JSON on stdout, diagnostics/digest on stderr), the
  `_colleague_via_uv` local-dev resolution that honors `--repo`, and the
  tri-state (0/1/2) exit-code contract. `scripts/ask-colleague.sh` + `prompts/`
  are byte-identical to the origin; `SKILL.md` diverges only in the one
  consumer-identifying Provenance clause (`microduck-cli vendors from
  guildmaster`). `docs/skill-sources.md` sync row updated to
  `2026-06-12 (colleague 1.7.0, direct)`. Refs: colleague#183, #186.

## [0.2.0] - 2026-06-06

### Added

- **`ask-colleague` skill** (`.claude/skills/ask-colleague/`) — the first-party front door to the `colleague` CLI (the renamed `convertible`). On top of `explore` / `review` / `write` it adds a `feedback` verb (grade a finished work item — the ROI loop), and `write` now **previews by default** in a throwaway worktree (no side effects) unless `--apply` / `--pr` is given. Reach for it reflexively — `review` for a diverse second opinion on a committed diff before opening a PR, `explore` for a fresh read of an unfamiliar area.

### Changed

- **Replaced the `outsource` skill with `ask-colleague`.** `outsource` was renamed to `ask-colleague` upstream ([colleague#148](https://github.com/agentculture/colleague/pull/148)). Because guildmaster has not re-broadcast the rename yet (its kit still ships the old `outsource`), `ask-colleague` is vendored **directly from the sibling `colleague` checkout** rather than from guildmaster — a tracked local divergence recorded in `docs/skill-sources.md`, parallel to the `agex` → `devex` one. Vendored verbatim except one consumer-identifying clause in the Provenance paragraph.
- **Ledger + CLAUDE.md + `.gitignore`:** point `docs/skill-sources.md` and the CLAUDE.md Skills section at `colleague` / `ask-colleague`, swap the *optional* runtime prerequisite `convertible` → `colleague` (env prefix `CONVERTIBLE_*` → `COLLEAGUE_*`, with the legacy names kept as a deprecated fallback), and gitignore the `.colleague/` run-artifact dir the skill writes (plus the stale `.agex/`).

## [0.1.4] - 2026-05-31

### Added

- **Vendor the `outsource` skill** (`.claude/skills/outsource/`) from
  guildmaster's canonical copy (origin
  [`agentculture/convertible`](https://github.com/agentculture/convertible),
  re-broadcast via guildmaster — guildmaster
  [#51](https://github.com/agentculture/guildmaster/pull/51)). Every agent
  cloned from this template now inherits the ability to hand a scoped task to a
  *different* engine/mind: `explore` (read-only investigation), `review` (a
  diverse second opinion on the committed diff), and `write` (delegate a small
  implementation). `explore`/`review` run isolated in a throwaway `git worktree`;
  `write` refuses a dirty tree. Fulfils
  [#8](https://github.com/agentculture/microduck-cli/issues/8).
- **Ledger + CLAUDE.md:** record `outsource` in `docs/skill-sources.md`
  (origin = convertible, re-broadcast via guildmaster; vendored verbatim — it
  already carries `type: command`) and document its *optional* runtime
  dependency on the `convertible` CLI (the skill exits with an install hint if
  absent, so a clone that never uses it is unaffected).

### Changed

### Fixed

## [0.1.3] - 2026-05-31

### Changed

- Expanded the clone-and-rename instructions in `CLAUDE.md`: added `README.md` to
  the rename targets and a portable `git grep` discovery command so a cloner can
  find every occurrence of the template name (hard-coded in ~100 places across the
  package, including the CLI command files and `_ISSUES_URL` in
  `microduck_cli/cli/__init__.py`) rather than renaming by hand.
- Synced `README.md`'s "Make it your own" checklist with `CLAUDE.md`: it now lists
  `README.md` itself as a rename target and points to `CLAUDE.md`'s discovery
  command as the authoritative procedure, so the two onboarding checklists no
  longer drift.

## [0.1.2] - 2026-05-30

### Changed

- Renamed the PR-lifecycle CLI references `agex` / `agex-cli` to `devex` (same
  tool, new name) across `CLAUDE.md`, `docs/skill-sources.md`, `.gitignore`, and
  the vendored `cicd`, `assign-to-workforce`, and `communicate` skills — the
  `cicd` scripts now invoke `devex pr`.
- Logged the vendored-skill in-place patch as a local divergence in
  `docs/skill-sources.md`; the matching canonical rename is tracked upstream for
  guildmaster in
  [agentculture/guildmaster#48](https://github.com/agentculture/guildmaster/issues/48)
  so a future re-sync reconciles cleanly.
- Aligned the documented `devex` version floor to `>=0.21` across the vendored
  `cicd` `SKILL.md` and `workflow.sh` install hint (were `>=0.1`), matching
  `docs/skill-sources.md` and the `await`-era feature set; flagged upstream on
  guildmaster#48.

### Fixed

- SonarCloud now reports code coverage — added `relative_files = true` to
  `[tool.coverage.run]` so `coverage.xml` emits repo-relative paths that map to
  `sonar.sources=microduck_cli` (absolute / `.venv` paths were dropped
  as unmappable). Mirrors the sibling `convertible` setup.

## [0.1.1] - 2026-05-26

### Changed

- **CI gates on the SonarCloud quality gate**
  ([issue #3](https://github.com/agentculture/microduck-cli/issues/3)) —
  added `sonar.qualitygate.wait=true` to `sonar-project.properties` so a failing
  gate fails the `test` job when `SONAR_TOKEN` is set. Token-less repos and fork
  PRs remain green (the scan step is guarded by `if: env.SONAR_TOKEN != ''`).

## [0.1.0] - 2026-05-26

### Added

- **Onboarded into the AgentCulture mesh** ([issue #1](https://github.com/agentculture/microduck-cli/issues/1)).
- **Agent-first CLI** cited from teken's (`afi-cli`) `python-cli` reference
  (`teken cli cite`) — verbs `whoami`, `learn`, `explain`, `overview`, `doctor`,
  and the `cli` noun group. Runtime is self-contained (`dependencies = []`);
  `teken>=0.8` is a dev dependency only. Passes the seven-bundle agent-first
  rubric (`teken cli doctor . --strict`). `doctor` checks the agent-identity
  invariants (prompt-file-present, backend-consistency, skills-present).
- **Mesh identity**: `culture.yaml` (`suffix: microduck-cli`,
  `backend: claude`) and the matching `CLAUDE.md` prompt file.
- **Canonical guildmaster skill kit** (11 skills) vendored under
  `.claude/skills/` (cite-don't-import): `agent-config`, `assign-to-workforce`,
  `cicd`, `communicate`, `doc-test-alignment`, `pypi-maintainer`, `run-tests`,
  `sonarclaude`, `spec-to-plan`, `think`, `version-bump`. Every `SKILL.md`
  carries `type: command` (load-bearing for the culture/claude backend);
  `cicd` / `communicate` consumer-identifying prose adapted, all script bodies
  verbatim. Provenance in `docs/skill-sources.md`. Three skills (`think`,
  `spec-to-plan`, `assign-to-workforce`) originate in `devague`, re-broadcast
  via guildmaster.
- **Build + deploy baseline**: `pyproject.toml` (hatchling), `tests/` (pytest,
  xdist, coverage), `.github/workflows/{tests,publish}.yml` (CI rubric/lint gate,
  PyPI Trusted Publishing), `.flake8`, `.markdownlint-cli2.yaml`,
  `sonar-project.properties`, and `.claude/skills.local.yaml.example`.

### Changed

### Fixed
