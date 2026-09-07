# Build Plan — jetson-retest-and-ai-lab-tutorial

slug: `jetson-retest-and-ai-lab-tutorial` · status: `exported` · from frame: `jetson-retest-and-ai-lab-tutorial`

> microduck-cli re-verified on Jetson AGX Thor (with a visible MuJoCo window) and Jetson AGX Orin, and a Jetson AI Lab tutorial shows a reader how to run the MicroDuck simulation, the CLI and the training smoke on their own Jetson

## Tasks

### t1 — Write the tutorial draft in a fork clone of jetson-ai-lab: src/content/tutorials/applications/microduck-on-jetson.mdx (Applications / Robotics, order 5, authors = the user) + src/pages/tutorials/microduck-on-jetson.astro; shared sim+CLI steps outside Tabs, per-box tabs DGX Spark / AGX Thor / AGX Orin; written from the 2026-09-04 records with Thor/Orin marked 'verified 2026-09-04 headless, operator re-run in progress'

- covers: c11, h5, c13, h6, c18, h9, c21, h12, c27, h7, c31, c32, h18, c33, h19, c9, h4
- acceptance:
  - frontmatter validates against src/content/config.ts (category Applications, section Robotics, tags, authors)
  - five warning admonitions present: no walking at the pin, Thor training via the fork branch OriNachum/`microduck_rl` fix/jetson-thor-torch-source until #39 merges, Orin cannot train (`sm_87`), relax makes the duck fall, dry-run on a pipe moved nothing — each linking its record or PR
  - prerequisites name Python 3.12 (uv python install 3.12 for JetPack 6), rustup + 'source ~/.cargo/env', the cargo build of robotd with build time from the records, and the tested L4T/JetPack per box; JetPack 6 marked untested
  - install line is 'uv tool install microduck-cli==0.9.4' with the sentence that every record ran at 0.9.4
  - the Thor tab carries the memory guard: run 'free -g' and proceed to the smoke only with >=20 GB available, check 'docker ps' health afterwards; the Orin tab has no training command
  - no upstream code is copied; microduck, `microduck_rl`, the pinned commits and #39 are linked

### t2 — Write docs/tools/`check_tutorial.py` in this repo: extracts every fenced bash line from a tutorial file and greps each against the named verification records (0 misses required), greps the tutorial + image filenames + alt text for '/home/' and the account name, and lists the PNGs under the tutorial's image dir for the single-window review

- covers: c24, c34
- acceptance:
  - running it on the tutorial prints per-command hit/miss with the record file that matched and exits non-zero on any miss or identity hit
  - unit-tested against a fixture tutorial + fixture record in tests/ (flake8/black/isort clean, no new runtime deps)

### t3 — Spark pre-flight (read-only except the fork): probe the seat0 session and /tmp/.X11-unix, both clones at the pins (docs/upstream-pins.md), 'uv run microduck env doctor' healthy, free -g; create the user's fork of NVIDIA-AI-IOT/jetson-ai-lab (gh repo fork --clone=false) and clone it to ../jetson-ai-lab (Node 20 + npm ci must work on Spark for 'npm run build')

- covers: c19, h10
- acceptance:
  - a pre-flight block with the probe outputs is ready to paste at the top of docs/verification/2026-09-07-spark-retest.md
  - OriNachum/jetson-ai-lab exists and 'npm ci && npm run build' passes on the untouched fork clone (exit code pasted)

### t4 — Spark dry-run of the tutorial, windowed: follow the tutorial's Spark path literally on this box (DISPLAY=:1, XAUTHORITY of the seat0 session), run the six checks + both live suites (fake; sim with `MICRODUCK_LIVE_HEADLESS`=0) + the 64-env smoke, take two MuJoCo-window-only screenshots >=4 s apart (gnome-screenshot -w, then verify no other window/terminal in the PNG), write docs/verification/2026-09-07-spark-retest.md in the 2026-09-04 record shape, and fix the tutorial wherever it misled — every fix listed in the record

- depends on: t1, t3
- covers: c4, h2, c7, h3, c27, h7, c34, h20
- acceptance:
  - record has the pins table matching docs/upstream-pins.md byte for byte, the CLI version (0.9.4), pasted outputs with marked elisions, 'What was NOT verified', and the walk sentinel quoted as xfail
  - two PNGs at ../jetson-ai-lab/public/images/tutorials/microduck-on-jetson/ each showing a single MuJoCo viewer window, referenced from the tutorial with alt text
  - every quoted line traces to a log kept in the session scratchpad; the record names the log files

### t5 — Run the tutorial checks: docs/tools/`check_tutorial.py` against the tutorial and the 2026-09-04 Thor/Orin + 2026-09-07 Spark records (0 misses, 0 identity hits), 'npm run build' in the fork at the branch head (exit 0), and 'npm run dev' page render at /tutorials/microduck-on-jetson (screenshot of the rendered page kept in scratch, not committed)

- depends on: t2, t4
- covers: c24, h15, c34, h20
- acceptance:
  - the three exit codes and the check script's summary are pasted into the Spark record's gates section

### t6 — Repo PR in microduck-cli: add the Spark record, point `_GB10_VERIFIED` in `microduck_cli`/env/hosts.py at it (Thor/Orin pointers unchanged until the operator runs), update tests/`test_hosts.py` and README's three-box Spark row, commit docs/tools/`check_tutorial.py` + tests, version-bump patch with CHANGELOG entry, cicd open, ask-colleague review, merge when green

- depends on: t5
- covers: c26, h16, c19, h10, c32, h18
- acceptance:
  - pytest -n auto, teken cli doctor --strict, black/isort/flake8/bandit, markdownlint all green at the PR head; HostInfo verdicts (`torch_source_applies`, remediation) unchanged
  - PR merged on main; the spec and frame from commits 50c16a0..6084c65 are in it

### t7 — Open a DRAFT PR from OriNachum/jetson-ai-lab branch docs/microduck-on-jetson to NVIDIA-AI-IOT/jetson-ai-lab main: commits signed off (-s) under the user's identity, body lists tested hardware (Spark GB10 windowed 2026-09-07; Thor/Orin 2026-09-04 headless, operator re-run in progress), the build exit code, and is signed '- Claude'

- depends on: t5, t6
- covers: c1, h8, c11, h5, c24, h15
- acceptance:
  - gh pr view shows isDraft=true against NVIDIA-AI-IOT/jetson-ai-lab:main and the site CI build passes on it

### t8 — OPERATOR: repeat the tutorial on Thor and AGX Orin. Thor: log in as thor through the JetKVM console (no gdm config change), export the session's DISPLAY/XAUTHORITY/DBUS address, follow the Thor tab windowed, free -g before the smoke (>=20 GB or skip), docker ps prod-\* health after; two MuJoCo-window screenshots >=4 s apart. Orin: log in on the DP-1 monitor, announce on the mesh, stop model-gear-vllm-associate, follow the Orin tab (checks 1-4 + gates, no training), restart the container and confirm healthy. Keep every improvisation in a notes file; the agent transcribes notes + logs into docs/verification/2026-09-07-thor-retest.md and -orin-retest.md

- depends on: t7
- covers: c3, h1, c4, h2, c7, h3, c9, h4, c20, h11, c22, h13, c23, h14, c31, h17, c38, h21
- acceptance:
  - Thor record: loginctl shows a thor seat0 session; get-default graphical.target; live suite 12 passed 1 xfailed windowed; smoke exit code or 'skipped: N GB available'; prod-\* all Up/healthy after; two screenshots; connector reading noted vs the JetKVM view
  - Orin record: env doctor 13/13 with the `sm_87` info line; fake suite 11 passed 2 skipped; sim suite 12 passed 1 xfailed; gates green; vLLM stop/start timestamps and healthy status; no train/smoke invocation anywhere
  - the operator's notes list each improvisation (or state there were none)

### t9 — Fold the operator runs back: every improvisation from t8 becomes a tutorial fix (or a stated reason not to), replace the Thor/Orin 'operator re-run in progress' labels with the 2026-09-07 records, add the Thor/Orin screenshots, re-run `check_tutorial.py` and npm run build, second microduck-cli PR (Thor/Orin records, hosts.py `_THOR_VERIFIED`/`_ORIN_VERIFIED`, README rows, version-bump), then mark the jetson-ai-lab PR ready for review

- depends on: t8
- covers: c38, h21, c13, h6, c26, h16, c1, h8
- acceptance:
  - tutorial diff after t8 addresses each listed improvisation; `check_tutorial.py` 0 misses against all three 2026-09-07 records; npm run build exit 0
  - gh pr view on the jetson-ai-lab PR shows isDraft=false; the second microduck-cli PR is merged

## Risks

- [unknown_nonblocking] MuJoCo's GLFW viewer has never been observed on a Jetson Xorg session; the Thor windowed proof (t8) rests on it opening. Fallback: Thor headless + duck monitor --json, windowed proof from Spark only, said so in the record and tutorial (task t8)
- [unknown_nonblocking] Thor has ~28 GB available beside an unlimited-memory production stack and a 60 GB swapfile; the 64-env smoke may be skipped by the >=20 GB guard (task t8)
- [unknown_nonblocking] Jetson AI Lab maintainers may not accept a training step that depends on a personal fork branch (#39 unmerged); mitigation: the draft PR states it, and t9 can swap to upstream once merged (task t7)
- [unknown_nonblocking] Spark has ~19 GB available today with vLLM lobes resident; the smoke ran with far more headroom on 2026-09-04 — t4 may need the same >=20 GB guard or a lobe pause with the user's go-ahead (task t4)
- [unknown_nonblocking] gdm's Xorg on Thor may restart when the JetKVM's EDID appears (hotplug); the operator re-checks loginctl after logging in (task t8)
- [unknown_nonblocking] Node 20 / npm availability on Spark for 'npm run build' is unverified until t3 (task t3)
