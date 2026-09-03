"""``microduck-cli policy`` — noun group for the policy lifecycle.

Mirrors :mod:`microduck_cli.cli._commands.cli` — the nested subparsers are
built with ``parser_class=type(p)`` so a parse error under this noun keeps the
structured ``error:``/``hint:`` contract instead of argparse's default exit 2,
and a bare ``microduck-cli policy`` prints this noun's overview.

Verb summaries live in :mod:`microduck_cli.explain.policy` (``VERBS``), which
the global ``overview``/``learn`` surfaces read too — so adding a verb here
means editing this file, ``explain/policy.py`` and ``tests/test_policy.py``,
and nothing else.

## Approved deviation d1 — API 16 has no policy channel

The pinned daemon (``docs/upstream-pins.md``, ``sim-remote-io`` @
``0cd676d6fbb6e90a762c84aa63abe7a02dbc9495``) answers ``API_VERSION = 16`` and
has no ``robot.policies`` / ``robot.loadPolicy`` / ``robot.reloadPolicies`` /
``robot.skills`` / ``robot.setSkill`` / ``robot.removeSkill`` methods at all
(``tests/fake_robotd.py`` transcribes this: those methods answer ``-32601``
below its ``POLICY_API_VERSION = 18``). This module is written against
**main**'s documented wire shapes instead — read from
``duck-ipc-proto/src/lib.rs`` at main commit ``2f7812086e20310db4f9c07d51fb4b46bdfc99bc``
(``gh api "repos/pollen-robotics/microduck/contents/duck-ipc-proto/src/lib.rs"
--jq .content | base64 -d``), not the pinned commit above —

* ``robot.policies`` — read slots + skills (API >= 18 only);
* ``robot.loadPolicy {slot: Option<String>, path: Option<String>}`` — write
  one slot. ``path`` is an *absolute* filesystem path the daemon opens
  directly (``LoadPolicyParams`` doc comment: "the daemon resolves nothing
  relative"); it is **not** a fetch — there is no ``source``/HF-repo field on
  this call at all, on main or on the pinned build;
* ``robot.reloadPolicies`` — put every slot back to its own default;
* ``robot.skills`` / ``robot.setSkill(SkillParams)`` /
  ``robot.removeSkill(SkillNameParams {name})`` (main's v22) — the skill
  table (``[[policy.skill]]``), added/removed by name. ``SkillParams`` carries
  ``name`` and optional ``path`` (same "absolute, not fetched" contract as
  ``robot.loadPolicy``), ``duration``, ``command``, ``unwind``, ``unwind_s``,
  ``chain``, ``action_scale``, ``gain_ratio``. Per main's ``destination()``
  table, ``Call::RobotSkills | Call::RobotSetSkill(_) | Call::RobotRemoveSkill(_)
  => (Robot, Slow)`` — **robotd's own socket serves these**, the same one
  ``robot.loadPolicy`` uses, so this CLI sends them as real requests rather
  than printing a ``robotctl`` line.

Against a daemon whose ``hello`` answers an ``api_version`` below 18 (or that
answers ``METHOD_NOT_FOUND`` for one of the calls above), every verb that
needs them raises :class:`~microduck_cli.cli._errors.CliError` (exit 2) with
:data:`D1_REMEDIATION`, naming the daemon's actual version, rather than being
silently absent. ``policy list`` is the one exception: on API 16 it falls back
to ``robot.subscribe``'s ``walk``/``stand``/``unavailable``/skill-file fields
(:meth:`~microduck_cli.ipc.client.RobotClient.skills_from_subscribe`), and
labels its answer with the source it actually used.

**Neither ``robot.loadPolicy`` nor ``robot.setSkill`` can fetch a Hub repo —**
their only file field is an absolute local path the daemon opens as-is.
``policy load <slot> <source>`` and ``policy add <name> <repo>`` therefore
inspect ``source``/``repo``: an absolute path is sent as ``path`` in a real,
gated request; anything else (an ``org/name`` Hub id — the CLI cannot resolve
that to bytes on the robot's disk) prints the ``sudo robotctl policy load|add
...`` line for a human to run on the robot instead, and exits 0 without
opening a socket — the same pattern ``policy search``/``check``/``update``
already use for updaterd's fetch namespace this CLI cannot reach either.

``policy search`` / ``check`` / ``update`` need updater's ``policy.*`` fetch
namespace, which lives on ``updaterd`` (a separate socket this CLI does not
open — see ``microduck_cli/ipc/proto.py``'s ``SOCKET_UPDATER`` vs
``SOCKET_ROBOT``). Those verbs, and ``policy pad ...`` (``pad.bindings`` /
``pad.bind`` are not in the pinned ``duck-ipc-proto`` method table either —
padd's socket forwards ``pad.input``/``pad.report`` only), print the exact
``robotctl`` command line a human runs on the robot instead of pretending to
reach a channel this CLI cannot open, and exit 0: printing a command is not a
failure.
"""

from __future__ import annotations

import argparse
import os
import subprocess  # nosec B404 - only used with fixed argv, never shell=True
import time
from typing import Any, Callable

from microduck_cli.cli._commands.overview import emit_overview
from microduck_cli.cli._errors import EXIT_ENV_ERROR, EXIT_USER_ERROR, CliError
from microduck_cli.cli._output import emit_result
from microduck_cli.duck import addressing
from microduck_cli.duck.gate import (
    SAFETY_COMMUNITY_POLICY,
    Consent,
    confirm_on_tty,
    consent,
    render_dry_run,
)
from microduck_cli.explain.policy import _CHEATSHEET_URL, VERBS
from microduck_cli.ipc.client import RobotClient, RpcError
from microduck_cli.ipc.proto import METHOD_NOT_FOUND
from microduck_cli.train import artifacts, lane

_SUBJECT = "microduck-cli policy"
_PURPOSE = "Train, export, publish and install MicroDuck policies."
_STATUS = "policy lifecycle over robotd/updaterd's documented wire shapes (see module docstring)"

# ---------------------------------------------------------------------------
# d1 — the policy channel needs API >= 18
# ---------------------------------------------------------------------------

#: From-which-version the documented ``robot.policies`` / ``robot.loadPolicy`` /
#: ``robot.reloadPolicies`` methods exist — matches ``tests/fake_robotd.py``'s
#: ``POLICY_API_VERSION``. Not in ``microduck_cli.ipc.proto``: the pinned
#: ``duck-ipc-proto`` has no constant for methods it does not define.
POLICY_API_VERSION = 18

D1_REMEDIATION = (
    "daemon API {api} has no policy channel — needs API >= 18 (microduck main); "
    "on this build the skills list comes from robot.subscribe — see "
) + _CHEATSHEET_URL

#: Quoted verbatim from ``docs/robot/cheatsheet.md``, "#### The slots, and four
#: things worth knowing", ``pollen-robotics/microduck`` **main** (this section
#: post-dates the pinned ``sim-remote-io`` fork point — same situation as
#: ``duck/gate.py``'s ``SAFETY_COMMUNITY_POLICY``, re-quote on re-pin). Carries
#: the two sentences ``policy load``'s text output is required to contain.
_DURABILITY_NOTE = (
    "The slots are `walk`, `stand`, `sitstand`, `ground_pick`, `kick_left`, "
    "`kick_right` and `roulade`. `load` writes the choice into "
    "`/etc/robot/robotd.toml`, so it survives a reboot and survives updates — a "
    "release replaces the binaries and the policies it ships, not the line that "
    "points elsewhere. `policy reset <slot>` puts a slot back to the robot's own "
    "policy; with no slot, it puts all seven back."
)

# ---------------------------------------------------------------------------
# upstream robotctl command lines (search/check/update/pad/install never open
# a socket this CLI cannot reach — see the module docstring)
# ---------------------------------------------------------------------------

_ROBOTCTL_SEARCH = "robotctl policy search {query}"
_ROBOTCTL_CHECK = "robotctl policy check"
_ROBOTCTL_UPDATE = "sudo robotctl policy update"
_ROBOTCTL_PAD_BINDINGS = "robotctl pad bindings"
_ROBOTCTL_PAD_BIND = "sudo robotctl pad bind {button} {skill}"
_ROBOTCTL_PAD_RESET = "sudo robotctl pad reset"
_ROBOTCTL_POLICY_LOAD = "sudo robotctl policy load {slot} {source}"
_ROBOTCTL_POLICY_ADD = "sudo robotctl policy add {name} {repo}"

_UPDATERD_NOTE = (
    "the policy.* fetch namespace this needs lives on updaterd, a socket this CLI "
    "does not open (see microduck_cli/ipc/proto.py SOCKET_UPDATER vs SOCKET_ROBOT); "
    "run the line above on the robot"
)
_PAD_NOTE = (
    "pad.bindings / pad.bind are not in the pinned duck-ipc-proto method table "
    "(configd owns [pad] in robotd.toml; padd's socket only forwards pad.input / "
    "pad.report); run the line above on the robot"
)
_FETCH_NOTE = (
    "not an absolute path — robot.loadPolicy/robot.setSkill open a path on the "
    "robot's own disk, they do not fetch one, and microduck-cli cannot fetch a Hub "
    "repo to bytes on the robot either (that fetch, when it exists, lives in "
    "updaterd's policy.* namespace, the same socket 'policy search'/'check'/"
    "'update' cannot reach); run the line above on the robot"
)

# ---------------------------------------------------------------------------
# Injectable client factory + subprocess runner (test seams)
# ---------------------------------------------------------------------------


def _default_client_factory(socket_path: str) -> RobotClient:
    client = RobotClient(socket_path, clock=time.monotonic)
    return client.connect(verify_joints=False)


#: Module-level seam: tests monkeypatch this (``monkeypatch.setattr(policy,
#: "_client_factory", fake)``) rather than opening a real unix socket.
_client_factory: Callable[[str], RobotClient] = _default_client_factory


def _default_runner(argv: list[str], *, cwd: str | None, env: dict[str, str]) -> Any:
    try:
        return subprocess.run(  # nosec B603 - fixed argv from lane.py builders, never shell=True
            argv, cwd=cwd, env=env, capture_output=True, text=True, check=False
        )
    except FileNotFoundError as err:
        # A missing cwd (the RL clone) or a missing `uv` both land here; name it.
        raise CliError(
            code=EXIT_ENV_ERROR,
            message=f"cannot run {argv[0]!r} in {cwd!r}: {err.strerror}: {err.filename}",
            remediation="check DUCK_SIM_RL points at a synced microduck_rl clone and `uv` is "
            "on PATH — see https://github.com/pollen-robotics/microduck_rl#quickstart",
        ) from err


#: Module-level seam: tests monkeypatch this rather than spawning `uv run ...`.
_runner: Callable[..., Any] = _default_runner


def _runner_ok(result: Any) -> bool:
    code = getattr(result, "returncode", None)
    if code is None and isinstance(result, dict):
        code = result.get("returncode")
    return code in (0, None)


def _run_result_payload(result: Any) -> dict[str, object]:
    return {
        "returncode": getattr(result, "returncode", None),
        "stdout": getattr(result, "stdout", None),
        "stderr": getattr(result, "stderr", None),
    }


# ---------------------------------------------------------------------------
# Addressing / connection helpers
# ---------------------------------------------------------------------------


def _env_with_state(args: argparse.Namespace) -> dict[str, str]:
    env = dict(os.environ)
    state = getattr(args, "state", None)
    if state:
        env["DUCK_SIM_STATE"] = state
    return env


def _resolve_duck(args: argparse.Namespace) -> addressing.DuckAddress:
    return addressing.resolve(
        name=getattr(args, "duck", None),
        socket=getattr(args, "socket", None),
        env=_env_with_state(args),
        listdir=os.listdir,
    )


def _state_dir(args: argparse.Namespace) -> str:
    state = getattr(args, "state", None)
    if state:
        return state
    return os.path.expanduser(addressing.DEFAULT_STATE_DIR)


def _build_client(args: argparse.Namespace) -> tuple[RobotClient, addressing.DuckAddress]:
    address = _resolve_duck(args)
    return _client_factory(address.socket_path), address


def _require_policy_channel(client: RobotClient) -> None:
    """Raise the d1 :class:`CliError` unless the daemon has advertised API >= 18."""
    api = client.daemon.api_version
    if api is not None and api < POLICY_API_VERSION:
        raise CliError(
            EXIT_ENV_ERROR,
            f"daemon API {api} has no policy channel",
            remediation=D1_REMEDIATION.format(api=api),
        )


def _d1_from_method_not_found(client: RobotClient, exc: RpcError) -> CliError:
    api = client.daemon.api_version
    label = api if api is not None else "unknown"
    return CliError(
        EXIT_ENV_ERROR,
        f"daemon API {label} has no policy channel ({exc.method or 'policy call'} refused)",
        remediation=D1_REMEDIATION.format(api=label),
    )


# ---------------------------------------------------------------------------
# overview
# ---------------------------------------------------------------------------


def policy_sections() -> list[dict[str, object]]:
    """Sections describing the ``policy`` noun (used by ``policy overview``)."""
    return [
        {"title": "Purpose", "items": [_PURPOSE]},
        {"title": "Verbs", "items": list(VERBS)},
        {"title": "Status", "items": [_STATUS]},
    ]


def cmd_policy_overview(args: argparse.Namespace) -> int:
    emit_overview(
        _SUBJECT,
        policy_sections(),
        json_mode=bool(getattr(args, "json", False)),
    )
    return 0


def _no_verb(args: argparse.Namespace) -> int:
    # `microduck-cli policy` with no sub-verb prints the noun's overview.
    return cmd_policy_overview(args)


# ---------------------------------------------------------------------------
# list
# ---------------------------------------------------------------------------


def cmd_policy_list(args: argparse.Namespace) -> int:
    json_mode = bool(getattr(args, "json", False))
    client, address = _build_client(args)
    try:
        api = client.daemon.api_version
        source = "robot.policies"
        slots: dict[str, object] = {}
        skills: list[object] = []
        use_fallback = api is not None and api < POLICY_API_VERSION
        if not use_fallback:
            try:
                result = client.request("robot.policies")
            except RpcError as exc:
                if exc.code == METHOD_NOT_FOUND:
                    use_fallback = True
                else:
                    raise
            else:
                if isinstance(result, dict):
                    slots = result.get("slots", {}) or {}
                    skills = result.get("skills", []) or []
        if use_fallback:
            source = "robot.subscribe"
            sub = client.subscribe()
            slots = {"walk": sub.walk, "stand": sub.stand, "unavailable": sub.unavailable}
            skills = [{"name": name, "file": file} for name, file in sub.files.items()]
    finally:
        client.close()

    payload = {"duck": address.name, "source": source, "slots": slots, "skills": skills}
    if json_mode:
        emit_result(payload, json_mode=True)
        return 0

    lines = [f"# policy list ({source})", f"duck: {address.name}", "", "## slots"]
    for name in ("walk", "stand", "unavailable"):
        lines.append(f"- {name}: {slots.get(name)}")
    lines.append("")
    lines.append("## skills")
    if skills:
        for skill in skills:
            if isinstance(skill, dict):
                lines.append(f"- {skill.get('name')}: {skill.get('file')}")
            else:
                lines.append(f"- {skill}")
    else:
        lines.append("- (none)")
    emit_result("\n".join(lines), json_mode=False)
    return 0


# ---------------------------------------------------------------------------
# load / reset — gated writes through robot.loadPolicy / robot.reloadPolicies
# add / remove — gated writes through robot.setSkill / robot.removeSkill
# ---------------------------------------------------------------------------


def _is_pollen_source(source: str) -> bool:
    return source.startswith("pollen-robotics/")


def _is_local_path(source: str) -> bool:
    """True when *source* is a filesystem path ``robot.loadPolicy``/``robot.setSkill``
    can open directly, rather than a Hub id (``org/name``) this CLI would need to
    fetch first and cannot. Both calls document their file field as an absolute
    path the daemon opens as-is — see the module docstring.
    """
    return os.path.isabs(source)


def _load_policy_result_text(verb: str, detail: str, accepted: bool) -> str:
    status = "accepted" if accepted else "refused"
    return f"policy {verb}: {detail} {status}.\n\n{_DURABILITY_NOTE}"


def _positional_for(args: argparse.Namespace, verb: str) -> list[str]:
    if verb == "load":
        return [args.slot, args.source]
    if verb == "reset":
        return [args.slot] if getattr(args, "slot", None) else []
    if verb == "add":
        return [args.name, args.repo]
    if verb == "remove":
        return [args.name]
    return []


def _do_gated_policy_call(
    args: argparse.Namespace,
    *,
    verb: str,
    method: str,
    call_params: dict[str, object],
    detail: str,
    trusted_source: bool,
) -> int:
    json_mode = bool(getattr(args, "json", False))
    apply_flag = bool(getattr(args, "apply", False))
    client, address = _build_client(args)
    try:
        _require_policy_channel(client)
        call_desc = f"{method} {call_params!r}"
        gate_state = consent(apply_flag)

        if gate_state is Consent.DRY_RUN:
            plan = {
                "verb": "policy-install" if not trusted_source else verb,
                "target": address.name,
                "socket": address.socket_path,
                "calls": [call_desc],
                "apply_command": (
                    f"microduck policy {verb} {' '.join(_positional_for(args, verb))} --apply"
                ),
            }
            body = render_dry_run(plan) + "\n\n" + _DURABILITY_NOTE
            if json_mode:
                emit_result(
                    {"mode": "dry_run", "call": call_desc, "params": call_params, "text": body},
                    json_mode=True,
                )
            else:
                emit_result(body, json_mode=False)
            return 0

        if gate_state is Consent.PROMPT:
            question = f"Send {call_desc}. Proceed? [y/N]"
            if not trusted_source:
                question = f"{SAFETY_COMMUNITY_POLICY}\n{question}"
            if not confirm_on_tty(question):
                raise CliError(
                    EXIT_USER_ERROR,
                    f"policy {verb} cancelled",
                    remediation="re-run and confirm, or pass --apply for non-interactive mode",
                )

        try:
            result = client.request(method, call_params)
        except RpcError as exc:
            if exc.code == METHOD_NOT_FOUND:
                raise _d1_from_method_not_found(client, exc) from exc
            raise
    finally:
        client.close()

    accepted = bool(result.get("accepted", True)) if isinstance(result, dict) else True
    body = _load_policy_result_text(verb, detail, accepted)
    if json_mode:
        emit_result(
            {
                "mode": "apply",
                "call": method,
                "params": call_params,
                "result": result,
                "text": body,
            },
            json_mode=True,
        )
    else:
        emit_result(body, json_mode=False)
    return 0


def cmd_policy_load(args: argparse.Namespace) -> int:
    json_mode = bool(getattr(args, "json", False))
    if not _is_local_path(args.source):
        line = _ROBOTCTL_POLICY_LOAD.format(slot=args.slot, source=args.source)
        return _emit_robotctl_line(line, _FETCH_NOTE, json_mode=json_mode)
    return _do_gated_policy_call(
        args,
        verb="load",
        method="robot.loadPolicy",
        call_params={"slot": args.slot, "path": args.source},
        detail=f"slot {args.slot!r} <- {args.source!r}",
        trusted_source=_is_pollen_source(args.source),
    )


def cmd_policy_reset(args: argparse.Namespace) -> int:
    slot = getattr(args, "slot", None)
    if slot:
        return _do_gated_policy_call(
            args,
            verb="reset",
            method="robot.loadPolicy",
            call_params={"slot": slot, "path": None},
            detail=f"slot {slot!r}",
            trusted_source=True,
        )
    # No slot named: reset every slot back to this robot's own policy.
    json_mode = bool(getattr(args, "json", False))
    apply_flag = bool(getattr(args, "apply", False))
    client, address = _build_client(args)
    try:
        _require_policy_channel(client)
        call_desc = "robot.reloadPolicies {}"
        gate_state = consent(apply_flag)
        if gate_state is Consent.DRY_RUN:
            plan = {
                "verb": "reset",
                "target": address.name,
                "socket": address.socket_path,
                "calls": [call_desc],
                "apply_command": "microduck policy reset --apply",
            }
            body = render_dry_run(plan) + "\n\n" + _DURABILITY_NOTE
            emit_result(
                {"mode": "dry_run", "call": call_desc, "text": body} if json_mode else body,
                json_mode=json_mode,
            )
            return 0
        if gate_state is Consent.PROMPT and not confirm_on_tty(f"Send {call_desc}. Proceed? [y/N]"):
            raise CliError(
                EXIT_USER_ERROR,
                "policy reset cancelled",
                remediation="re-run and confirm, or pass --apply for non-interactive mode",
            )
        try:
            result = client.request("robot.reloadPolicies", {})
        except RpcError as exc:
            if exc.code == METHOD_NOT_FOUND:
                raise _d1_from_method_not_found(client, exc) from exc
            raise
    finally:
        client.close()
    accepted = bool(result.get("accepted", True)) if isinstance(result, dict) else True
    body = _load_policy_result_text("reset", "all seven slots", accepted)
    emit_result(
        (
            {"mode": "apply", "call": "robot.reloadPolicies", "result": result, "text": body}
            if json_mode
            else body
        ),
        json_mode=json_mode,
    )
    return 0


def cmd_policy_add(args: argparse.Namespace) -> int:
    json_mode = bool(getattr(args, "json", False))
    if not _is_local_path(args.repo):
        line = _ROBOTCTL_POLICY_ADD.format(name=args.name, repo=args.repo)
        return _emit_robotctl_line(line, _FETCH_NOTE, json_mode=json_mode)
    # SkillParams (main's duck-ipc-proto, robot.setSkill) — name is required; every
    # other field is optional and means "keep the default / whatever an existing
    # entry with this name already has".
    params: dict[str, object] = {"name": args.name, "path": args.repo}
    if getattr(args, "hold", None) is not None:
        params["duration"] = float(args.hold)
    if getattr(args, "command_vector", None):
        params["command"] = [float(x) for x in args.command_vector.split(",")]
    return _do_gated_policy_call(
        args,
        verb="add",
        method="robot.setSkill",
        call_params=params,
        detail=f"skill {args.name!r} <- {args.repo!r}",
        trusted_source=_is_pollen_source(args.repo),
    )


def cmd_policy_remove(args: argparse.Namespace) -> int:
    return _do_gated_policy_call(
        args,
        verb="remove",
        method="robot.removeSkill",
        call_params={"name": args.name},
        detail=f"skill {args.name!r}",
        trusted_source=True,
    )


# ---------------------------------------------------------------------------
# search / check / update — updaterd's policy.* namespace this CLI can't reach
# ---------------------------------------------------------------------------


def _emit_robotctl_line(line: str, note: str, *, json_mode: bool) -> int:
    if json_mode:
        emit_result({"command": line, "note": note}, json_mode=True)
    else:
        emit_result(f"{line}\n\n{note}", json_mode=False)
    return 0


def cmd_policy_search(args: argparse.Namespace) -> int:
    line = _ROBOTCTL_SEARCH.format(query=args.query)
    return _emit_robotctl_line(line, _UPDATERD_NOTE, json_mode=bool(getattr(args, "json", False)))


def cmd_policy_check(args: argparse.Namespace) -> int:
    return _emit_robotctl_line(
        _ROBOTCTL_CHECK, _UPDATERD_NOTE, json_mode=bool(getattr(args, "json", False))
    )


def cmd_policy_update(args: argparse.Namespace) -> int:
    line = _ROBOTCTL_UPDATE
    if getattr(args, "version", None):
        line += f" --version {args.version}"
    return _emit_robotctl_line(line, _UPDATERD_NOTE, json_mode=bool(getattr(args, "json", False)))


# ---------------------------------------------------------------------------
# pad bindings / bind / reset — config-owned, never opened as a file here
# ---------------------------------------------------------------------------


def cmd_policy_pad_bindings(args: argparse.Namespace) -> int:
    return _emit_robotctl_line(
        _ROBOTCTL_PAD_BINDINGS, _PAD_NOTE, json_mode=bool(getattr(args, "json", False))
    )


def cmd_policy_pad_bind(args: argparse.Namespace) -> int:
    line = _ROBOTCTL_PAD_BIND.format(button=args.button, skill=args.skill)
    return _emit_robotctl_line(line, _PAD_NOTE, json_mode=bool(getattr(args, "json", False)))


def cmd_policy_pad_reset(args: argparse.Namespace) -> int:
    line = _ROBOTCTL_PAD_RESET
    if getattr(args, "button", None):
        line += f" {args.button}"
    return _emit_robotctl_line(line, _PAD_NOTE, json_mode=bool(getattr(args, "json", False)))


# ---------------------------------------------------------------------------
# train sub-verbs — argv through train/lane.py, executed via the injected runner
# ---------------------------------------------------------------------------


def cmd_policy_smoke(args: argparse.Namespace) -> int:
    json_mode = bool(getattr(args, "json", False))
    state_dir = _state_dir(args)
    argv, cwd = lane.smoke(args.task, rl_clone=args.rl_clone)
    result = lane.run(argv, os.environ, _runner, cwd=cwd)
    ok = _runner_ok(result)
    if ok:
        lane.record_smoke_pass(state_dir, args.task, commit=args.task)
    payload = {"argv": argv, "cwd": cwd, "ok": ok, **_run_result_payload(result)}
    if json_mode:
        emit_result(payload, json_mode=True)
    else:
        emit_result(
            f"smoke {args.task}: {'passed' if ok else 'failed'}\nargv: {' '.join(argv)}",
            json_mode=False,
        )
    return 0 if ok else 1


def cmd_policy_train(args: argparse.Namespace) -> int:
    json_mode = bool(getattr(args, "json", False))
    state_dir = _state_dir(args)
    argv, cwd = lane.train(
        args.task,
        num_envs=args.num_envs,
        max_iterations=args.max_iterations,
        hf_jobs=args.hf_jobs,
        flavor=args.flavor,
        namespace=args.namespace,
        timeout=args.timeout,
        detach=args.detach,
        resume_checkpoint=args.resume,
        rl_clone=args.rl_clone,
        state_dir=state_dir,
        force=args.force,
        reason=args.reason,
    )
    result = lane.run(argv, os.environ, _runner, cwd=cwd)
    ok = _runner_ok(result)
    if ok:
        artifacts.append_artifact(state_dir, args.task, run_path=cwd)
    payload = {"argv": argv, "cwd": cwd, "ok": ok, **_run_result_payload(result)}
    if json_mode:
        emit_result(payload, json_mode=True)
    else:
        emit_result(
            f"train {args.task}: {'ok' if ok else 'failed'}\nargv: {' '.join(argv)}",
            json_mode=False,
        )
    return 0 if ok else 1


def cmd_policy_play(args: argparse.Namespace) -> int:
    json_mode = bool(getattr(args, "json", False))
    argv, cwd = lane.play(
        args.task,
        wandb_run_path=args.wandb_run_path,
        checkpoint_path=args.checkpoint,
        rl_clone=args.rl_clone,
    )
    result = lane.run(argv, os.environ, _runner, cwd=cwd)
    ok = _runner_ok(result)
    payload = {"argv": argv, "cwd": cwd, "ok": ok, **_run_result_payload(result)}
    emit_result(
        payload if json_mode else f"play {args.task}: argv: {' '.join(argv)}", json_mode=json_mode
    )
    return 0 if ok else 1


def cmd_policy_export(args: argparse.Namespace) -> int:
    json_mode = bool(getattr(args, "json", False))
    state_dir = _state_dir(args)
    argv, cwd = lane.export(
        args.task,
        wandb_run_path=args.wandb_run_path,
        checkpoint_path=args.checkpoint,
        rl_clone=args.rl_clone,
    )
    result = lane.run(argv, os.environ, _runner, cwd=cwd)
    ok = _runner_ok(result)
    if ok:
        artifacts.append_artifact(state_dir, args.task, run_path=cwd, checkpoint=args.checkpoint)
    payload = {"argv": argv, "cwd": cwd, "ok": ok, **_run_result_payload(result)}
    emit_result(
        payload if json_mode else f"export {args.task}: argv: {' '.join(argv)}",
        json_mode=json_mode,
    )
    return 0 if ok else 1


def cmd_policy_publish(args: argparse.Namespace) -> int:
    json_mode = bool(getattr(args, "json", False))
    state_dir = _state_dir(args)
    argv, cwd = lane.publish(
        args.onnx,
        args.repo,
        args.kind,
        duration_s=args.duration_s,
        slot=args.slot,
        unwind_s=args.unwind_s,
        dry_run=args.dry_run,
        force=args.force,
        rl_clone=args.rl_clone,
    )
    result = lane.run(argv, os.environ, _runner, cwd=cwd)
    ok = _runner_ok(result)
    if ok and not args.dry_run:
        artifacts.append_artifact(state_dir, args.onnx, onnx_path=args.onnx, hf_repo=args.repo)
    payload = {"argv": argv, "cwd": cwd, "ok": ok, **_run_result_payload(result)}
    emit_result(
        payload if json_mode else f"publish {args.repo}: argv: {' '.join(argv)}",
        json_mode=json_mode,
    )
    return 0 if ok else 1


def cmd_policy_infer(args: argparse.Namespace) -> int:
    json_mode = bool(getattr(args, "json", False))
    argv, cwd = lane.infer(args.walking, rl_clone=args.rl_clone)
    result = lane.run(argv, os.environ, _runner, cwd=cwd)
    ok = _runner_ok(result)
    payload = {"argv": argv, "cwd": cwd, "ok": ok, **_run_result_payload(result)}
    emit_result(payload if json_mode else f"infer: argv: {' '.join(argv)}", json_mode=json_mode)
    return 0 if ok else 1


def cmd_policy_install(args: argparse.Namespace) -> int:
    line = lane.install_argv(args.kind, args.name, args.repo, hold=args.hold)
    note = (
        "install prints the robotctl line rather than sending it: the install target "
        "is the robot's own robotctl, not this CLI's robot.loadPolicy call"
    )
    return _emit_robotctl_line(line, note, json_mode=bool(getattr(args, "json", False)))


# ---------------------------------------------------------------------------
# argparse wiring
# ---------------------------------------------------------------------------


def _add_json(p: argparse.ArgumentParser) -> None:
    p.add_argument("--json", action="store_true", help="Emit structured JSON.")


def _add_conn(p: argparse.ArgumentParser) -> None:
    p.add_argument("--duck", help="Duck name (default: the single duck under --state).")
    p.add_argument("--socket", help="Explicit robot-control socket path (overrides --duck).")
    p.add_argument("--state", help="State dir (default: DUCK_SIM_STATE or ~/.cache/duck-sim).")
    _add_json(p)


def _add_apply(p: argparse.ArgumentParser) -> None:
    p.add_argument(
        "--apply",
        action="store_true",
        help="Send this call now (non-interactive/agent mode). Without it: prompt on a "
        "TTY, print a zero-side-effect dry-run plan otherwise.",
    )


def _add_train(p: argparse.ArgumentParser) -> None:
    p.add_argument("--state", help="State dir for the smoke-gate record and artifact ledger.")
    p.add_argument("--rl-clone", dest="rl_clone", help="Path to the microduck_rl clone.")
    _add_json(p)


def register(sub: argparse._SubParsersAction) -> None:
    p = sub.add_parser(
        "policy",
        help="Policy lifecycle (see 'microduck-cli policy overview').",
    )
    p.add_argument("--json", action="store_true", help="Emit structured JSON.")
    p.set_defaults(func=_no_verb, json=False)
    # `p` is a _CliArgumentParser (the top-level subparsers were built with that
    # parser_class); propagate it so `policy <verb>` parse errors route through the
    # structured error contract instead of argparse's default stderr/exit 2.
    noun_sub = p.add_subparsers(dest="policy_command", parser_class=type(p))

    ov = noun_sub.add_parser("overview", help="Describe the policy noun.")
    ov.add_argument(
        "target",
        nargs="?",
        help="Ignored — overview always describes this noun. Accepted so a stray "
        "path argument never hard-fails.",
    )
    _add_json(ov)
    ov.set_defaults(func=cmd_policy_overview)

    lst = noun_sub.add_parser("list", help="List policy slots and skills.")
    _add_conn(lst)
    lst.set_defaults(func=cmd_policy_list)

    ld = noun_sub.add_parser("load", help="Load SOURCE into SLOT (robot.loadPolicy).")
    ld.add_argument("slot")
    ld.add_argument("source")
    _add_conn(ld)
    _add_apply(ld)
    ld.set_defaults(func=cmd_policy_load)

    rs = noun_sub.add_parser("reset", help="Reset one slot, or all seven with no SLOT.")
    rs.add_argument("slot", nargs="?")
    _add_conn(rs)
    _add_apply(rs)
    rs.set_defaults(func=cmd_policy_reset)

    ad = noun_sub.add_parser("add", help="Add a skill (a policy alongside walk/stand).")
    ad.add_argument("name")
    ad.add_argument("repo")
    ad.add_argument("--hold", type=int, help="Seconds to hold a held-pose skill.")
    ad.add_argument(
        "--command",
        dest="command_vector",
        help="Comma-separated command vector, e.g. 1,1,0.",
    )
    _add_conn(ad)
    _add_apply(ad)
    ad.set_defaults(func=cmd_policy_add)

    rm = noun_sub.add_parser("remove", help="Remove a previously-added skill override.")
    rm.add_argument("name")
    _add_conn(rm)
    _add_apply(rm)
    rm.set_defaults(func=cmd_policy_remove)

    se = noun_sub.add_parser("search", help="Print the robotctl line to search the Hub.")
    se.add_argument("query")
    _add_json(se)
    se.set_defaults(func=cmd_policy_search)

    ch = noun_sub.add_parser("check", help="Print the robotctl line to check for updates.")
    _add_json(ch)
    ch.set_defaults(func=cmd_policy_check)

    up = noun_sub.add_parser("update", help="Print the robotctl line to update policies.")
    up.add_argument("--version", help="Pin a specific version instead of the newest.")
    _add_json(up)
    up.set_defaults(func=cmd_policy_update)

    pad_p = noun_sub.add_parser(
        "pad", help="Pad button -> skill bindings (see 'policy pad bindings')."
    )
    _add_json(pad_p)
    pad_p.set_defaults(func=cmd_policy_pad_bindings)
    pad_sub = pad_p.add_subparsers(dest="policy_pad_command", parser_class=type(pad_p))

    pb = pad_sub.add_parser("bindings", help="Print the robotctl line to list pad bindings.")
    _add_json(pb)
    pb.set_defaults(func=cmd_policy_pad_bindings)

    pbind = pad_sub.add_parser("bind", help="Print the robotctl line to bind BUTTON to SKILL.")
    pbind.add_argument("button")
    pbind.add_argument("skill")
    _add_json(pbind)
    pbind.set_defaults(func=cmd_policy_pad_bind)

    pr = pad_sub.add_parser("reset", help="Print the robotctl line to reset one or all bindings.")
    pr.add_argument("button", nargs="?")
    _add_json(pr)
    pr.set_defaults(func=cmd_policy_pad_reset)

    sm = noun_sub.add_parser("smoke", help="Run the mandatory smoke test (64 envs, 5 iterations).")
    sm.add_argument("task")
    _add_train(sm)
    sm.set_defaults(func=cmd_policy_smoke)

    tr = noun_sub.add_parser("train", help="Train TASK (refuses without a recorded smoke pass).")
    tr.add_argument("task")
    tr.add_argument("--num-envs", dest="num_envs", type=int, default=4096)
    tr.add_argument("--max-iterations", dest="max_iterations", type=int)
    tr.add_argument("--hf-jobs", dest="hf_jobs", action="store_true")
    tr.add_argument("--flavor")
    tr.add_argument("--namespace")
    tr.add_argument("--timeout")
    tr.add_argument("--detach", action="store_true")
    tr.add_argument("--resume", help="Checkpoint file to resume from.")
    tr.add_argument("--force", action="store_true", help="Bypass the smoke gate.")
    tr.add_argument("--reason", help="Required with --force: why the gate is bypassed.")
    _add_train(tr)
    tr.set_defaults(func=cmd_policy_train)

    pl = noun_sub.add_parser("play", help="Play back a trained TASK.")
    pl.add_argument("task")
    pl_src = pl.add_mutually_exclusive_group()
    pl_src.add_argument("--wandb-run-path", dest="wandb_run_path")
    pl_src.add_argument("--checkpoint")
    _add_train(pl)
    pl.set_defaults(func=cmd_policy_play)

    ex = noun_sub.add_parser("export", help="Export TASK to an ONNX artifact.")
    ex.add_argument("task")
    ex_src = ex.add_mutually_exclusive_group()
    ex_src.add_argument("--wandb-run-path", dest="wandb_run_path")
    ex_src.add_argument("--checkpoint")
    _add_train(ex)
    ex.set_defaults(func=cmd_policy_export)

    pub = noun_sub.add_parser("publish", help="Publish an ONNX policy to the Hub.")
    pub.add_argument("--onnx", required=True)
    pub.add_argument("--repo", required=True)
    pub.add_argument("--kind", required=True, choices=["episodic", "perpetual"])
    pub.add_argument("--duration-s", dest="duration_s", type=float)
    pub.add_argument("--slot")
    pub.add_argument("--unwind-s", dest="unwind_s", type=float)
    pub.add_argument("--dry-run", dest="dry_run", action="store_true")
    pub.add_argument("--force", action="store_true")
    _add_train(pub)
    pub.set_defaults(func=cmd_policy_publish)

    inf = noun_sub.add_parser("infer", help="Run local ONNX inference for a walking policy.")
    inf.add_argument("--walking", required=True)
    _add_train(inf)
    inf.set_defaults(func=cmd_policy_infer)

    ins = noun_sub.add_parser("install", help="Print the robotctl line to add|load a policy.")
    ins.add_argument("kind", choices=["add", "load"])
    ins.add_argument("name")
    ins.add_argument("repo")
    ins.add_argument("--hold", type=int)
    _add_json(ins)
    ins.set_defaults(func=cmd_policy_install)
