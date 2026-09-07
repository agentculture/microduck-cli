# jetson-retest-and-ai-lab-tutorial

> microduck-cli re-verified on Jetson AGX Thor (with a visible MuJoCo window) and Jetson AGX Orin, and a Jetson AI Lab tutorial shows a reader how to run the MicroDuck simulation, the CLI and the training smoke on their own Jetson
> instruction: Done when both 2026-09-07 records are merged on main, Thor boots graphical with a thor session, and the jetson-ai-lab PR is open (merge is the maintainers' call and not part of done)

## Audience

- Two readers: (1) the operator of this repo re-verifying microduck-cli 0.9.4 on Thor and Orin and wanting to SEE the sim; (2) a Jetson AI Lab reader with a Jetson AGX Thor or AGX Orin on JetPack 7 who has never heard of MicroDuck and wants a standing, reacting simulated duck driven from one CLI, plus the training smoke where the board can run it

## Before → After

- Before: Thor and Orin were verified once (2026-09-04) headless, at CLI 2b00480/3c09fb0, with stale feature-branch checkouts left on both boxes; Thor is console-only with no monitor and Orin shows only a login greeter; the only public write-up is this repo's README, nothing on jetson-ai-lab.com
- After: Both boxes re-verified at 0.9.4 with dated records under docs/verification/, Thor running a graphical session where the MuJoCo duck is visibly standing (two screenshots seconds apart in the record), and a tutorial PR open against NVIDIA-AI-IOT/jetson-ai-lab that a Thor or Orin owner can follow end to end, stating plainly what does not work at the pin

## Why it matters

- A verification that nobody can watch is a claim, not a proof; a windowed run on Thor is the first time the duck is seen on a Jetson. Publishing on Jetson AI Lab puts the CLI in front of the people who own the hardware it was verified on, and the site's own contribution bar (verify on the hardware you name, mark what you did not test) forces the record to precede the prose

## Requirements

- Thor's GUI session must auto-login the 'thor' user (or an operator logs in once) so a real DISPLAY/XAUTHORITY/session bus exists for 'env up --sim' and for the screenshot recipe in .claude/skills/operate-microduck/SKILL.md 'Watch it' — a greeter alone (Orin's state today: gnome-shell owned by gdm, no user session) gives the sim nothing to open a window on
  - instruction: On Thor: sudo systemctl set-default graphical.target && sudo systemctl start gdm; add AutomaticLogin=thor under \[daemon\] in /etc/gdm3/custom.conf only with the user's go-ahead; verify with loginctl (a thor seat0 session) and pgrep -u thor gnome-shell; record exact commands and the revert (set-default multi-user.target)
  - honesty: after the change, 'loginctl list-sessions' shows a seat0 session owned by thor and DISPLAY/XAUTHORITY/`DBUS_SESSION_BUS_ADDRESS` can be read from that gnome-shell's /proc environ
- Each re-test produces a new dated record under docs/verification/ (2026-09-07-thor-retest.md, 2026-09-07-orin-retest.md) in the six-check / three-tier shape of 2026-09-04-thor-sanity.md, with outputs pasted unchanged and elisions marked; this time Thor's tier 2 runs WINDOWED (`MICRODUCK_LIVE_HEADLESS`=0 / env up --sim without --headless) and carries two screenshots a few seconds apart
  - instruction: Copy the 2026-09-04 record's section skeleton; run tiers 0-3 on Thor (tier 2 windowed, two gnome-screenshot PNGs >=4 s apart stored beside the record or under docs/verification/img/), tiers 0-2 on Orin; paste outputs unchanged with marked elisions; end with 'What was NOT verified'
  - honesty: every quoted line in a record exists in a log kept in the session scratchpad, and each record names the CLI commit it ran at
- The tutorial lands in NVIDIA-AI-IOT/jetson-ai-lab as src/content/tutorials/<subfolder>/<slug>.md plus src/pages/tutorials/<slug>.astro wrapping TutorialLayout; frontmatter needs title, description, category (enum in src/content/config.ts), tags, and authors (name + github); PR from a fork to main with 'npm run build' passing (schema validation), DCO sign-off recommended
  - instruction: Fork NVIDIA-AI-IOT/jetson-ai-lab under the user's account; branch docs/microduck-on-jetson; add src/content/tutorials/applications/microduck-on-jetson.md(x) + src/pages/tutorials/microduck-on-jetson.astro + public/images/tutorials/microduck-on-jetson/\*.png; npm ci && npm run build; commit -s; PR to main with the tested JetPack versions listed
  - honesty: 'npm run build' exits 0 in the fork at the PR head and the page renders at /tutorials/microduck-on-jetson under npm run dev
- The tutorial states what does not work at the pin, in the site's own voice (admonition warning): the duck stands, holds 50 Hz and runs skills but does not walk; Thor training needs the `microduck_rl`#39 branch or the SBSA-index override until it merges; Orin cannot train; relax drops the duck. CONTRIBUTING.md requires 'clearly identify anything you did not test' and 'verify commands on the hardware and JetPack you name' — hence the re-test precedes the tutorial
  - instruction: Tutorial carries admonition-warning blocks for: no walking at the pin (link sim-bringup record), Thor training via the PR-39 branch until it merges, Orin cannot train (`sm_87`), relax makes the duck fall, dry-run on a pipe moved nothing; each links the record that proved it
  - honesty: each of the five caveats in the tutorial names the docs/verification record or upstream issue it comes from; none is softened relative to the record
- The re-test records update `microduck_cli`/env/hosts.py HostInfo.verified strings and README's three-box table to point at the 2026-09-07 records (docs-only change to hosts.py constants + tests/`test_hosts.py` strings), bumped as one docs PR through the cicd lane
  - instruction: Edit `_GB10_VERIFIED`/`_THOR_VERIFIED`/`_ORIN_VERIFIED` strings in `microduck_cli`/env/hosts.py to name the 2026-09-07 records, update the matching asserts in tests/`test_hosts.py` and README's three-box table; version-bump patch; cicd open
  - honesty: tests/`test_hosts.py` passes with the new 'verified' strings and no HostInfo verdict (`torch_source_applies`, remediation) changes
- The tutorial covers three boxes — DGX Spark (GB10), Jetson AGX Thor and Jetson AGX Orin — as one path with per-box tabs or a matrix: Spark and Thor get sim + CLI + training smoke (Spark on upstream's cu129 source as shipped, Thor on the PR-39/SBSA override), Orin gets sim + CLI only. Spark's evidence is a 2026-09-07 re-run of the six checks at 0.9.4 on this box (windowed, its Spark record already has the screenshot recipe), so all three boxes cite same-day records at the same CLI version
  - instruction: Run the six checks on Spark at 0.9.4 exactly as docs/verification/2026-09-04-sim-bringup.md did (windowed via DISPLAY=:1, gnome-screenshot), write docs/verification/2026-09-07-spark-retest.md; in the tutorial use a Tabs block (.mdx) labelled DGX Spark / AGX Thor / AGX Orin for the box-specific steps (torch source, training availability), with the shared sim + CLI steps outside the tabs
  - honesty: each of the three tabs' commands is quoted from that box's 2026-09-07 record, and the Orin tab carries no training command
- Thor hosts a production docker stack (prod-api, prod-postgres, prod-minio, prod-worker, prod-scheduler, prod-notifier, prod-backup) and three vLLM engines with NO container memory limits, a 60 GB swapfile and `overcommit_memory`=0 (probed 2026-09-07). Tier 3 (the 64-env smoke) runs only after 'free -g' shows >=20 GB available; the record pastes free -g before and after, and 'docker ps' health of every prod-\* container after tier 3; if headroom is short the smoke is skipped and recorded as not run, never forced into swap
  - honesty: the Thor record shows free -g immediately before tier 3 and docker ps --format '{{.Names}} {{.Status}}' immediately after, with every prod-\* container still Up/healthy
- The records PR bumps the version and publish.yml pushes that version to PyPI on merge, so 'uv tool install microduck-cli' would install a release the records never ran. The tutorial's install line pins the verified version ('uv tool install microduck-cli==<verified>') and states the version each record ran at; the docs-only bump is noted as behaviour-identical
  - honesty: the tutorial's install command names an exact version that appears as the CLI version in all three 2026-09-07 records
- The tutorial's prerequisites state Python >=3.12 (CLI floor) and name the tested software: Thor JetPack 7 / L4T R38.2.2, Orin L4T R39, Spark DGX OS — all Python 3.12.3 boxes. JetPack 6 Orin ships Python 3.10; the tutorial tells those readers to 'uv python install 3.12' for the CLI and marks the RL venv (torch 2.9.1 cp312) as untested on JetPack 6. It also lists the Rust toolchain (rustup; 'source ~/.cargo/env' before env doctor, since a non-login shell lacks ~/.cargo/bin) and the cargo build of robotd as prerequisites with rough build times from the records
  - honesty: the prerequisites section lists Python 3.12, rustup + 'source ~/.cargo/env', the cargo build, and the tested L4T/JetPack per box; JetPack 6 is marked untested
- Tutorial screenshots capture the MuJoCo window only (gnome-screenshot -w on the focused viewer, or a crop), never a full desktop — no terminals, hostnames, browser tabs or other windows appear; the JetKVM web view is not itself screenshotted for the tutorial. The identity grep extends to image file names and alt text
  - honesty: every PNG under public/images/tutorials/microduck-on-jetson/ shows a single MuJoCo viewer window; a reviewer can find no terminal, hostname or other window in any of them

## Honesty conditions

- 'done' is claimed only when all three 2026-09-07 records are on main, Thor's seat0 session is thor-owned, and the jetson-ai-lab PR URL exists; merge of that PR is not claimed
- the record's pins table matches docs/upstream-pins.md byte for byte for the microduck rows, and the walk sentinel result is quoted (xfail) not omitted
- no 'policy smoke' or 'train' invocation appears in the Orin record or in the tutorial's Orin path
- the tutorial's prerequisites name only JetPack/L4T versions and boxes the records cover (Spark GB10, Thor L4T R38.2.2, Orin L4T R39); no other Jetson module is implied to work
- the before-state facts (target, greeter, stale branches, no site page) are each traceable to a probe quoted in the /scope entries s1-s5, s16
- the Thor record contains two screenshots of the MuJoCo window taken >=4 s apart and names the JetKVM as the display; if the window did not open, the record says so and the after-state is reported as partial
- the tutorial links each verification record it draws on; no number in the tutorial lacks a record line
- the Thor success lines (get-default, loginctl, gnome-screenshot, pytest tally, smoke exit) are pasted from the run logs unchanged
- the Orin success lines are pasted from the run logs unchanged; a missing Orin screenshot is stated, not implied
- a script diffs every fenced command in the tutorial against the three records and reports 0 misses; the identity grep and npm build exit codes are pasted
- the operator's Thor/Orin notes list each improvisation; the tutorial diff after step 4 addresses every one or says why not

## Success signals

- Thor: 'systemctl get-default' prints graphical.target, gdm is active, a thor-owned gnome-shell exists, and 'env up --sim' (no --headless) opens a MuJoCo window captured by gnome-screenshot; the live suite with `MICRODUCK_LIVE_BODY`=sim `MICRODUCK_LIVE_SIM`=1 `MICRODUCK_LIVE_HEADLESS`=0 reports 12 passed 1 xfailed; policy smoke exits 0 on the PR-39 venv
- Orin: env doctor 13/13 (training remediation as info), the fake-body live suite 11 passed 2 skipped, the sim-body suite 12 passed 1 xfailed, gates green at 0.9.4; a screenshot of the duck on Orin's connected DP-1 monitor if a user session is opened
  - instruction: On Orin: env doctor --json; `MICRODUCK_LIVE`=1 live suite twice (fake body; then `MICRODUCK_LIVE_BODY`=sim `MICRODUCK_LIVE_SIM`=1); run the six gates; if a user session is opened on DP-1, env up --sim windowed + gnome-screenshot, else record 'no user session, headless'
- Tutorial: 'npm run build' passes in the fork with the new content + wrapper; every fenced command in the tutorial appears verbatim in one of the two 2026-09-07 records; grep for home paths and account names in the tutorial and images = 0; PR opened to NVIDIA-AI-IOT/jetson-ai-lab main with DCO sign-off
  - instruction: In the fork: npm ci && npm run build (paste exit code); scripts/check-tutorial-commands.py (new, in this repo's scratch or docs/tools) extracts fenced bash lines from the tutorial and greps them in the three 2026-09-07 records; grep -rn '/home/\|orinachum' on the tutorial + image names; gh pr create against NVIDIA-AI-IOT/jetson-ai-lab main with commit -s
- The operator can follow the tutorial on Thor and Orin without asking the agent anything the tutorial should have said; every place they had to improvise becomes a tutorial fix before the draft PR is marked ready

## Scope / boundaries

- Re-test at the pinned upstream commits — pollen-robotics/microduck sim-remote-io @0cd676d and `microduck_rl` develop @29e887e are still the branch heads (checked 2026-09-07 via gh api) and `microduck_rl`#39 is still OPEN, so there is nothing to re-pin to; docs/upstream-pins.md stays untouched and locomotion stays 'not achieved' (`test_sim_body_walks_forward_on_move` remains xfail)
  - instruction: Before each run: gh api the two branch heads and gh pr view `microduck_rl`#39; paste the shas in the record's pins table; do not touch docs/upstream-pins.md
- Orin GPU training stays out of scope: at this pin the only torch 2.9.1 cp312 CUDA-13 wheel carries no `sm_87` kernels (docs/verification/2026-09-04-orin-sanity.md, `microduck_cli`/env/hosts.py `_ORIN_NO_TRAINING_REMEDIATION`). The Orin re-test covers checks 1-4 (sim, fake, rules, gates) and the tutorial routes Orin readers to sim+CLI only, training on Thor/Spark/HF Jobs
  - instruction: On Orin run checks 1-4 and the gates only; quote env doctor's `host_class` info line (the `sm_87` remediation) as the evidence that training was not attempted; the tutorial's training section is labelled 'Thor only' with the Orin sentence pointing at the record

## Non-goals

- No change to `microduck_cli` code, tests or the CLI contract is planned by this idea; the deliverables are verification records, a Thor GUI restore, and an external tutorial. If the re-test finds a defect, it is filed and fixed as its own PR, not folded into the tutorial work
- No CI runner, self-hosted GitHub runner, or resident service on Thor/Orin is reconfigured beyond the GUI target; the Thor GUI change is the ONE system-state change (set-default graphical.target + start gdm, and optionally AutomaticLogin in /etc/gdm3/custom.conf), made with the user's go-ahead and recorded with the exact commands so it can be reverted

## Assumptions

- Thor currently boots to multi-user.target with gdm.service inactive; gdm3 46, gnome-shell 46, xserver-xorg and gnome-remote-desktop are installed, /etc/gdm3/custom.conf sets WaylandEnable=false, and BOTH DRM connectors (card2-DP-1, card2-HDMI-A-1) report disconnected — restoring a GUI needs 'systemctl set-default graphical.target' plus 'systemctl start gdm' AND a display to render on (a physically attached monitor, or a virtual/headless display)
- Thor's `microduck_rl` clone sits on fix/jetson-thor-torch-source @a30a9e4 (the PR #39 branch), so 'env doctor' `rl_pinned_commit` fails there BY DESIGN as docs/verification/2026-09-04-thor-sanity.md records; the Thor re-test keeps that branch for the training tier and records the doctor line as expected, not as a regression
- Thor's microduck-cli checkout is feat/thor-sanity @7e88d86 and Orin's is docs/orin-sanity @8733c10 (dirty: untracked .eidetic/); both are behind origin/main 75ff0b6 (0.9.4). The re-test runs on a fresh checkout of main (or the PyPI 0.9.4 wheel via 'uv tool install microduck-cli' — pypi latest is 0.9.4), recording which one
- Memory on the shared boxes: Thor shows 28 GB available of 122 (vLLM primary/rerank/embed + prod-\* containers up); Orin shows 9 GB available of 61 (model-gear-vllm-associate resident). The 2026-09-04 Orin run stopped that vLLM container with the operator's explicit go-ahead and restarted it afterwards; Thor ran beside its engines. The re-test follows the same decisions unless the user says otherwise, and every stop/start is logged with timestamps
- Category 'Applications' with section 'Robotics' (the slot reachy-mini-jetson-assistant.md already occupies, order 4) is the natural home; 'VLA' is for vision-language-action models and would misfile a MuJoCo RL sim. Tags follow the neighbours: robotics, jetson-thor, jetson-orin, mujoco, reinforcement-learning, simulation, microduck
- Tutorial images (the windowed MuJoCo duck on Thor, the Orin capture) come from the re-test screenshots, scrubbed of home paths and account names as README PR #7 did (grep '/home/\|orinachum' = 0), stored at public/images/tutorials/microduck-on-jetson/
- The tutorial does not copy upstream code or docs: it links pollen-robotics/microduck and `microduck_rl`, names the pinned commits, and drives everything through the microduck CLI — the standing user directive that upstream code is never copied or cited applies to the tutorial too
- The user has a merged PR on jetson-ai-lab (#316) and a project entry there (Tau), so the contribution path is a fork PR to main under their GitHub identity; the microduck-cli mesh agent signs nothing on the external repo — commits carry the user's identity with a DCO sign-off, and the PR body is signed '- Claude' per the global posting rule

## Scope exploration

- `s1` — `thor: systemctl get-default / gdm.service / /sys/class/drm/*/status / dpkg (probed 2026-09-07 over ssh)`: multi-user.target, gdm inactive (gdm.service.d/override.conf is NVIDIA's stock ExecStartPre= drop-in, not a local disable), no X socket, DP-1 and HDMI-A-1 both 'disconnected'; gdm3/gnome-shell/xserver-xorg/gnome-remote-desktop installed; WaylandEnable=false. A GUI is one set-default + one start away, but there is no monitor to draw on
  - seeds: `c2`
- `s2` — `orin: loginctl / pgrep gnome-shell / /etc/gdm3/custom.conf (probed 2026-09-07)`: graphical.target with gdm active, DP-1 'connected', but the only seat0 session is the gdm greeter (gnome-shell runs as user gdm; no orin desktop session). A windowed sim on Orin needs a user login or AutomaticLogin in custom.conf; the Spark screenshot recipe assumes the display owner's session bus exists
  - seeds: `c3`
- `s3` — `gh api branches: microduck/sim-remote-io, microduck_rl/develop; gh pr view microduck_rl#39`: both heads unchanged since the 2026-09-02 pin; PR #39 (Jetson torch source split) open, not merged. No re-pin is available, so the walk sentinel stays xfail and the tutorial cannot promise walking
  - seeds: `c4`
- `s4` — `thor: ~/git/microduck_rl (git rev-parse); README.md three-box table row for Thor`: clone on the PR-39 branch a30a9e4, clean; README row already says `rl_pinned_commit` fails there until upstream merges and this repo re-pins
  - seeds: `c5`
- `s5` — `thor/orin: ~/git/microduck-cli branch + git fetch origin; pypi.org/pypi/microduck-cli/json`: both box checkouts are stale feature branches; main is 0.9.4 and PyPI serves 0.9.4 — the tutorial can honestly say 'uv tool install microduck-cli' once the re-test uses it
  - seeds: `c6`
- `s6` — `docs/verification/2026-09-04-{thor,orin}-sanity.md, 2026-09-04-sim-bringup.md`: the record format (box+pins table, tier 0 venv, checks 1-6, 'What was NOT verified', logs named) is established; Thor's previous run was explicitly headless ('no graphical session, DISPLAY unset') — the windowed run is the new thing
  - seeds: `c7`
- `s7` — `thor/orin: free -g, docker ps, pgrep vllm (2026-09-07)`: Thor 28 GB free (was 42-46 GB on 2026-09-04) with three vLLM engines resident; Orin 9 GB free with the associate vLLM up. Sim + gnome-shell fit; the 64-env smoke on Thor previously wanted ~46 GB headroom, so memory is a live risk
  - seeds: `c8`
- `s8` — `microduck_cli/env/hosts.py docstring + remediations; orin: .venv torch import`: hosts.py already encodes 'Orin: not trainable at this pin' and 'Thor: trainable with a local override'; Orin's venv torch import fails without `LD_LIBRARY_PATH` exactly as the record says
  - seeds: `c9`
- `s9` — `CLAUDE.md 'Architecture' + README 'Proof — three boxes'`: the CLI is at 0.9.4 with 1101 tests; this idea is operations + documentation, not engineering. Keeps the change surface honest for the version-check job (still bumps, docs-only)
  - seeds: `c10`
- `s10` — `jetson-ai-lab: TUTORIAL_TEMPLATE.md, CONTENT_GUIDE.md, CONTRIBUTING.md, src/content/config.ts (clone @57994bc, 2026-09-03)`: two-file recipe (content + .astro wrapper), zod schema with a fixed category enum, authors field, images under public/images/tutorials/<slug>/, admonitions/tabs/mermaid available in .mdx; 'npm run build' is the validator
  - seeds: `c11`
- `s11` — `jetson-ai-lab: src/content/tutorials/applications/reachy-mini-jetson-assistant.md, vla/*, src/data/categories.json`: the only Robotics-sectioned tutorial is the Reachy Mini one (Applications/Robotics, order 4); VLA holds GR00T and OpenPi on Thor. Both Thor tutorials open with 'Why Jetson AGX Thor?', prerequisites, numbered steps, troubleshooting, references
  - seeds: `c12`
- `s12` — `jetson-ai-lab CONTRIBUTING.md 'Verify Jetson commands on the hardware…'; operate-microduck SKILL.md 'Say this before you run it'`: the site's contribution bar and this repo's honesty rule coincide: the tutorial can only carry commands the re-test ran on the named JetPack versions (Thor L4T R38.2.2 / JetPack 7, Orin L4T R39)
  - seeds: `c13`
- `s13` — `docs/deliveries/2026-09-04-readme-as-proof.md evidence e5 (identity scrub)`: the scrub rule and its check already exist; reuse it for the tutorial's captures
  - seeds: `c14`
- `s14` — `memory: microduck-upstream-state-2026-09 (user directive); docs/upstream-pins.md`: never copy/vendor upstream code; implement documented CLIs and link. The tutorial inherits this
  - seeds: `c15`
- `s15` — `gh pr list --author OriNachum on NVIDIA-AI-IOT/jetson-ai-lab; src/content/projects/autonomous-intelligence.md; gh repo list --fork`: PR #316 merged; project entry exists; no jetson-ai-lab fork under the user's account yet, so one must be created before the tutorial PR
  - seeds: `c16`
- `s16` — `thor: systemd display-manager.service -> gdm3.service; /etc/gdm3/custom.conf; journalctl (no trace of the set-default)`: gdm is a static unit reached through graphical.target; the box was deliberately left at multi-user.target. Flipping it is reversible with set-default multi-user.target
  - seeds: `c17`
- `s17` — `jetson-ai-lab: grep 'DGX Spark|GB10' — setup/intro-to-jetson.md, applications/live-vlm-webui.md, model-optimization/finetune-on-jetson.mdx, src/data/benchmarks.json; this box: nvidia-smi GB10, 19 GB free of 121`: the site already treats DGX Spark as a first-class target (device matrix rows, a fine-tuning playbook link, benchmark entries), so a Spark column in the tutorial fits house style; the Spark box has 19 GB free today (vLLM lobes resident), a fraction of the 2026-09-04 headroom
  - seeds: `c27`
- `s18` — `challenge pass / lifecycle lens: thor systemctl/loginctl/drm re-probe after the user's restore`: the target flip is done; what remains for a windowed run is a thor-owned session (login via JetKVM or AutomaticLogin=thor) and a connected sink
  - seeds: `c30`
- `s19` — `challenge pass / adjacent-systems lens: thor docker inspect HostConfig.Memory, swapon, /proc/sys/vm/overcommit_memory`: an unlimited-memory production stack shares the box; a memory-hungry training smoke would degrade it through swap before any OOM — a containment threshold is needed spec-side
  - seeds: `c31`
- `s20` — `challenge pass / hidden-dependency lens: gh pr view microduck_rl#39 headRepositoryOwner`: the only working Thor training recipe lives on a personal fork; a public tutorial that depends on it needs an explicit choice
- `s21` — `challenge pass / unstated-assumptions lens: orin loginctl (greeter only) vs claim c17 and instruction c23`: c23 says 'if a user session is opened' without saying by whom; c17 forbids the change that would open it — the frame contradicted itself here
- `s22` — `challenge pass / data-flow lens: tests/live/test_live_cli.py (env = dict(os.environ), MICRODUCK_LIVE_HEADLESS) and env/stack.py base_env=os.environ`: clean — DISPLAY/XAUTHORITY exported in the ssh shell reach duck-body through both the live suite and env up; no code change needed for a windowed run
- `s23` — `challenge pass / concurrency lens: thor ~/git/actions-runner, pgrep Runner.Listener`: clean — the runner is installed but no listener process is running, so no CI job can land on Thor mid-run; re-check pgrep before tier 3
- `s24` — `challenge pass / operations lens: spark loginctl seat0 tty2, /tmp/.X11-unix/X1`: clean — the Spark record's DISPLAY=:1 recipe still matches the live session
- `s25` — `challenge pass / reversibility lens: orin docker ps model-gear-vllm-associate (healthy), 2026-09-04 record's stop/start timestamps`: the stop/start is reversible and was rehearsed; residual: the colleague backend (ask-colleague) is unavailable to the whole mesh while it is down — announce on the mesh channel before stopping
- `s26` — `challenge pass / observability lens: thor /sys/class/drm (all cards), xrandr on :0 refused without the gdm cookie, JetKVM view (user)`: the sysfs connector state disagrees with what the JetKVM shows; use loginctl + the JetKVM view as the observable, and read xrandr with the session owner's XAUTHORITY once thor is logged in
  - seeds: `c35`

## Decisions

- Thor's display is a JetKVM (KVM-over-IP with an HDMI input that presents an EDID sink): plug its HDMI into Thor so card2-HDMI-A-1 reads 'connected', run gdm on that output, and watch the MuJoCo window in the JetKVM browser view as well as via gnome-screenshot. No virtual display work
- The JetKVM is already cabled to Thor's HDMI but its USB power may be off (user, 2026-09-07) — hence the 0-byte EDID. Step 1 of the run is: power the JetKVM, re-read /sys/class/drm/card2-HDMI-A-1/status until 'connected', then set-default graphical.target + start gdm
- Thor GUI restore was done by the user before the run (probed 2026-09-07): graphical.target, gdm active, Xorg on :0 with the gdm greeter's gnome-shell; no thor seat0 session yet (AutomaticLogin commented out) and HDMI-A-1 still shows no EDID while the JetKVM's USB power is being checked. Plan step 1 becomes verify + log in, not flip
- Thor's kernel DRM connector status is NOT the readiness signal: with the JetKVM powered and the user watching the greeter through it, card2-DP-1 and card2-HDMI-A-1 still read 'disconnected' with no EDID (only card0/1/2 exist, no other connector nodes). Readiness for the windowed run = a thor-owned seat0 session in loginctl plus the JetKVM view; the record quotes both and notes the connector reading
- Order of work (user, 2026-09-07, supersedes c25): (1) write the tutorial first, from the existing 2026-09-04 records and this spec; (2) the agent tests the tutorial on Spark by following it literally, windowed, producing docs/verification/2026-09-07-spark-retest.md and fixing the tutorial where it misled; (3) merge the PR in this repo (spec, Spark record, hosts.py pointer, README row) and open a DRAFT PR on NVIDIA-AI-IOT/jetson-ai-lab; (4) the operator (user) repeats the tutorial on Thor (windowed via JetKVM) and AGX Orin, and their runs are the Thor/Orin records; the draft is marked ready after step 4
- The tutorial's Thor and Orin sections are written from the 2026-09-04 records and labelled 'verified 2026-09-04 (headless), operator re-run in progress' until step 4 lands; Spark is the only box the agent re-verifies at the pinned CLI version before the draft PR opens. This replaces c7's agent-run Thor/Orin records with operator-run ones and makes the operator's run the usability test of the tutorial itself

## Open parks

- [unknown_nonblocking] Thor's MuJoCo window over a virtual display: whether duck-body's MuJoCo viewer (GLFW) opens on an Xorg 'virtual' head with the tegra/nvidia driver and no connected monitor is unverified; if it does not, the fallback is a monitor, or windowed on Orin (monitor connected) + headless on Thor with duck monitor --json as the proof
- [unknown_nonblocking] Whether the 64-env training smoke still passes on Thor with only ~28 GB available beside three vLLM engines (it had 46 GB on 2026-09-04) — decided by the run, not now
- [unknown_nonblocking] Whether Jetson AI Lab maintainers accept a tutorial whose training tier depends on an unmerged upstream PR (`microduck_rl`#39); mitigations: wait for the merge and re-pin, or document the override as an explicit 'until #39 merges' step
- [unknown_nonblocking] The JetKVM is not currently cabled to Thor: both DRM connectors show status 'disconnected' with a 0-byte EDID (probed 2026-09-07), and no JetKVM answered mDNS from Spark. Step 1 needs the operator to plug its HDMI into Thor and share its web address before the GUI can be verified
- [unknown_nonblocking] Whether gdm's Xorg on Thor keeps running once the JetKVM's EDID appears (hotplug on the tegra driver may restart the session), and whether AutomaticLogin is needed at all if the user logs in once through the JetKVM console — settled by the run
- [unknown_nonblocking] Residual after the pass: the MuJoCo viewer's behaviour on Thor's Xorg has never been observed on any Jetson; the whole windowed proof rests on it opening. Fallback recorded in v1
