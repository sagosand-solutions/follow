# SPDX-License-Identifier: AGPL-3.0-or-later
"""Per-run labels shared by the fixture containers, the tool probes and the reaper."""
import os

LABEL_KEY = "ci-fixture.run"
AUTH_MARKER = ".ci-run-authorized.json"


def run_label():
    run = os.environ.get("GITHUB_RUN_ID", "local")
    attempt = os.environ.get("GITHUB_RUN_ATTEMPT", "1")
    return f"{run}-{attempt}"


def label_argument():
    return f"{LABEL_KEY}={run_label()}"
