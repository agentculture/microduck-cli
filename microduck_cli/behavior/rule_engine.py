"""The rule engine — react/inhibit evaluation against one :class:`Sense` snapshot.

:class:`RuleEngine` holds a validated
:class:`~microduck_cli.behavior.rules.RulesConfig`, a
:class:`~microduck_cli.behavior.intents.KindRegistry`, and an injected clock, and
answers ONE question per tick: given this snapshot and this live behaviour set,
what fires, and what does not fire and why. It performs no I/O, opens no socket,
starts no thread, sets up no logging, and imports nothing from
:mod:`microduck_cli.cli` beyond the shared error type it never raises out.

Data, not logging
-----------------
Every non-firing outcome a reader would want to know — a rule cooling down, a
rule re-arming, an action an inhibit rule disabled, an admission the model
refused — comes back as a :class:`Drop` in the :class:`TickResult`. Nothing is
silently skipped, and nothing is written anywhere: a later task wires these drops
to the ``[SENSE stage=rule ...]`` sense log. A layer whose drops are invisible is
indistinguishable from one that silently no-ops, so the drops are the layer's
primary output, not a debugging afterthought.

One admission path
------------------
A firing rule does NOT build a behaviour itself. It submits an
:class:`~microduck_cli.behavior.intents.Intent` with ``origin="rule"`` through
``registry.admit()`` — the same call the CLI's ``inject()`` makes — so a rule can
never acquire limits, defaults or refusal wording of its own. A refused admission
becomes a :data:`REASON_REFUSED` drop whose ``detail`` is the registry's refusal
text VERBATIM; that is what makes rule-fired and injected refusals byte-identical.

Timing semantics
----------------
* ``cooldown_s`` — the minimum seconds between two firings of the same rule, on
  the injected clock. The FIRST firing is always allowed (no rule has to wait a
  cooldown before it has ever fired).
* ``hysteresis`` — a VALUE margin, not a time one. After a rule with an ordered
  comparator (``gt``/``ge``/``lt``/``le``) fires, it is disarmed until the sense
  value crosses back past ``value ± hysteresis`` — below ``value - hysteresis``
  for ``gt``/``ge``, above ``value + hysteresis`` for ``lt``/``le`` — which is
  what stops a reading sitting exactly on a threshold from flapping. For every
  other comparator (and when ``hysteresis == 0``) the rule re-arms as soon as its
  predicate reads False, and cooldown alone governs.
* ``duration_s`` — a react rule's own bound on the admitted action. It is passed
  into the intent payload as ``duration_s``, so it is validated and capped by the
  ONE validator (:data:`~microduck_cli.behavior.intents.MAX_DURATION_S`) exactly
  like an injected duration: a runaway rule duration is refused, never clamped.

Predicate semantics
-------------------
A sense field reading ``None`` means *no reading* and can never satisfy an
ordered, equality or boolean predicate — an unwired or failed sensor must not be
able to fire a rule. The one exception is ``absent_for``, whose whole subject is
absence: it holds once the named field has read ``None`` continuously for at
least ``value`` seconds, measured from the first tick this engine saw.
"""

from __future__ import annotations

from dataclasses import dataclass
from dataclasses import field as dataclass_field
from typing import Callable, Sequence

from microduck_cli.behavior.intents import ORIGIN_RULE, Admission, Intent, KindRegistry
from microduck_cli.behavior.model import Behavior
from microduck_cli.behavior.rules import Rule, RulesConfig
from microduck_cli.behavior.sense import SENSE_FIELDS, Sense

# --------------------------------------------------------------------------- #
# Outcome vocabulary                                                          #
# --------------------------------------------------------------------------- #

#: The rule fired and its intent was admitted.
REASON_FIRED = "fired"
#: The rule matched but has fired within ``cooldown_s``.
REASON_COOLDOWN = "cooldown"
#: The rule matched but has not yet crossed back past its hysteresis margin.
REASON_REARMING = "rearming"
#: The rule matched but an inhibit rule currently disables its action.
REASON_INHIBITED = "inhibited"
#: The rule matched and was submitted, but the registry refused the admission.
REASON_REFUSED = "refused"

#: The comparators whose threshold hysteresis can be measured against.
ORDERED_OPS: frozenset[str] = frozenset({"lt", "le", "gt", "ge"})


@dataclass(frozen=True)
class Drop:
    """One thing that did NOT happen, and why.

    ``reason`` is the named token (:data:`REASON_COOLDOWN`,
    :data:`REASON_INHIBITED`, ...), ``rule_id`` the react/inhibit rule it belongs
    to, and ``detail`` the human-readable specifics — for a
    :data:`REASON_REFUSED` drop that is the registry's refusal text verbatim.
    """

    reason: str
    rule_id: str
    detail: str = ""


@dataclass(frozen=True)
class Fire:
    """One rule that fired and had its intent admitted."""

    rule_id: str
    kind: str
    behavior: Behavior
    admission: Admission


@dataclass(frozen=True)
class TickResult:
    """Everything one :meth:`RuleEngine.evaluate` decided.

    ``active`` is the live set AFTER applying this tick's admissions and
    evictions (oldest first, as :func:`~microduck_cli.behavior.model.arbitrate`
    expects) — the caller may adopt it wholesale or apply ``fires``/``evicted``
    itself. ``inhibited`` maps each disabled action to the inhibit rule
    disabling it.
    """

    now: float
    fires: tuple[Fire, ...] = ()
    drops: tuple[Drop, ...] = ()
    inhibited: dict[str, str] = dataclass_field(default_factory=dict)
    active: tuple[Behavior, ...] = ()
    evicted: tuple[Behavior, ...] = ()


@dataclass
class _RuleState:
    """Per-rule cooldown + hysteresis bookkeeping."""

    last_fire_t: float | None = None
    armed: bool = True


def _field_value(sense: Sense, name: str):
    """The snapshot's reading for *name*, or ``None`` when there is none."""
    return getattr(sense, name, None)


def _is_number(value: object) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _compare(op: str, left, right) -> bool:
    """A total, never-raising comparison — a type mismatch is simply ``False``."""
    try:
        if op == "gt":
            return bool(left > right)
        if op == "ge":
            return bool(left >= right)
        if op == "lt":
            return bool(left < right)
        if op == "le":
            return bool(left <= right)
        if op == "eq":
            return bool(left == right)
        if op == "ne":
            return bool(left != right)
    except TypeError:
        return False
    return False


class RuleEngine:
    """Evaluate a rules file against each tick's snapshot.

    Construct once with a validated config, a registry, and a zero-arg clock
    returning monotonic seconds; call :meth:`evaluate` once per tick. The engine
    owns only its per-rule timing state — the live behaviour set is passed in and
    handed back, so the engine is a pure function of (config, snapshot, active)
    plus that timing state, and a test can drive 250 ticks with a fake clock in
    microseconds.
    """

    def __init__(
        self,
        config: RulesConfig,
        registry: KindRegistry,
        clock: Callable[[], float],
    ) -> None:
        self._config = config
        self._registry = registry
        self._clock = clock
        self._state: dict[str, _RuleState] = {}
        self._last_present: dict[str, float] = {}
        self._started = False

    # -- public API -------------------------------------------------------- #

    @property
    def config(self) -> RulesConfig:
        return self._config

    def evaluate(self, sense: Sense, active: Sequence[Behavior] = ()) -> TickResult:
        """Run one tick over *sense* against the live set *active*."""
        now = self._clock()
        self._track_presence(sense, now)

        inhibited = self._inhibited_actions(sense, now)
        live: list[Behavior] = list(active)
        fires: list[Fire] = []
        drops: list[Drop] = []
        evicted: list[Behavior] = []

        for rule in self._config.react:
            outcome = self._step_react(rule, sense, now, inhibited, live)
            if isinstance(outcome, Drop):
                drops.append(outcome)
                continue
            if outcome is None:
                continue
            fires.append(outcome)
            evicted.extend(outcome.admission.evicted)
            gone = {b.id for b in outcome.admission.evicted}
            live = [b for b in live if b.id not in gone]
            live.append(outcome.behavior)

        return TickResult(
            now=now,
            fires=tuple(fires),
            drops=tuple(drops),
            inhibited=inhibited,
            active=tuple(live),
            evicted=tuple(evicted),
        )

    # -- absence tracking -------------------------------------------------- #

    def _track_presence(self, sense: Sense, now: float) -> None:
        """Update each field's last-seen stamp (what ``absent_for`` measures)."""
        if not self._started:
            # Seed every field at the first tick so ``absent_for`` measures
            # elapsed absence from engine start, not from -inf.
            for name in SENSE_FIELDS:
                self._last_present[name] = now
            self._started = True
        for name in SENSE_FIELDS:
            if _field_value(sense, name) is not None:
                self._last_present[name] = now

    def _absent_for(self, name: str, now: float) -> float:
        return now - self._last_present.get(name, now)

    # -- predicate evaluation ---------------------------------------------- #

    def _match(self, rule: Rule, sense: Sense, now: float) -> bool:
        """Whether *rule*'s predicate holds over *sense*. Never raises."""
        pred = rule.when
        if pred.op == "absent_for":
            return self._absent_for(pred.field, now) >= float(pred.value)  # type: ignore[arg-type]
        value = _field_value(sense, pred.field)
        if value is None:
            return False  # no reading never matches
        if pred.op == "is_true":
            return bool(value)
        if pred.op == "is_false":
            return not bool(value)
        return _compare(pred.op, value, pred.value)

    # -- cooldown / hysteresis --------------------------------------------- #

    def _rule_state(self, rule: Rule) -> _RuleState:
        return self._state.setdefault(rule.id, _RuleState())

    def _step_arming(self, rule: Rule, sense: Sense, matched: bool) -> None:
        """Re-arm *rule* once its reading has crossed back past the margin."""
        state = self._rule_state(rule)
        if state.armed:
            return
        pred = rule.when
        value = _field_value(sense, pred.field)
        if rule.hysteresis > 0 and pred.op in ORDERED_OPS and _is_number(pred.value):
            if value is None or not _is_number(value):
                state.armed = True  # no reading: nothing holds the rule disarmed
                return
            threshold = float(pred.value)  # type: ignore[arg-type]
            if pred.op in ("gt", "ge"):
                state.armed = float(value) < threshold - rule.hysteresis
            else:
                state.armed = float(value) > threshold + rule.hysteresis
            return
        state.armed = not matched

    def _cooling(self, rule: Rule, now: float) -> bool:
        state = self._rule_state(rule)
        if state.last_fire_t is None:
            return False  # the first firing is always allowed
        return (now - state.last_fire_t) < rule.cooldown_s

    def _mark_fired(self, rule: Rule, now: float) -> None:
        state = self._rule_state(rule)
        state.last_fire_t = now
        if rule.hysteresis > 0 and rule.when.op in ORDERED_OPS:
            state.armed = False

    # -- inhibit ----------------------------------------------------------- #

    def _inhibited_actions(self, sense: Sense, now: float) -> dict[str, str]:
        """``action -> inhibit rule id`` for every inhibit rule matching NOW.

        Independent of cooldown: while an inhibit predicate holds, its actions
        are disabled — a continuous effect, not an event. The first rule in file
        order naming an action owns the report for it.
        """
        blocked: dict[str, str] = {}
        for rule in self._config.inhibit:
            if not self._match(rule, sense, now):
                continue
            for action in sorted(rule.disable):
                blocked.setdefault(action, rule.id)
        return blocked

    # -- react ------------------------------------------------------------- #

    def _step_react(
        self,
        rule: Rule,
        sense: Sense,
        now: float,
        inhibited: dict[str, str],
        live: Sequence[Behavior],
    ) -> Fire | Drop | None:
        """One react rule's outcome: a :class:`Fire`, a :class:`Drop`, or nothing."""
        matched = self._match(rule, sense, now)
        self._step_arming(rule, sense, matched)
        if not matched:
            return None

        action = rule.action or ""
        if action in inhibited:
            return Drop(
                reason=REASON_INHIBITED,
                rule_id=rule.id,
                detail=f"action {action!r} is disabled by inhibit rule {inhibited[action]!r}",
            )
        if not self._rule_state(rule).armed:
            return Drop(
                reason=REASON_REARMING,
                rule_id=rule.id,
                detail=(
                    f"{rule.when.field} has not crossed back past "
                    f"{rule.when.value!r} ± {rule.hysteresis}"
                ),
            )
        if self._cooling(rule, now):
            state = self._rule_state(rule)
            waited = now - (state.last_fire_t or now)
            return Drop(
                reason=REASON_COOLDOWN,
                rule_id=rule.id,
                detail=f"fired {waited:.3f}s ago, cooldown_s is {rule.cooldown_s}",
            )

        admission = self._registry.admit(self._intent(rule, now), now, live)
        if not admission.admitted:
            return Drop(reason=REASON_REFUSED, rule_id=rule.id, detail=admission.reason)

        behavior = admission.behavior
        if behavior is None:  # pragma: no cover - defensive: admitted implies a behavior
            return Drop(reason=REASON_REFUSED, rule_id=rule.id, detail="admitted with no behavior")

        self._mark_fired(rule, now)
        return Fire(rule_id=rule.id, kind=action, behavior=behavior, admission=admission)

    def _intent(self, rule: Rule, now: float) -> Intent:
        """The intent *rule* submits: its params plus its own ``duration_s`` bound."""
        payload = dict(rule.params)
        if rule.duration_s is not None:
            payload.setdefault("duration_s", rule.duration_s)
        return Intent(
            kind=rule.action or "",
            payload=payload,
            origin=ORIGIN_RULE,
            rule_id=rule.id,
            submitted_at=now,
        )
