"""Own what you energize — let go of the duck on ANY abnormal exit, never on a clean one.

An engine run drives the duck by streaming intents. If the process dies mid-run —
an exception in a rule, a Ctrl-C, a ``systemctl stop`` — whatever it last sent is
what the duck keeps doing until robotd's own deadman notices. That is a walking
robot with nobody at the controls, so the run must let go on the way out.

**The four sends, each independent.** In order:

1. ``robot.stop`` (a REQUEST — the one send whose answer we wait for);
2. ``robot.pose {"active": false}`` (a notification) — stop asserting a posture;
3. ``robot.mouth {"open": 0.0}`` (a notification) — close the beak;
4. ``robot.sound {"tag": …, "hold": false}`` (a notification) — end a HELD sound,
   which is the one sound that outlives the sender (``wheee``).

Each is attempted on its own and a failure in one NEVER skips the next: a release
runs on a link that has usually just misbehaved, so "we asked" and "it happened"
are different claims and :class:`ReleaseReport` reports them separately. The
caller — the engine run verb — exits non-zero naming the failures rather than
implying a safety it did not achieve.

**``robot.relax`` is never sent.** Relaxing de-energises the servos, and a duck
standing on two legs with no torque FALLS. The safe exit for this robot is
"stop asking for motion", not "go limp" — which is the opposite of the arm's
release-torque discipline this module otherwise mirrors, because the hardware is
the opposite: an arm holding a pose is the hazard, a duck holding itself up is not.

**The limit, stated rather than implied.** These are messages over a socket. A
dead socket, a killed daemon, a ``SIGKILL`` of this process, a power cut: none of
them run this code, and no ``finally`` can. In those cases the only thing left is
robotd's own deadman, which expires the *velocity* command — the duck stops
walking, but a held pose, an open beak and a held sound are not covered. That is
the honest boundary of what this module can promise.
"""

from __future__ import annotations

import signal
import threading
from dataclasses import dataclass, field
from types import TracebackType
from typing import Any, Callable, Mapping

from microduck_cli.behavior import senselog
from microduck_cli.ipc import proto

#: ``senselog`` stage token for release traffic.
STAGE = "release"

#: The named drop for a release send that did not land.
DROP_RELEASE_FAILED = "release-failed"

#: The sound tag used when the caller does not name the one currently playing.
#: ``wheee`` is the only tag with ``hold`` semantics (the held joy ride), so it is
#: the tag a release most needs to cancel.
DEFAULT_SOUND_TAG = "wheee"

#: How long the ONE request in a release may take. Deliberately short: this runs
#: while an exception is unwinding and a slow daemon must not turn a crash into a
#: hang.
DEFAULT_TIMEOUT_S = 2.0


@dataclass(frozen=True)
class ReleaseStep:
    """One of the four sends: what was asked, and what actually happened.

    ``ok`` is ``True`` only when the send genuinely landed — a notification that
    reached the write queue, or a request that answered without an error and
    without ``accepted: false``. ``error`` carries the reason when it did not.
    """

    method: str
    params: Any
    ok: bool
    error: str = ""

    def as_dict(self) -> dict[str, object]:
        return {"method": self.method, "params": self.params, "ok": self.ok, "error": self.error}


@dataclass(frozen=True)
class ReleaseReport:
    """What a release sweep managed to do — honest, never assumed.

    ``complete`` is the only thing a caller may report as safety. A verb that
    tells an operator "the duck was released" without checking it is making a
    claim this module refused to make.
    """

    steps: tuple[ReleaseStep, ...] = ()

    @property
    def attempted(self) -> tuple[str, ...]:
        return tuple(step.method for step in self.steps)

    @property
    def sent(self) -> tuple[str, ...]:
        return tuple(step.method for step in self.steps if step.ok)

    @property
    def failed(self) -> tuple[str, ...]:
        return tuple(step.method for step in self.steps if not step.ok)

    @property
    def errors(self) -> dict[str, str]:
        return {step.method: step.error for step in self.steps if not step.ok}

    @property
    def complete(self) -> bool:
        """``True`` iff all four sends landed."""
        return bool(self.steps) and not self.failed

    def describe(self) -> str:
        """One line for an operator — safe to hand to ``emit_diagnostic``."""
        if not self.steps:
            return "Release sent nothing (no client)."
        if self.complete:
            return "Released the duck after an abnormal exit: " + ", ".join(self.sent) + "."
        failures = "; ".join(f"{method} ({reason})" for method, reason in self.errors.items())
        return (
            "Release INCOMPLETE: " + failures + ". The duck may still be moving, posturing, "
            "or sounding — robotd's deadman expires the velocity command only."
        )

    def as_dict(self) -> dict[str, object]:
        """JSON-serialisable form, for a verb's ``--json`` payload."""
        return {
            "steps": [step.as_dict() for step in self.steps],
            "sent": list(self.sent),
            "failed": list(self.failed),
            "errors": self.errors,
            "complete": self.complete,
        }


def _accepted(result: Any) -> tuple[bool, str]:
    """``(ok, reason)`` for a request reply. No ``accepted`` field means no verdict."""
    if not isinstance(result, Mapping):
        return (True, "")
    if result.get("accepted") is False:
        reason = result.get("reason")
        return (False, reason if isinstance(reason, str) and reason else "refused")
    return (True, "")


def _do_request(client, method: str, params: Any, timeout: float) -> ReleaseStep:
    try:
        result = client.request(method, params, timeout=timeout)
    except Exception as exc:  # every failure is recorded, none aborts the sweep
        return ReleaseStep(method, params, ok=False, error=str(exc) or type(exc).__name__)
    ok, reason = _accepted(result)
    return ReleaseStep(method, params, ok=ok, error=reason)


def _do_notify(client, method: str, params: Any, _timeout: float) -> ReleaseStep:
    try:
        queued = client.notify(method, params)
    except Exception as exc:  # pragma: no cover - notify is documented not to raise
        return ReleaseStep(method, params, ok=False, error=str(exc) or type(exc).__name__)
    if queued:
        return ReleaseStep(method, params, ok=True)
    return ReleaseStep(method, params, ok=False, error="not queued: the link is down or full")


def release_on_exit(
    client,
    *,
    sound_tag: str = DEFAULT_SOUND_TAG,
    timeout: float = DEFAULT_TIMEOUT_S,
) -> ReleaseReport:
    """Send the four release messages, each independently; report what landed.

    Never raises: a release runs while something else is already going wrong, and
    an exception from here would replace the real failure with a worse one. A
    ``None`` client yields an empty report rather than an error — a run that never
    connected has nothing to release.

    :param client: a :class:`~microduck_cli.ipc.client.RobotClient` (or anything
        with the same ``request``/``notify`` pair).
    :param sound_tag: the tag currently playing, when the caller knows it;
        defaults to :data:`DEFAULT_SOUND_TAG`, the only tag that can be held.
    """
    if client is None:
        return ReleaseReport()
    plan: tuple[tuple[Callable[..., ReleaseStep], str, Any], ...] = (
        (_do_request, proto.ROBOT_STOP, None),
        (_do_notify, proto.ROBOT_POSE, {"active": False}),
        (_do_notify, proto.ROBOT_MOUTH, {"open": 0.0}),
        (_do_notify, proto.ROBOT_SOUND, {"tag": sound_tag, "hold": False}),
    )
    steps: list[ReleaseStep] = []
    for send, method, params in plan:
        step = send(client, method, params, timeout)
        steps.append(step)
        if step.ok:
            senselog.stage(STAGE, method, "released", "sent on an abnormal exit")
        else:
            senselog.drop(STAGE, method, DROP_RELEASE_FAILED, step.error)
    return ReleaseReport(tuple(steps))


class SignalExit(BaseException):
    """Raised inside :func:`owning` when a SIGTERM arrives.

    A ``BaseException`` deliberately: it means "the process was told to stop", the
    same class of event as ``KeyboardInterrupt``, and an ``except Exception:``
    somewhere in a rule must not be able to swallow it and keep driving.
    """

    def __init__(self, signum: int) -> None:
        super().__init__(f"signal {signum}")
        self.signum = signum


#: The signals :func:`owning` installs handlers for. SIGINT already raises
#: ``KeyboardInterrupt``, but the handler is reinstalled anyway so the context is
#: the sole owner of both for its lifetime and restores both on the way out.
HANDLED_SIGNALS: tuple[int, ...] = (signal.SIGINT, signal.SIGTERM)


@dataclass
class _Owner:
    """The context manager :func:`owning` returns. See its docstring."""

    client: Any
    sound_tag: str = DEFAULT_SOUND_TAG
    timeout: float = DEFAULT_TIMEOUT_S
    on_release: Callable[[ReleaseReport], None] | None = None
    #: ``None`` until a release happens — so ``None`` IS the assertion that a
    #: clean exit sent nothing.
    report: ReleaseReport | None = None
    _previous: dict[int, Any] = field(default_factory=dict, repr=False)

    def __enter__(self) -> "_Owner":
        self._install_handlers()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        _exc: BaseException | None,
        _tb: TracebackType | None,
    ) -> None:
        """Release iff the block is unwinding; always let the exception through.

        The clean/abnormal split is exactly ``exc_type is None``, which is what
        makes ``KeyboardInterrupt``, ``SystemExit`` and :class:`SignalExit`
        abnormal for free — ``__exit__`` is handed any ``BaseException``, not just
        ``Exception`` — and equally makes a plain ``return`` clean. Returns
        ``None`` on every path: this releases the duck, it does not decide what
        the failure meant, and swallowing the exception would hide a crash behind
        a zero exit code.
        """
        try:
            if exc_type is None:
                return  # A DELIBERATE HOLD SURVIVES: a clean exit sends nothing.
            self.release()
        finally:
            self._restore_handlers()

    def release(self) -> ReleaseReport:
        """Release now, recording the outcome on :attr:`report`. Never raises."""
        self.report = release_on_exit(self.client, sound_tag=self.sound_tag, timeout=self.timeout)
        if self.on_release is not None:
            try:
                self.on_release(self.report)
            except SystemExit:
                raise
            except BaseException:  # noqa: B036 - a broken hook must not replace the real error
                pass
        return self.report

    # -- signals -----------------------------------------------------------

    def _install_handlers(self) -> None:
        """Own SIGINT/SIGTERM for the life of the context, and only for that.

        Installed here rather than at import so a library consumer's own handlers
        are untouched outside the block. ``signal.signal`` only works on the main
        thread — off it (a test worker, an engine on a side thread) installation is
        skipped entirely, and the context still releases on any exception, which is
        the part that matters.
        """
        if threading.current_thread() is not threading.main_thread():
            return
        for signum in HANDLED_SIGNALS:
            try:
                self._previous[signum] = signal.getsignal(signum)
                signal.signal(signum, self._on_signal)
            except (ValueError, OSError):  # pragma: no cover - platform dependent
                self._previous.pop(signum, None)

    def _restore_handlers(self) -> None:
        for signum, previous in self._previous.items():
            try:
                signal.signal(signum, previous)
            except (ValueError, OSError, TypeError):  # pragma: no cover
                pass
        self._previous.clear()

    def _on_signal(self, signum: int, _frame: Any) -> None:
        """Turn a signal into an exception so the ``with`` block actually unwinds.

        SIGTERM's default action is to kill the process outright, which runs no
        ``finally`` and releases nothing. Raising instead is the only way the exit
        path above gets to run at all.
        """
        senselog.stage(STAGE, "signal", "signal", f"received signal {signum}; releasing")
        if signum == signal.SIGINT:
            raise KeyboardInterrupt
        raise SignalExit(signum)


def owning(
    client,
    *,
    sound_tag: str = DEFAULT_SOUND_TAG,
    timeout: float = DEFAULT_TIMEOUT_S,
    on_release: Callable[[ReleaseReport], None] | None = None,
) -> _Owner:
    """Wrap a whole engine run so an abnormal exit lets go of the duck.

    Usage::

        with owning(client) as owner:
            engine.run(...)
        if owner.report is not None and not owner.report.complete:
            raise CliError(EXIT_ENV_ERROR, owner.report.describe(), "check the daemon")

    On a CLEAN exit nothing is sent at all — a deliberate hold (a posture the
    operator asked the duck to keep) survives the verb that set it. On ANY
    exception, including ``KeyboardInterrupt`` and a SIGTERM converted to
    :class:`SignalExit`, :func:`release_on_exit` runs and the exception continues
    to propagate untouched.
    """
    return _Owner(
        client=client,
        sound_tag=sound_tag,
        timeout=timeout,
        on_release=on_release,
    )


__all__ = [
    "DEFAULT_SOUND_TAG",
    "DEFAULT_TIMEOUT_S",
    "DROP_RELEASE_FAILED",
    "HANDLED_SIGNALS",
    "STAGE",
    "ReleaseReport",
    "ReleaseStep",
    "SignalExit",
    "owning",
    "release_on_exit",
]
