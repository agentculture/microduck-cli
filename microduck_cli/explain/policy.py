"""Explain entries and verb list for the ``policy`` noun.

Owned by the ``policy`` noun task: adding a ``policy`` verb means editing this
module (``VERBS`` + ``ENTRIES``), ``cli/_commands/policy.py`` and
``tests/test_policy.py`` — nothing else. See :mod:`microduck_cli.explain.env`
for the shared conventions.

Every entry here links to the upstream page it implements against
(``pollen-robotics/microduck``'s ``docs/robot/cheatsheet.md`` /
``docs/policy-manifest.md``, or ``pollen-robotics/microduck_rl``'s
``README.md`` / ``AGENTS.md`` / ``scripts/hf/README.md``) — see the
"Approved deviation d1" section of ``cli/_commands/policy.py`` for why the
wire methods these verbs use (``robot.policies``, ``robot.loadPolicy``,
``robot.reloadPolicies``, ``robot.setSkill``, ``robot.removeSkill``) are
transcribed from **main**, not the pinned ``sim-remote-io`` commit, and why
they need a daemon reporting API >= 18.
"""

from __future__ import annotations

_CHEATSHEET_URL = "https://github.com/pollen-robotics/microduck/blob/main/docs/robot/cheatsheet.md"
_POLICY_MANIFEST_URL = (
    "https://github.com/pollen-robotics/microduck/blob/main/docs/policy-manifest.md"
)
_RL_README_URL = "https://github.com/pollen-robotics/microduck_rl/blob/develop/README.md"
_RL_AGENTS_URL = "https://github.com/pollen-robotics/microduck_rl/blob/develop/AGENTS.md"
_RL_HF_README_URL = (
    "https://github.com/pollen-robotics/microduck_rl/blob/develop/scripts/hf/README.md"
)

_D1_NOTE = (
    "Needs a daemon reporting API >= 18 (microduck main); the pinned sim-remote-io "
    "build (API 16) has no policy channel at all."
)

VERBS: list[str] = [
    "policy overview — describe the policy noun (train, export, publish, install policies)",
    "policy list — read policy slots + skills (robot.policies, or robot.subscribe on API 16)",
    "policy load <slot> <source> — robot.loadPolicy one slot from an absolute path,"
    " gated; a non-path source prints the robotctl line instead",
    "policy reset <slot> — put one slot (or, with none named, all seven) back to its own"
    " policy, gated",
    "policy add <name> <repo> — robot.setSkill from an absolute path, gated; a"
    " non-path repo prints the robotctl line instead",
    "policy remove <name> — robot.removeSkill, gated",
    "policy search <query> — print the robotctl line to search the Hub (updaterd, unreachable)",
    "policy check — print the robotctl line to check for policy updates (updaterd, unreachable)",
    "policy update — print the robotctl line to update policies (updaterd, unreachable)",
    "policy pad bindings — print the robotctl line to list pad button bindings",
    "policy pad bind <button> <skill> — print the robotctl line to bind a pad button",
    "policy pad reset <button> — print the robotctl line to reset one (or, with none"
    " named, all) pad bindings",
    "policy smoke <task> — the mandatory 64-env/5-iteration smoke test",
    "policy train <task> — train a task (refuses without a recorded smoke pass)",
    "policy play <task> — play back a trained task",
    "policy export <task> — export a task to an ONNX artifact",
    "policy publish — publish an ONNX policy to the Hub",
    "policy infer — run local ONNX inference for a walking policy",
    "policy install <kind> <name> <repo> — print the robotctl line to add|load a policy",
]

_POLICY = f"""\
# microduck-cli policy

Noun group for the *policy lifecycle*: read and write the robot's policy
slots and skills, train a policy, export it to a deployable artifact,
publish it, and install it onto a duck.

Two channels this noun reaches, and two it prints instead of reaching:

* `robotd`'s documented **policy channel** (`robot.policies`,
  `robot.loadPolicy`, `robot.reloadPolicies`, `robot.skills`,
  `robot.setSkill`, `robot.removeSkill`) — needs a daemon reporting API >= 18.
  The pinned sim-remote-io build answers API 16 and has none of these; every
  verb that needs them exits 2 naming that. Neither `robot.loadPolicy` nor
  `robot.setSkill` can fetch a Hub repo — their file field is an absolute
  local path the daemon opens as-is — so `policy load`/`policy add` only send
  the real call for an absolute-path source; anything else prints the
  `robotctl` line instead.
* `updaterd`'s `policy.*` fetch namespace (search/check/update) and pad's
  `[pad]` config (bindings/bind/reset) are **not sockets this CLI opens** —
  those verbs print the exact `robotctl` line to run on the robot instead.
* The `microduck_rl` train lane (smoke/train/play/export/publish/infer/
  install) builds `uv run ...` argv and runs it through an injected runner;
  it never imports the RL repo.

## Usage

    microduck-cli policy
    microduck-cli policy overview
    microduck-cli policy list --json

## See also

- {_CHEATSHEET_URL}
- {_POLICY_MANIFEST_URL}
- {_RL_README_URL}
"""

_POLICY_OVERVIEW = """\
# microduck-cli policy overview

Read-only description of the `policy` noun: slots/skills, the lifecycle
verbs, and today's status. Descriptive, so it never hard-fails — a stray
positional argument is accepted and ignored.

## Usage

    microduck-cli policy overview
    microduck-cli policy overview --json
"""

_POLICY_LIST = f"""\
# microduck-cli policy list

Reads policy slots (`walk`, `stand`, `unavailable`) and skills.

* Daemon API >= 18: `robot.policies`.
* Daemon API < 18 (the pinned sim-remote-io build, API 16): falls back to
  `robot.subscribe`'s `walk`/`stand`/`unavailable`/skill-file fields — the
  only place API 16 reports which skills have a policy behind them.

The JSON payload's `source` field says which one actually answered.

## Usage

    microduck-cli policy list
    microduck-cli policy list --duck duck-a --json

## See also

- {_CHEATSHEET_URL}
"""

_POLICY_LOAD = f"""\
# microduck-cli policy load <slot> <source>

`robot.loadPolicy {{slot, path}}` opens a file already on the robot's own
disk — it does not fetch one. So `source` is inspected first: an **absolute
path** is sent as `path` in a real, gated call (`--apply`, TTY prompt, or a
zero-side-effect dry-run plan on a non-TTY without `--apply`); anything else
(an `org/name` Hub id this CLI cannot resolve to bytes on the robot) instead
prints `sudo robotctl policy load <slot> <source>` and exits 0 without opening
a socket. A gated, absolute-path load shows the community-policy safety
sentence first (there is no way to tell a verified path from an unverified
one from the path alone).

{_D1_NOTE}

The text output always states that a load survives a reboot and names
`policy reset`, quoting `docs/robot/cheatsheet.md`, "The slots, and four
things worth knowing": loading writes the choice into `/etc/robot/robotd.toml`,
so it *survives a reboot* and survives updates, and `policy reset <slot>`
undoes it.

## Usage

    microduck-cli policy load walk RemiFabre/microduck-flamingo-cycle
    microduck-cli policy load walk /var/lib/robot/policies/flamingo.onnx --apply

## See also

- {_CHEATSHEET_URL}
"""

_POLICY_RESET = f"""\
# microduck-cli policy reset [<slot>]

Puts one slot (`robot.loadPolicy {{slot, path: null}}`) or, with no slot
named, all seven (`robot.reloadPolicies`) back to the robot's own policy.
Gated the same way as `policy load`. {_D1_NOTE}

## Usage

    microduck-cli policy reset walk
    microduck-cli policy reset --apply

## See also

- {_CHEATSHEET_URL}
"""

_POLICY_ADD = f"""\
# microduck-cli policy add <name> <repo>

Adds a skill — a policy that sits alongside `walk`/`stand` and runs on
request — via `robot.setSkill {{name, path, duration, command, ...}}`, main's
v22 skill table (`[[policy.skill]]`), served by `robotd`'s own socket.
`robot.setSkill` opens `path` on the robot's own disk, it does not fetch one,
so `repo` is inspected first the same way `policy load` inspects `source`: an
**absolute path** is sent as `path` in a real, gated call; anything else
prints `sudo robotctl policy add <name> <repo>` and exits 0 without opening a
socket. `--hold <seconds>` sets `duration` (a held-pose skill's length);
`--command x,y,z` sets the command vector it is fed while it runs. A gated,
absolute-path add shows the community-policy safety sentence first.
{_D1_NOTE}

## Usage

    microduck-cli policy add polite-bow fffiloni/microduck-polite-bow-b1d864
    microduck-cli policy add flamingo /var/lib/robot/policies/flamingo.onnx \\
        --hold 5 --command 1,1,0 --apply

## See also

- {_CHEATSHEET_URL}
"""

_POLICY_REMOVE = f"""\
# microduck-cli policy remove <name>

Removes a previously-added skill via `robot.removeSkill {{name}}` — a skill
the robot's own release ships comes back once the override is gone. Gated the
same way as `policy load`. {_D1_NOTE}

## Usage

    microduck-cli policy remove polite-bow --apply

## See also

- {_CHEATSHEET_URL}
"""

_POLICY_SEARCH = f"""\
# microduck-cli policy search <query>

`policy search` needs the Hub search updaterd's `policy.*` fetch namespace
exposes — a socket (`updaterd`) this CLI does not open. Prints the exact
`robotctl policy search <query>` line to run on the robot and exits 0.

## Usage

    microduck-cli policy search microduck

## See also

- {_CHEATSHEET_URL}
- {_POLICY_MANIFEST_URL}
"""

_POLICY_CHECK = f"""\
# microduck-cli policy check

Same reasoning as `policy search`: prints `robotctl policy check` (read-only
on the robot: "changes nothing and says so plainly when the Hub cannot be
reached") and exits 0.

## Usage

    microduck-cli policy check

## See also

- {_CHEATSHEET_URL}
"""

_POLICY_UPDATE = f"""\
# microduck-cli policy update

Same reasoning as `policy search`: prints `sudo robotctl policy update`
(optionally `--version <v>` to pin one instead of the newest) and exits 0.
`update` never disturbs a slot you loaded yourself — it "is left alone,
because it points somewhere else entirely."

## Usage

    microduck-cli policy update
    microduck-cli policy update --version v1

## See also

- {_CHEATSHEET_URL}
"""

_POLICY_PAD = f"""\
# microduck-cli policy pad

Pad button -> skill bindings. `pad.bindings` / `pad.bind` are not in the
pinned `duck-ipc-proto` method table — `[pad]` is owned by `robotd.toml`
(configd), and padd's own socket only forwards `pad.input`/`pad.report`. Every
`pad` verb prints the exact `robotctl` line instead of opening a config file,
and exits 0.

## Usage

    microduck-cli policy pad bindings
    microduck-cli policy pad bind x polite-bow
    microduck-cli policy pad reset

## See also

- {_CHEATSHEET_URL}
"""

_POLICY_PAD_BINDINGS = f"""\
# microduck-cli policy pad bindings

Prints `robotctl pad bindings` — the read-only listing of the five bindable
buttons (`a`, `x`, `lb`, `rb`, `dpad_down`) and what each runs.

## Usage

    microduck-cli policy pad bindings

## See also

- {_CHEATSHEET_URL}
"""

_POLICY_PAD_BIND = f"""\
# microduck-cli policy pad bind <button> <skill>

Prints `sudo robotctl pad bind <button> <skill>` — writes one `[pad]` line on
the robot; "nothing restarts, padd notices within a second."

## Usage

    microduck-cli policy pad bind x polite-bow

## See also

- {_CHEATSHEET_URL}
"""

_POLICY_PAD_RESET = f"""\
# microduck-cli policy pad reset [<button>]

Prints `sudo robotctl pad reset [<button>]` — one button back to its default,
or (with none named) the whole mapping.

## Usage

    microduck-cli policy pad reset
    microduck-cli policy pad reset x

## See also

- {_CHEATSHEET_URL}
"""

_TRAIN_LANE_NOTE = (
    "Builds `uv run ...` argv for the upstream microduck_rl tooling (never imports "
    "mjlab_microduck/mjlab/torch/warp) and runs it through an injected runner; "
    "secrets (HF_TOKEN, a wandb key) reach the child only via its environment, "
    "never on argv."
)

_POLICY_SMOKE = f"""\
# microduck-cli policy smoke <task>

The mandatory smoke test AGENTS.md requires before any longer run: 64 envs,
5 iterations. Recording a pass here is what unblocks `policy train` for the
same task id under the same state dir. {_TRAIN_LANE_NOTE}

## Usage

    microduck-cli policy smoke Mjlab-Velocity-Flat-MicroDuck

## See also

- {_RL_AGENTS_URL}
- {_RL_README_URL}
"""

_POLICY_TRAIN = f"""\
# microduck-cli policy train <task>

Trains `task`. Refuses (exit 1, naming the smoke command) without a recorded
`policy smoke <task>` pass under the same `--state` dir, unless `--force` is
given with `--reason`. `--hf-jobs` (with `--flavor`/`--namespace`/`--timeout`/
`--detach`) submits to Hugging Face Jobs instead of running locally, matching
`scripts/hf/README.md`'s flag spellings exactly. {_TRAIN_LANE_NOTE}

## Usage

    microduck-cli policy smoke Mjlab-Velocity-Flat-MicroDuck
    microduck-cli policy train Mjlab-Velocity-Flat-MicroDuck --num-envs 4096
    microduck-cli policy train Mjlab-Kick-Flat-MicroDuck --hf-jobs \\
        --flavor a100-large --namespace pollen-robotics --timeout 12h --detach

## See also

- {_RL_AGENTS_URL}
- {_RL_HF_README_URL}
"""

_POLICY_PLAY = f"""\
# microduck-cli policy play <task>

Plays back a trained task (`--wandb-run-path` or `--checkpoint`, mutually
exclusive). {_TRAIN_LANE_NOTE}

## Usage

    microduck-cli policy play Mjlab-Velocity-Flat-MicroDuck \\
        --wandb-run-path entity/project/run_id

## See also

- {_RL_README_URL}
"""

_POLICY_EXPORT = f"""\
# microduck-cli policy export <task>

Exports a trained task to an ONNX artifact via `scripts/export.py`
(`--wandb-run-path` or `--checkpoint`, mutually exclusive). A successful run
is appended to the train-lane artifact ledger. {_TRAIN_LANE_NOTE}

## Usage

    microduck-cli policy export Mjlab-Velocity-Flat-MicroDuck --checkpoint model_29999.pt

## See also

- {_RL_README_URL}
"""

_POLICY_PUBLISH = f"""\
# microduck-cli policy publish

Publishes an ONNX policy to the Hub via `uv run publish`. `--kind` is
`episodic` or `perpetual`; `--duration-s`/`--slot`/`--unwind-s` match the
README's publish flags. `--dry-run` says what it would do and changes
nothing on the RL side. {_TRAIN_LANE_NOTE}

## Usage

    microduck-cli policy publish --onnx walk.onnx --repo you/microduck-walk --kind perpetual

## See also

- {_RL_README_URL}
"""

_POLICY_INFER = f"""\
# microduck-cli policy infer

Runs `scripts/infer_policy.py --walking <onnx>` locally. {_TRAIN_LANE_NOTE}

## Usage

    microduck-cli policy infer --walking walk.onnx

## See also

- {_RL_README_URL}
"""

_POLICY_INSTALL = f"""\
# microduck-cli policy install <kind> <name> <repo>

Prints the `sudo robotctl policy add|load <name> <repo> [--hold <s>]` line —
installing onto the robot is the robot's own `robotctl`'s job, not this CLI's
`robot.loadPolicy` call (which addresses one already-running daemon, not a
release). `kind` is `add` (episodic, or a held perpetual pose) or `load` (a
perpetual gait installed into a named slot).

## Usage

    microduck-cli policy install add polite-bow fffiloni/microduck-polite-bow-b1d864
    microduck-cli policy install load walk RemiFabre/microduck-flamingo-cycle

## See also

- {_CHEATSHEET_URL}
- {_RL_README_URL}
"""

ENTRIES: dict[tuple[str, ...], str] = {
    ("policy",): _POLICY,
    ("policy", "overview"): _POLICY_OVERVIEW,
    ("policy", "list"): _POLICY_LIST,
    ("policy", "load"): _POLICY_LOAD,
    ("policy", "reset"): _POLICY_RESET,
    ("policy", "add"): _POLICY_ADD,
    ("policy", "remove"): _POLICY_REMOVE,
    ("policy", "search"): _POLICY_SEARCH,
    ("policy", "check"): _POLICY_CHECK,
    ("policy", "update"): _POLICY_UPDATE,
    ("policy", "pad"): _POLICY_PAD,
    ("policy", "pad", "bindings"): _POLICY_PAD_BINDINGS,
    ("policy", "pad", "bind"): _POLICY_PAD_BIND,
    ("policy", "pad", "reset"): _POLICY_PAD_RESET,
    ("policy", "smoke"): _POLICY_SMOKE,
    ("policy", "train"): _POLICY_TRAIN,
    ("policy", "play"): _POLICY_PLAY,
    ("policy", "export"): _POLICY_EXPORT,
    ("policy", "publish"): _POLICY_PUBLISH,
    ("policy", "infer"): _POLICY_INFER,
    ("policy", "install"): _POLICY_INSTALL,
}
