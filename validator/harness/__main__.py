# SPDX-License-Identifier: MIT
"""The harness entry point. Nothing here launches a process; ``run`` refuses by design."""
import sys

from .cli import main

if __name__ == "__main__":
    sys.exit(main())
