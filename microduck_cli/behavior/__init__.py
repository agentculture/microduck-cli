"""The duck behavior package: a data-only rules schema (:mod:`.rules`).

This package holds no engine, no I/O beyond reading a rules file, and no
transport. It is deliberately pure so it can be validated, tested, and reused
by whatever evaluator eventually composes it onto the neurosymbolic-system
tick — see ``microduck_cli/CLAUDE.md`` for why that runtime lives upstream and
is not implemented here.
"""

from __future__ import annotations
