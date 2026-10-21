# SPDX-License-Identifier: AGPL-3.0-or-later
"""A tiny trusted wrapper that applies RLIMIT_FSIZE and execs the real command.

Used instead of ``preexec_fn`` in a parent that may have live reader threads: the limit is applied in a fresh process
before the exec, and the parent never forks a child with a Python-level pre-exec hook.
"""
import os
import resource
import sys


def main(argv):
    if len(argv) < 3:
        print("usage: ci.limit_exec BYTES COMMAND [ARG...]", file=sys.stderr)
        return 2
    limit = int(argv[1])
    resource.setrlimit(resource.RLIMIT_FSIZE, (limit, limit))
    os.execvp(argv[2], argv[2:])
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))
