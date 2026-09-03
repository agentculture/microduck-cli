"""The duck behaviour runtime: model, sense, rules, and the tick engine.

Layered leaf-first. :mod:`.model`, :mod:`.sense` and :mod:`.rules` are pure —
value objects and pure functions, no I/O beyond reading a rules file — and
:mod:`.engine` is the only module with a loop. :mod:`.liveness` and
:mod:`.senselog` are its two side-effecting helpers (one file, one logger).

The engine lives HERE by decision c20 (see ``CLAUDE.md``), built extraction-first
behind the seams a later move to ``neurosymbolic-system`` needs: ``TargetSink``,
``SenseProviders``, ``tick_seam``, rules-as-data, one admission registry, and a
heartbeat rather than a flag file. Nothing in this package imports a transport or
an SDK.
"""

from __future__ import annotations
