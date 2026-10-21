# SPDX-License-Identifier: AGPL-3.0-or-later
"""Remove every container carrying this run's label; red if any existed.

The reaper is the simple technical stop for this run. It runs with ``if: always()`` before the upload: if a fixture or
probe container survived the deadline, the run is already red, and no receipt may be uploaded green.
"""
from __future__ import annotations

import json
import subprocess
import sys

from .labels import AUTH_MARKER, LABEL_KEY, run_label


class ReaperError(RuntimeError):
    pass


def _list(label):
    result = subprocess.run(["docker", "ps", "-aq", "--filter", f"label={LABEL_KEY}={label}"],
                            check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=30)
    return [line for line in result.stdout.decode("utf-8", "replace").splitlines() if line.strip()]


def reap(label=None):
    label = label or run_label()
    ids = _list(label)
    if not ids:
        return 0
    result = subprocess.run(["docker", "rm", "-f", *ids], check=False,
                            stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, timeout=30)
    remaining = _list(label)
    if result.returncode != 0 or remaining:
        raise ReaperError("containers with this run label survived removal")
    # A container existed after the deadline: the run must be red even though it is now gone.
    return 1


def _authorized(marker, label):
    try:
        with open(marker, "rb") as handle:
            data = json.loads(handle.read())
    except (OSError, ValueError):
        return False
    return data.get("label") == label


def main(argv=None, marker=None):
    marker = marker or AUTH_MARKER
    if not _authorized(marker, run_label()):
        print("ci.reaper: no authorized start for this run; nothing to reap")
        return 0
    try:
        code = reap()
    except (ReaperError, subprocess.SubprocessError, OSError) as exc:
        print(f"ci.reaper: {exc}", file=sys.stderr)
        return 1
    if code:
        print("ci.reaper: leftover container(s) existed and were removed; the run is red", file=sys.stderr)
    else:
        print("ci.reaper: no leftover containers")
    return code


if __name__ == "__main__":
    sys.exit(main())
