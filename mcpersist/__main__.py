"""Lets `python -m mcpersist ...` work as the CLI entry point (see run.bat)."""

import sys

from .cli import main

if __name__ == "__main__":
    sys.exit(main())
