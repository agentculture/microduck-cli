# Delivery Summary — jetson-retest-and-ai-lab-tutorial

plan: `jetson-retest-and-ai-lab-tutorial` · run: `partial` · date: `2026-09-07`
baseline: `devague summary skeleton`

## Intent

> microduck-cli re-verified on Jetson AGX Thor (with a visible MuJoCo window) and Jetson AGX Orin, and a Jetson AI Lab tutorial shows a reader how to run the MicroDuck simulation, the CLI and the training smoke on their own Jetson

After: Both boxes re-verified at 0.9.4 with dated records under docs/verification/, Thor running a graphical session where the MuJoCo duck is visibly standing (two screenshots seconds apart in the record), and a tutorial PR open against NVIDIA-AI-IOT/jetson-ai-lab that a Thor or Orin owner can follow end to end, stating plainly what does not work at the pin


This run executed waves 1–3 of the plan and stopped, by the user's direction
(`d1`), before opening any pull request: the tutorial was written, the agent
followed it literally on DGX Spark with the viewer windowed, the Spark record was
written, and `/validate-delivery` filed twelve evidence records (two of them
failures) before this summary. The Thor and Orin re-runs are the operator's
(`t8`) and have not happened; the two PRs (`t6`, `t7`) follow this artifact.

## Planned Work

- `t1` — Write the tutorial draft in a fork clone of jetson-ai-lab: src/content/tutorials/applications/microduck-on-jetson.mdx (Applications / Robotics, order 5, authors = the user) + src/pages/tutorials/microduck-on-jetson.astro; shared sim+CLI steps outside Tabs, per-box tabs DGX Spark / AGX Thor / AGX Orin; written from the 2026-09-04 records with Thor/Orin marked 'verified 2026-09-04 headless, operator re-run in progress'
- `t2` — Write docs/tools/`check_tutorial.py` in this repo: extracts every fenced bash line from a tutorial file and greps each against the named verification records (0 misses required), greps the tutorial + image filenames + alt text for '/home/' and the account name, and lists the PNGs under the tutorial's image dir for the single-window review
- `t3` — Spark pre-flight (read-only except the fork): probe the seat0 session and /tmp/.X11-unix, both clones at the pins (docs/upstream-pins.md), 'uv run microduck env doctor' healthy, free -g; create the user's fork of NVIDIA-AI-IOT/jetson-ai-lab (gh repo fork --clone=false) and clone it to ../jetson-ai-lab (Node 20 + npm ci must work on Spark for 'npm run build')
- `t4` — Spark dry-run of the tutorial, windowed: follow the tutorial's Spark path literally on this box (DISPLAY=:1, XAUTHORITY of the seat0 session), run the six checks + both live suites (fake; sim with `MICRODUCK_LIVE_HEADLESS`=0) + the 64-env smoke, take two MuJoCo-window-only screenshots >=4 s apart (gnome-screenshot -w, then verify no other window/terminal in the PNG), write docs/verification/2026-09-07-spark-retest.md in the 2026-09-04 record shape, and fix the tutorial wherever it misled — every fix listed in the record
- `t5` — Run the tutorial checks: docs/tools/`check_tutorial.py` against the tutorial and the 2026-09-04 Thor/Orin + 2026-09-07 Spark records (0 misses, 0 identity hits), 'npm run build' in the fork at the branch head (exit 0), and 'npm run dev' page render at /tutorials/microduck-on-jetson (screenshot of the rendered page kept in scratch, not committed)
- `t6` — Repo PR in microduck-cli: add the Spark record, point `_GB10_VERIFIED` in `microduck_cli`/env/hosts.py at it (Thor/Orin pointers unchanged until the operator runs), update tests/`test_hosts.py` and README's three-box Spark row, commit docs/tools/`check_tutorial.py` + tests, version-bump patch with CHANGELOG entry, cicd open, ask-colleague review, merge when green
- `t7` — Open a DRAFT PR from OriNachum/jetson-ai-lab branch docs/microduck-on-jetson to NVIDIA-AI-IOT/jetson-ai-lab main: commits signed off (-s) under the user's identity, body lists tested hardware (Spark GB10 windowed 2026-09-07; Thor/Orin 2026-09-04 headless, operator re-run in progress), the build exit code, and is signed '- Claude'
- `t8` — OPERATOR: repeat the tutorial on Thor and AGX Orin. Thor: log in as thor through the JetKVM console (no gdm config change), export the session's DISPLAY/XAUTHORITY/DBUS address, follow the Thor tab windowed, free -g before the smoke (>=20 GB or skip), docker ps prod-\* health after; two MuJoCo-window screenshots >=4 s apart. Orin: log in on the DP-1 monitor, announce on the mesh, stop model-gear-vllm-associate, follow the Orin tab (checks 1-4 + gates, no training), restart the container and confirm healthy. Keep every improvisation in a notes file; the agent transcribes notes + logs into docs/verification/2026-09-07-thor-retest.md and -orin-retest.md
- `t9` — Fold the operator runs back: every improvisation from t8 becomes a tutorial fix (or a stated reason not to), replace the Thor/Orin 'operator re-run in progress' labels with the 2026-09-07 records, add the Thor/Orin screenshots, re-run `check_tutorial.py` and npm run build, second microduck-cli PR (Thor/Orin records, hosts.py `_THOR_VERIFIED`/`_ORIN_VERIFIED`, README rows, version-bump), then mark the jetson-ai-lab PR ready for review

## Actual Delivery

| Plan task | Status | What actually landed |
|-----------|--------|----------------------|
| `t1` | delivered | `microduck-on-jetson.mdx` + `.astro` wrapper in the fork (`../jetson-ai-lab`, branch `docs/microduck-on-jetson`), commits `3fdb6f2` (draft) and `31c2bad` (dry-run fixes + two screenshots); Applications / Robotics, order 5, authors OriNachum; eight Warning blocks incl. the five required; install pinned to 0.9.4; `npm run build` exit 0 |
| `t2` | delivered | `docs/tools/check_tutorial.py` + `tests/test_check_tutorial.py` (11 tests), merged `61a564c` after the TDD gate (1101 → 1112 passing) |
| `t3` | delivered, amended (`d4`) | pre-flight probed and saved (`00-preflight`): doctor 13/13, clones at the pins, `DISPLAY=:1`, 19 GB available; the fork already existed as `OriNachum/jetson-generative-ai-playground` and was cloned; `npm ci && npm run build` passed on it (via t1) |
| `t4` | delivered, amended (`d2`, `d3`, `d6`) | `docs/verification/2026-09-07-spark-retest.md` (`c1307b8`): steps 1, 5–11 run on the PyPI 0.9.4 wheel, windowed; live suites 11 passed 2 skipped / 12 passed 1 xfailed windowed; smoke failed OOM beside vLLM then passed (16 s) with `vllm-primary` paused; two window-only PNGs; steps 2–4 verified by result, not re-run; five tutorial fixes landed in `31c2bad` |
| `t5` | delivered | `check_tutorial.py`: 30 checked lines, 30 hit, 0 miss, 0 identity hits (6 provisioning fences `nocheck`); `npm run build` exit 0, page emitted at `dist/tutorials/microduck-on-jetson/`; results pasted in the Spark record |
| `t6` | partial — deferred (`d1`) | pointers landed on the branch (`7495769`: `_GB10_VERIFIED`, `tests/test_hosts.py`, README row) with the record and the checker; the version bump, CHANGELOG entry and the PR itself are NOT done — they follow this summary |
| `t7` | blocked — deferred (`d1`) | no PR opened on `NVIDIA-AI-IOT/jetson-ai-lab` yet; the fork branch is ready (`31c2bad`, DCO-signed, build green) |
| `t8` | blocked — operator | not started; Thor is at `graphical.target` with the JetKVM working but only the gdm greeter (no thor session); Orin shows its greeter on DP-1 |
| `t9` | blocked | depends on `t8` |

## Mid-work Decisions

- `d1` — Validate-delivery and summarize-delivery run after t5 and BEFORE any PR (t6, t7), with t8/t9 pending as operator work; the plan had the delivery legs after the merges — user direction 2026-09-07: 'when Tutorial is done, and before any PR'; Thor/Orin operator runs count as the tutorial's validation afterwards
- `d2` — The Spark training smoke needed model-gear-vllm-primary stopped for 4.5 min (06:34:22-06:38:55+03:00) and restarted healthy; beside it Warp saw 0 GiB and failed OOM — plan risk r4 materialised; operator chose 'stop vllm-primary for the smoke, restart after'
- `d3` — For the windowed run the Spark desktop session was unlocked and idle-delay/lock-enabled set to 0/false, then restored to 300/true at 06:40:04; the first attempt died when the session locked — GNOME lock closed the viewer and the body exited; the tutorial now warns about it
- `d4` — The jetson-ai-lab fork already existed as OriNachum/jetson-generative-ai-playground (upstream renamed); it was cloned to ../jetson-ai-lab instead of creating a new fork — gh repo fork reported the existing fork; a second fork is impossible
- `d5` — CLI defect found by the dry-run, deferred to its own PR per spec c10: env doctor on a wheel install reads docs/upstream-pins.md from a path the wheel does not ship, prints \[FAIL\] for two warning-severity pin checks under a healthy verdict — tutorial step 5 documents the symptom; fixing it changes CLI code, outside this plan's docs-only scope
- `d6` — Steps 2-4 of the tutorial (git clone, cargo build, uv sync) were not re-run on Spark; clones, daemons and venv pre-existed at the pins and were verified by result — re-running would have cost an hour for no new evidence; Thor/Orin operator runs exercise them from scratch

## Drift From Plan

| Plan item | Reason for divergence | Classification |
|-----------|------------------------|-----------------|
| `t6` (`d1`) | user direction 2026-09-07: 'when Tutorial is done, and before any PR'; Thor/Orin operator runs count as the tutorial's validation afterwards | `acceptable` |
| `t4` (`d2`) | plan risk r4 materialised; operator chose 'stop vllm-primary for the smoke, restart after' | `acceptable` |
| `t4` (`d3`) | GNOME lock closed the viewer and the body exited; the tutorial now warns about it | `acceptable` |
| `t3` (`d4`) | gh repo fork reported the existing fork; a second fork is impossible | `acceptable` |
| `t4` (`d5`) | tutorial step 5 documents the symptom; fixing it changes CLI code, outside this plan's docs-only scope | `needs-follow-up` |
| `t7` | draft PR not opened in this run — follows the summary by the same user direction as `d1`; no separate record | acceptable |
| `t8`, `t9` | not started — operator work that the user scheduled after the PRs (`c36`) | acceptable |
| `t2` | lint gates widened to `docs/tools` (black/isort/flake8) — not in the task text; passes | acceptable |
| `t4` (`d6`) | re-running would have cost an hour for no new evidence; Thor/Orin operator runs exercise them from scratch | `acceptable` |

## Evidence

All checks ran agent-side; **this repo has no `@pytest.mark.behavioral` marker
or `tests/behavioral/` folder**, so each is an ad-hoc run filed through
`/validate-delivery` as `o1–o12` / `e1–e12` (all approved; `e11`, `e12` are
**fail** records, kept as such). Deltas `b1–b4` approved; `b5`, `b6` filed after
the approval round and still `proposed`.

- tests: `uv run pytest -n auto --cov=microduck_cli` at `61a564c` — 1112 passed, 93 % (gate 60); `tests/test_hosts.py` at `7495769` — 17 passed (`e7`)
- live: `MICRODUCK_LIVE=1 uv run pytest -m live -n0 -v tests/live` — 11 passed, 2 skipped; with `MICRODUCK_LIVE_BODY=sim MICRODUCK_LIVE_SIM=1 MICRODUCK_LIVE_HEADLESS=0` — 12 passed, 1 xfailed (`e5`)
- tutorial commands: `docs/tools/check_tutorial.py` — commands=30 hit=30 miss=0 identity_hits=0 images=2 (`e1`)
- site: `npm run build` in `../jetson-ai-lab` @ `31c2bad` — exit 0 (`e8`)
- lint: black / isort / flake8 (incl. `docs/tools`) / bandit / `teken cli doctor --strict` / markdownlint — all clean (`08-gates.txt`)
- smoke: `microduck policy smoke Mjlab-Velocity-Flat-MicroDuck` — attempt 1 `ok: false` (CUDA OOM, Warp saw 0 GiB); attempt 2 `ok: true`, 16.29 s, max RSS 3.2 GB, with `model-gear-vllm-primary` stopped 06:34:22–06:38:55 and healthy after (`d2`)
- doctor on the wheel: `microduck env doctor` — `healthy`, exit 0, two `[FAIL] … pinned commit … unknown` lines (`e11`, fail)
- commits (this repo, branch `docs/jetson-retest-and-ai-lab-tutorial`): `50c16a0..7495769`; fork: `3fdb6f2..31c2bad`
- PRs / issues: none opened yet (`e12`, fail — by design, `d1`)
- logs: session scratchpad `spark-retest/` (`00-preflight` … `10-check-tutorial`, `smoke.json`, `smoke2.json`, `engine.stderr`, `senses.jsonl`, PNGs)

## Delivery Claims

| Claim | Confidence | Evidence |
|-------|------------|----------|
| The tutorial exists, builds, and every checked fenced command is quoted from a record or doc | high | fork `31c2bad`; `e1` (30/30), `e8` (build exit 0) |
| Following the tutorial's Spark path on the PyPI 0.9.4 wheel stands the duck in a visible MuJoCo window, runs skills, rules and the engine at 50 Hz | high | `docs/verification/2026-09-07-spark-retest.md`; PNGs `microduck-spark-stand-{1,2}.png`; `e5`, `e6`, `e9` |
| The 64-env training smoke passes on Spark | medium | passes only with the GPU free (`d2`); fails OOM beside a resident vLLM engine — the tutorial says so |
| Steps 2–4 (clone, cargo build, uv sync) work as written | low | verified by result only (`d6`); run from scratch on Thor/Orin on 2026-09-04, not on Spark this run |
| `env doctor` on the wheel reads 13/13 ok | **fails** | `e11`: two `[FAIL]` pin lines under a `healthy` verdict; documented in Step 5, CLI fix deferred (`d5`) |
| The hosts.py GB10 pointer and README row cite the new record without changing any verdict | high | `7495769`; `e7` (17 passed, 0 verdict lines in the diff) |
| The done condition (records on main, Thor session, PR URL) holds | unverified / **unmet** | `e12`: no PR exists yet; Thor/Orin not run |
| The tutorial works on Thor and Orin as written | unverified | operator run `t8` pending; Thor/Orin tabs carry "verified 2026-09-04 (headless); operator re-run in progress" |

## Remaining Work / Follow-up

- `t6` — version-bump (patch) + CHANGELOG, open the microduck-cli PR through the `cicd` lane (spec, plan, this summary, Spark record, checker, pointers), address review comments and SonarCloud, merge — next, per the user's order
- `t7` — open the **draft** PR from the fork to `NVIDIA-AI-IOT/jetson-ai-lab` main after `t6` merges; run `/code-review medium` on the tutorial and fix findings first
- `t8` — **operator**: log in as `thor` via the JetKVM console and as `orin` on the DP-1 monitor, follow the tutorial's Thor and Orin tabs (Thor: `free -g` ≥ 20 GB before the smoke, `docker ps` health after; Orin: mesh announcement, stop/restart `model-gear-vllm-associate`), keep an improvisation list
- `t9` — fold the operator notes into the tutorial, write the Thor/Orin 2026-09-07 records, update `_THOR_VERIFIED` / `_ORIN_VERIFIED` and README, second PR, mark the jetson-ai-lab PR ready
- **CLI defect (`d5`)** — ship the upstream pins inside the package (fallback when `docs/upstream-pins.md` is absent) and print `[WARN]` for warning-severity checks; own PR, then the tutorial's Step 5 note and pinned version can move to that release
- `b5`, `b6` — two deltas still `proposed`; confirm or reject
- Spark screenshots show the wheel-era duck; the Thor windowed shot (via JetKVM) is the one the spec's after-state (`c20`) names — pending `t8`
