"""The composition root — one client, one tick seam, one engine, wired here.

Every other module under :mod:`microduck_cli.behavior` is a leaf that imports no
transport and starts no loop. This module is the ONE place they are assembled
into a running duck, and :mod:`microduck_cli.cli._commands.rules` is thin
argparse wiring on top of it (the reachy-mini-cli split: engine logic in a
sibling package, ``_commands/`` stays wiring).

What :func:`build_runtime` composes
----------------------------------

* a :class:`~microduck_cli.ipc.client.RobotClient` (the ONLY socket) and the
  :class:`~microduck_cli.behavior.sense.SenseProviders` it exposes;
* the ONE :class:`~microduck_cli.behavior.intents.KindRegistry`
  (:func:`~microduck_cli.behavior.intents.default_registry` plus
  :func:`microduck_cli.behavior.idle.register`, unless idle is turned off) —
  a rule firing and an agent injecting reach the same validator;
* a :class:`~microduck_cli.behavior.rule_engine.RuleEngine` over the merged
  rules config;
* :class:`~microduck_cli.behavior.human_gate.HumanGate` wrapping
  :class:`~microduck_cli.behavior.sink.RobotSink` in a
  :class:`~microduck_cli.behavior.human_gate.GatedSink`, so a person at the pad
  wins every motion channel;
* a :class:`~microduck_cli.behavior.liveness.Heartbeat` the engine beats;
* the :class:`~microduck_cli.behavior.engine.Engine` and its ONE
  :class:`~microduck_cli.behavior.engine.TickBus`, whose drivers run in this
  order every tick: **rules -> human-gate observation -> health poll -> intent
  spool**.

ONE tick seam, never a second process. Every rider above composes onto
``tick_seam``; none of them opens a socket of its own. That is the
single-SDK-owner rule this family learned from reachy-mini-cli, and
:func:`~microduck_cli.behavior.liveness.refuse_if_engine_live` is what stops a
second *process* from arriving anyway.

The intent spool
----------------
``<state>/intents.jsonl`` is how an agent injects while the engine runs: one
JSON object per line (``{"kind": ..., "payload": {...}, "id": ...}``), appended
by anybody, drained by :class:`IntentSpool` on the tick. Each line goes through
``registry.inject(origin="agent")`` — the same gate a rule uses — and the engine
acknowledges by appending one record to ``<state>/intents.log`` carrying the
admission, or the refusal text VERBATIM. A file is used rather than a socket
because the engine already owns the only socket this process may open, and a
second listening thread would be a second author.

The start sequence
------------------
:func:`build_runtime` performs ``connect`` and ``hello``; :func:`arm` performs
``health`` -> ``init`` -> ``enable`` -> ``armed``. Each of the six is logged as
one ``[SENSE stage=start ...]`` line, in order, so an operator reading stderr
sees exactly how far bring-up got. Gating ``init``/``enable`` behind
:func:`microduck_cli.duck.gate.consent` is the CLI's job — this module is
reached only once consent has been given, and :func:`start_plan` renders the
zero-side-effect preview for the dry-run path that never gets here.

Deliberate deviation from the task brief, recorded rather than silent:
``connect`` passes ``verify_joints=False`` and the ``robot.subscribe`` that
starts the 50 Hz state stream happens at ``armed`` instead. Subscribing inside
``connect`` (``verify_joints=True``) would start streaming state from a duck
that has not been initialised or enabled yet, and would put ``robot.subscribe``
*before* ``robot.init`` on the wire — the opposite of the order the start
sequence exists to guarantee. The joint table is still checked: :func:`arm`
waits for the first ``robot.state`` frame after subscribing and refuses (exit 2)
when the client counted a ``joint-table-mismatch``.
"""

from __future__ import annotations

import json
import os
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable

from microduck_cli.behavior import idle as idle_mod
from microduck_cli.behavior import senselog
from microduck_cli.behavior.engine import DEFAULT_HZ, Engine, TickBus, TickContext
from microduck_cli.behavior.human_gate import GatedSink, HumanGate
from microduck_cli.behavior.intents import ORIGIN_AGENT, KindRegistry, default_registry
from microduck_cli.behavior.liveness import Heartbeat, state_path
from microduck_cli.behavior.rule_engine import RuleEngine
from microduck_cli.behavior.rules import RulesConfig
from microduck_cli.behavior.sense import read_sense
from microduck_cli.behavior.sink import RobotSink
from microduck_cli.cli._errors import EXIT_ENV_ERROR, CliError
from microduck_cli.ipc import proto
from microduck_cli.ipc.client import DROP_JOINT_MISMATCH

__all__ = [
    "INTENT_LOG_NAME",
    "INTENT_SPOOL_NAME",
    "START_STEPS",
    "STAGE",
    "IntentSpool",
    "Runtime",
    "arm",
    "build_runtime",
    "start_plan",
]

#: ``senselog`` stage token for the start sequence.
STAGE = "start"

#: ``senselog`` stage token for the rules driver and the intent spool.
STAGE_RULE = "rule"
STAGE_INTENT = "intent"

#: The six start steps, in the order they are logged. A test asserts this
#: sequence appears on stderr in exactly this order.
START_STEPS: tuple[str, ...] = ("connect", "hello", "health", "init", "enable", "armed")

#: The spool an agent appends intents to, and the log the engine acknowledges in.
INTENT_SPOOL_NAME = "intents.jsonl"
INTENT_LOG_NAME = "intents.log"

#: How often the health/session pollers run, in Hz. Health is a REQUEST, not a
#: stream: polling it every tick would be fifty round trips a second for a
#: number that changes far slower than that.
HEALTH_POLL_HZ = 2.0
SESSION_POLL_HZ = 1.0

#: How long a background poll may take before it is abandoned.
POLL_TIMEOUT_S = 1.0

#: How long :func:`arm` waits for the first ``robot.state`` frame before giving
#: up on verifying the joint table. Silence is not a mismatch — a daemon that
#: never streams tells us nothing about its table — so a timeout is reported,
#: not fatal.
JOINT_WAIT_S = 1.0
_JOINT_POLL_S = 0.02

#: How long :meth:`Runtime.close` lets the client's writer thread flush what is
#: already queued before the link is torn down. See :meth:`Runtime.close`.
DRAIN_TIMEOUT_S = 1.0
_DRAIN_POLL_S = 0.005


def _stage(step: str, source: str, detail: str = "") -> None:
    senselog.stage(STAGE, source, step, detail)


# --------------------------------------------------------------------------- #
# The intent spool                                                            #
# --------------------------------------------------------------------------- #


class IntentSpool:
    """A file-backed intent inbox drained on the tick, acknowledged in a log.

    ``spool_path`` is append-only JSONL written by anybody (``rules intent``, an
    agent's tool call); ``log_path`` is append-only JSONL written only here. The
    spool is drained by OFFSET rather than by truncation, so a writer appending
    while the engine reads can never lose a line, and the engine never has to
    take a lock on a file another process owns.

    Every drained line goes through ``registry.inject(origin="agent")`` — the
    ONE admission path — and the record appended to the log carries the
    registry's own ``reason`` verbatim, which is what makes a refusal an agent
    reads byte-identical to the one a rule would have received.
    """

    def __init__(
        self,
        spool_path: str | os.PathLike[str],
        log_path: str | os.PathLike[str],
        registry: KindRegistry,
    ) -> None:
        self.spool_path = Path(spool_path)
        self.log_path = Path(log_path)
        self._registry = registry
        self._offset = 0
        self.drained = 0
        self.admitted = 0
        self.refused = 0

    # -- writing (the agent side) ------------------------------------------

    def submit(
        self, kind: str, payload: dict | None = None, *, intent_id: str | None = None
    ) -> str:
        """Append one intent to the spool; returns the id to wait for."""
        identifier = intent_id or uuid.uuid4().hex
        line = json.dumps(
            {"id": identifier, "kind": kind, "payload": dict(payload or {})},
            sort_keys=True,
        )
        self.spool_path.parent.mkdir(parents=True, exist_ok=True)
        with self.spool_path.open("a", encoding="utf-8") as handle:
            handle.write(line + "\n")
        return identifier

    # -- reading (the engine side) -----------------------------------------

    def _new_lines(self) -> list[str]:
        """Everything appended since the last drain. Never raises."""
        try:
            size = self.spool_path.stat().st_size
        except OSError:
            return []
        if size < self._offset:  # the file was replaced: start over
            self._offset = 0
        if size == self._offset:
            return []
        try:
            with self.spool_path.open("r", encoding="utf-8") as handle:
                handle.seek(self._offset)
                text = handle.read()
                self._offset = handle.tell()
        except OSError as exc:
            senselog.drop(STAGE_INTENT, str(self.spool_path), "spool-unreadable", str(exc))
            return []
        return [line for line in text.splitlines() if line.strip()]

    def drain(self, ctx: TickContext) -> list[dict[str, object]]:
        """Submit every newly-appended intent through the ONE registry."""
        records: list[dict[str, object]] = []
        for line in self._new_lines():
            record = self._handle(line, ctx)
            if record is not None:
                records.append(record)
        for record in records:
            self._append_log(record)
        return records

    def _handle(self, line: str, ctx: TickContext) -> dict[str, object] | None:
        try:
            entry = json.loads(line)
        except ValueError as exc:
            senselog.drop(STAGE_INTENT, str(self.spool_path), "spool-malformed", str(exc))
            return None
        if not isinstance(entry, dict) or not isinstance(entry.get("kind"), str):
            senselog.drop(
                STAGE_INTENT, str(self.spool_path), "spool-malformed", f"not an intent: {line!r}"
            )
            return None
        payload = entry.get("payload")
        if payload is not None and not isinstance(payload, dict):
            payload = None
        self.drained += 1
        admission = self._registry.inject(
            entry["kind"],
            payload or {},
            now=ctx.now,
            active=ctx.active,
            origin=ORIGIN_AGENT,
        )
        record: dict[str, object] = {
            "id": str(entry.get("id") or ""),
            "kind": entry["kind"],
            "admitted": bool(admission.admitted),
            "reason": admission.reason,
            "at": ctx.now,
            "tick": ctx.tick,
        }
        if admission.admitted and admission.behavior is not None:
            self.admitted += 1
            for evicted in admission.evicted:
                ctx.evict(evicted.id)
            ctx.admit(admission.behavior)
            record["behavior"] = admission.behavior.id
            senselog.stage(STAGE_INTENT, entry["kind"], "admitted", record["behavior"])
        else:
            self.refused += 1
            senselog.drop(STAGE_INTENT, entry["kind"], "intent-refused", admission.reason)
        return record

    def _append_log(self, record: dict[str, object]) -> None:
        try:
            self.log_path.parent.mkdir(parents=True, exist_ok=True)
            with self.log_path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(record, sort_keys=True) + "\n")
        except OSError as exc:  # an unwritable log must never stop the tick
            senselog.drop(STAGE_INTENT, str(self.log_path), "intent-log-unwritable", str(exc))

    def records(self) -> list[dict]:
        """Every acknowledgement in the log, oldest first. Never raises."""
        try:
            text = self.log_path.read_text(encoding="utf-8")
        except OSError:
            return []
        out: list[dict] = []
        for line in text.splitlines():
            if not line.strip():
                continue
            try:
                entry = json.loads(line)
            except ValueError:
                continue
            if isinstance(entry, dict):
                out.append(entry)
        return out

    def driver(self) -> Callable[[TickContext], None]:
        """A :class:`~microduck_cli.behavior.engine.TickBus` driver that drains."""

        def _drive(ctx: TickContext) -> None:
            self.drain(ctx)

        _drive.name = "intent-spool"  # type: ignore[attr-defined]
        return _drive


# --------------------------------------------------------------------------- #
# Tick drivers                                                                #
# --------------------------------------------------------------------------- #


def rules_driver(engine: RuleEngine) -> Callable[[TickContext], None]:
    """The rules rider: evaluate once per tick, apply what fired, name every drop.

    ``RuleEngine.evaluate`` is handed ``ctx.active`` — the live
    :class:`~microduck_cli.behavior.model.Behavior` objects, which is what
    arbitration and the admission model need. (The brief said
    ``ctx.active_names()``; names alone cannot be arbitrated against, so the
    objects are passed and the names remain available on the context.)
    """

    def _drive(ctx: TickContext) -> None:
        result = engine.evaluate(ctx.sense, ctx.active)
        for drop in result.drops:
            senselog.drop(STAGE_RULE, drop.rule_id, drop.reason, drop.detail)
        for fire in result.fires:
            for evicted in fire.admission.evicted:
                ctx.evict(evicted.id)
            ctx.admit(fire.behavior)
            senselog.stage(STAGE_RULE, fire.rule_id, "fired", f"{fire.kind} -> {fire.behavior.id}")
        for action, rule_id in sorted(result.inhibited.items()):
            senselog.stage(STAGE_RULE, rule_id, "inhibits", action)

    _drive.name = "rules"  # type: ignore[attr-defined]
    return _drive


class HealthPoller:
    """Polls ``robot.health`` at 2 Hz and ``robot.remoteSessionActive`` at 1 Hz.

    Both are REQUESTS, so neither may run on the tick thread: a request waits for
    a correlated reply, and at 50 Hz one hesitant daemon answer would cost the
    whole tick. Each poll therefore runs on a short-lived worker, at most one at
    a time — a poll still in flight when the next is due is a NAMED drop
    (``health-poll-busy``), never a queue that grows.

    The schedule is driven by ``ctx.now`` (the engine's injected clock), so a
    test with a fake clock controls exactly how many polls happen.
    """

    def __init__(
        self,
        client,
        *,
        health_hz: float = HEALTH_POLL_HZ,
        session_hz: float = SESSION_POLL_HZ,
        timeout: float = POLL_TIMEOUT_S,
    ) -> None:
        self._client = client
        self._health_period = 1.0 / health_hz if health_hz > 0 else 0.0
        self._session_period = 1.0 / session_hz if session_hz > 0 else 0.0
        self._timeout = timeout
        self._next_health: float | None = None
        self._next_session: float | None = None
        self._worker: threading.Thread | None = None
        self.polls = 0
        self.busy_drops = 0

    def _busy(self) -> bool:
        return self._worker is not None and self._worker.is_alive()

    def _spawn(self, calls: list[Callable[[], Any]]) -> None:
        def _run() -> None:
            for call in calls:
                try:
                    call()
                except Exception as exc:  # a poll never kills the engine
                    senselog.drop("ipc", "poll", "poll-failed", str(exc))

        self._worker = threading.Thread(target=_run, name="microduck-health-poll", daemon=True)
        self._worker.start()

    def __call__(self, ctx: TickContext) -> None:
        now = ctx.now
        calls: list[Callable[[], Any]] = []
        if self._health_period and (self._next_health is None or now >= self._next_health):
            self._next_health = now + self._health_period
            calls.append(lambda: self._client.poll_health(self._timeout))
        if self._session_period and (self._next_session is None or now >= self._next_session):
            self._next_session = now + self._session_period
            calls.append(lambda: self._client.poll_remote_session(self._timeout))
        if not calls:
            return
        if self._busy():
            self.busy_drops += 1
            senselog.drop("ipc", "poll", "health-poll-busy", "a previous poll is still in flight")
            return
        self.polls += len(calls)
        self._spawn(calls)

    @property
    def name(self) -> str:
        return "health-poll"


# --------------------------------------------------------------------------- #
# The runtime                                                                 #
# --------------------------------------------------------------------------- #


@dataclass
class Runtime:
    """Everything one engine run owns. Built by :func:`build_runtime`."""

    address: Any
    client: Any
    registry: KindRegistry
    rules: RulesConfig
    rule_engine: RuleEngine
    gate: HumanGate
    robot_sink: RobotSink
    sink: GatedSink
    heartbeat: Heartbeat
    engine: Engine
    bus: TickBus
    spool: IntentSpool
    poller: HealthPoller
    state_dir: str
    hz: float
    sleep: Callable[[float], None] = time.sleep
    register_idle: bool = True
    api_version: int | None = None
    steps: list[dict[str, object]] = field(default_factory=list)

    @property
    def socket_path(self) -> str:
        return getattr(self.address, "socket_path", "")

    def record_step(self, step: str, detail: str = "", ok: bool = True) -> None:
        """Log one start step and remember it for the verb's own report."""
        self.steps.append({"step": step, "detail": detail, "ok": ok})
        _stage(step, self.socket_path or "duck", detail)

    def run(self, max_ticks: int | None = None) -> int:
        """Drive the duck until stopped or *max_ticks*. Returns the tick count."""
        return self.engine.run(tick_seam=self.bus, max_ticks=max_ticks)

    def close(self) -> None:
        """Flush what is queued, then stop the sink worker and the client.

        The flush is load-bearing, not politeness. Three of the four release
        sends are NOTIFICATIONS, which the client *queues* for its writer thread
        rather than sending inline — and ``RobotClient.close`` discards whatever
        is still queued when it tears the link down. Closing straight after a
        release would therefore turn "we asked" into "nothing left the socket",
        which is exactly the claim :mod:`microduck_cli.behavior.release` refuses
        to make on the caller's behalf. Bounded by :data:`DRAIN_TIMEOUT_S` of
        REAL time (this is the shutdown path, not the tick loop, so it does not
        use the engine's injected sleep — a test's no-op sleep would spin here).
        """
        self._drain_writes()
        for name, closer in (("sink", self.robot_sink.close), ("client", self.client.close)):
            try:
                closer()
            except Exception as exc:  # a failed close is NAMED, never swallowed
                senselog.drop(STAGE, name, "close-failed", f"{type(exc).__name__}: {exc}")

    def _drain_writes(self) -> None:
        flush = getattr(self.client, "flush", None)
        if flush is None:  # pragma: no cover - a client without a write queue
            return
        if not flush(DRAIN_TIMEOUT_S):
            senselog.drop(
                STAGE,
                "client",
                "flush-timeout",
                f"queued frames still pending after {DRAIN_TIMEOUT_S:.1f}s; "
                "a wedged or dead daemon did not take them",
            )


def build_runtime(
    address,
    rules_config: RulesConfig,
    *,
    client_factory: Callable[[str], Any],
    clock: Callable[[], float],
    state_dir: str | os.PathLike[str],
    hz: float = DEFAULT_HZ,
    sleep: Callable[[float], None] = time.sleep,
    register_idle: bool = True,
) -> Runtime:
    """Connect to *address* and compose the whole runtime around one client.

    Performs the ``connect`` and ``hello`` start steps (the remaining four are
    :func:`arm`'s). ``client_factory(socket_path)`` returns a CONNECTED client —
    injected so a test drives the in-process fake, and so this module never
    decides what a connection costs.
    """
    socket_path = getattr(address, "socket_path", str(address))
    _stage("connect", socket_path, "opening the robot control socket")
    client = client_factory(socket_path)
    api_version = getattr(getattr(client, "daemon", None), "api_version", None)
    _stage("hello", socket_path, f"daemon api_version={api_version}")

    registry = default_registry()
    gate = HumanGate()
    if register_idle:
        idle_mod.register(registry, gate)

    rule_engine = RuleEngine(rules_config, registry, clock=clock)
    robot_sink = RobotSink(client)
    providers = client.providers()
    sink = GatedSink(robot_sink, gate, sense=lambda: read_sense(providers, clock()))
    heartbeat = Heartbeat(state_path(state_dir), clock=clock)
    engine = Engine(
        sink=sink,
        providers=providers,
        clock=clock,
        sleep=sleep,
        hz=hz,
        heartbeat=heartbeat,
    )
    spool = IntentSpool(
        Path(state_dir) / INTENT_SPOOL_NAME,
        Path(state_dir) / INTENT_LOG_NAME,
        registry,
    )
    poller = HealthPoller(client)
    bus = TickBus(
        [
            rules_driver(rule_engine),
            gate.driver(),
            poller,
            spool.driver(),
        ]
    )

    runtime = Runtime(
        address=address,
        client=client,
        registry=registry,
        rules=rules_config,
        rule_engine=rule_engine,
        gate=gate,
        robot_sink=robot_sink,
        sink=sink,
        heartbeat=heartbeat,
        engine=engine,
        bus=bus,
        spool=spool,
        poller=poller,
        state_dir=str(state_dir),
        hz=hz,
        sleep=sleep,
        register_idle=register_idle,
        api_version=api_version,
    )
    runtime.steps.append({"step": "connect", "detail": socket_path, "ok": True})
    runtime.steps.append({"step": "hello", "detail": f"api_version={api_version}", "ok": True})
    return runtime


def _health_problem(result: Any) -> str | None:
    """The reason a ``robot.health`` answer says the duck is not healthy."""
    if not isinstance(result, dict):
        return None  # no verdict is not a refusal
    if result.get("healthy", True):
        return None
    reason = result.get("reason")
    return reason if isinstance(reason, str) and reason else "the daemon reports unhealthy"


def _wait_for_state_frame(runtime: Runtime, sleep: Callable[[float], None]) -> bool:
    """Wait briefly for the first ``robot.state`` frame; True when one landed."""
    waited = 0.0
    while waited < JOINT_WAIT_S:
        if runtime.client.peek(proto.ROBOT_STATE) is not None:
            return True
        sleep(_JOINT_POLL_S)
        waited += _JOINT_POLL_S
    return False


def arm(runtime: Runtime, *, sleep: Callable[[float], None] | None = None) -> Runtime:
    """Run ``health`` -> ``init`` -> ``enable`` -> ``armed`` against a live duck.

    Raises :class:`~microduck_cli.cli._errors.CliError` (exit 2) when the daemon
    reports unhealthy, when ``init``/``enable`` are refused, or when the joint
    table the daemon streams disagrees with ours. Consent for the two motion
    steps is the CALLER's business — reaching this function means it was given.
    """
    naps = sleep or runtime.sleep
    client = runtime.client

    health = client.request(proto.ROBOT_HEALTH, timeout=POLL_TIMEOUT_S)
    problem = _health_problem(health)
    if problem is not None:
        runtime.record_step("health", problem, ok=False)
        raise CliError(
            EXIT_ENV_ERROR,
            f"the duck is not healthy: {problem}",
            remediation="run 'microduck env doctor' and read the daemon's own log before driving",
        )
    runtime.record_step("health", "healthy")

    _require_accepted(runtime, "init", proto.ROBOT_INIT, None)
    _require_accepted(runtime, "enable", proto.ROBOT_ENABLE, {"on": True})

    client.subscribe(int(runtime.hz))
    if _wait_for_state_frame(runtime, naps) and client.drops.get(DROP_JOINT_MISMATCH):
        runtime.record_step("armed", "joint table mismatch", ok=False)
        raise CliError(
            EXIT_ENV_ERROR,
            "the daemon's joint table disagrees with this CLI's",
            remediation=(
                "re-pin duck-ipc-proto (docs/upstream-pins.md) or update the daemon "
                "before driving this duck"
            ),
        )
    rules_loaded = len(runtime.rules.react) + len(runtime.rules.inhibit)
    idle_note = "idle registered" if runtime.register_idle else "idle off (--no-idle)"
    runtime.record_step("armed", f"{rules_loaded} rule(s) loaded, {idle_note}")
    return runtime


def _require_accepted(runtime: Runtime, step: str, method: str, params: Any) -> None:
    """Send one gated discrete call and refuse the run when it is not accepted."""
    result = runtime.client.request(method, params, timeout=POLL_TIMEOUT_S)
    reason = ""
    if isinstance(result, dict):
        reason = result.get("reason") if isinstance(result.get("reason"), str) else ""
        if result.get("accepted") is False:
            runtime.record_step(step, reason or "refused", ok=False)
            raise CliError(
                EXIT_ENV_ERROR,
                f"{method} was refused: {reason or 'no reason given'}",
                remediation="check the duck is on its stand and the daemon is healthy",
            )
    runtime.record_step(step, reason or method)


# --------------------------------------------------------------------------- #
# The dry-run plan                                                            #
# --------------------------------------------------------------------------- #


def start_calls(hz: float) -> list[str]:
    """The six start steps as the wire calls they make, for a dry-run plan."""
    return [
        "connect: open the duck's control socket",
        f"{proto.HELLO} {{'api_version': {proto.API_VERSION}}}",
        f"{proto.ROBOT_HEALTH} (refuse the run when the duck is not healthy)",
        f"{proto.ROBOT_INIT} (GATED: powers the joints and ramps to the home pose)",
        f"{proto.ROBOT_ENABLE} {{'on': True}} (GATED: the duck starts driving)",
        f"armed: {proto.ROBOT_SUBSCRIBE} {{'hz': {int(hz)}}}, rules loaded, idle registered",
    ]


def start_plan(
    address,
    *,
    hz: float,
    apply_command: str,
    rules_path: str | None = None,
) -> dict[str, object]:
    """The plan dict :func:`microduck_cli.duck.gate.render_dry_run` renders."""
    plan: dict[str, object] = {
        "verb": "init",
        "target": getattr(address, "name", "") or "?",
        "socket": getattr(address, "socket_path", ""),
        "calls": start_calls(hz),
        "apply_command": apply_command,
    }
    if rules_path:
        plan["rules"] = rules_path
    return plan


def steps_in_order(lines: Iterable[str]) -> list[str]:
    """The start steps named by ``[SENSE stage=start ... event=<step>]`` lines.

    Exposed for tests and for a verb that wants to prove the sequence ran: the
    obligation this task carries is that ``connect, hello, health, init, enable,
    armed`` appear in exactly that order.
    """
    marker = f"[SENSE stage={STAGE} "
    found: list[str] = []
    for line in lines:
        if marker not in line:
            continue
        for token in line.split():
            if token.startswith("event="):
                found.append(token[len("event=") :].rstrip("]"))
    return found
