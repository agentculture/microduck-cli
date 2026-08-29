"""Entry point for ``python -m microduck_cli``."""

from __future__ import annotations

import sys

from microduck_cli.cli import main

if __name__ == "__main__":
    sys.exit(main())
