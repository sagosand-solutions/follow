# SPDX-License-Identifier: AGPL-3.0-or-later
"""Per-run labels shared by the fixture containers, the tool probes and the reaper."""
import os

LABEL_KEY = "ci-fixture.run"
AUTH_MARKER = ".ci-run-authorized.json"


def run_label(strict=False):
    run = os.environ.get("GITHUB_RUN_ID")
    attempt = os.environ.get("GITHUB_RUN_ATTEMPT")
    if run and attempt:
        return f"{run}-{attempt}"
    if strict:
        return None
    return "local-1"


def label_argument():
    return f"{LABEL_KEY}={run_label()}"
