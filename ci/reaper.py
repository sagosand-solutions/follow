# SPDX-License-Identifier: AGPL-3.0-or-later
"""Remove every container carrying this run's label; red if any existed.

The reaper is the simple technical stop for this run. It runs with ``if: always()`` before the upload: if a fixture or
probe container survived the deadline, the run is already red, and no receipt may be uploaded green.
"""
from __future__ import annotations

import hashlib
import json
import os
import stat
import subprocess
import sys

from . import workflow_guard
from .labels import AUTH_MARKER, LABEL_KEY, run_label


class ReaperError(RuntimeError):
    pass


def _list(label):
    result = subprocess.run(["docker", "ps", "-aq", "--filter", f"label={LABEL_KEY}={label}"],
                            check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=30)
    return [line for line in result.stdout.decode("utf-8", "replace").splitlines() if line.strip()]


def reap(label):
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


MARKER_KEYS = {"label", "run_id", "run_attempt", "source_digest", "spec_sha256", "mandate_sha256"}
_MISSING = object()


def _read_marker(path):
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC)
    except FileNotFoundError:
        return _MISSING
    except OSError as exc:
        raise ReaperError(f"the authorization marker cannot be opened: {exc.strerror}") from None
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise ReaperError("the authorization marker is not a regular file")
        data = os.read(fd, 4097)
        if len(data) > 4096:
            raise ReaperError("the authorization marker is too large")
    finally:
        os.close(fd)
    try:
        parsed = json.loads(data)
    except ValueError:
        raise ReaperError("the authorization marker is not valid JSON") from None
    if parsed is None:
        raise ReaperError("the authorization marker is null")
    return parsed


def _sha(path):
    with open(path, "rb") as handle:
        return hashlib.sha256(handle.read()).hexdigest()


def _authorized(marker, label, policy_path, mandate_path):
    data = _read_marker(marker)
    if data is _MISSING:
        return False
    if not isinstance(data, dict) or set(data) != MARKER_KEYS:
        raise ReaperError("the authorization marker has an unexpected key set")
    if label is None:
        raise ReaperError("no trusted run identity for the authorization marker")
    if data["label"] != label:
        raise ReaperError("the authorization marker has a different label")
    if data["run_id"] != os.environ.get("GITHUB_RUN_ID") or data["run_attempt"] != os.environ.get("GITHUB_RUN_ATTEMPT"):
        raise ReaperError("the authorization marker has a different run identity")
    if data["source_digest"] != os.environ.get("GITHUB_SHA"):
        raise ReaperError("the authorization marker has a different source digest")
    try:
        with open(policy_path, "rb") as handle:
            policy = json.loads(handle.read())
    except (OSError, ValueError):
        raise ReaperError("the frozen policy cannot be read") from None
    spec_path = policy.get("spec_path")
    if not isinstance(spec_path, str) or not spec_path:
        raise ReaperError("the frozen policy has no spec path")
    try:
        spec_sha = _sha(spec_path)
    except OSError:
        raise ReaperError("the frozen spec cannot be read") from None
    if spec_sha != policy.get("spec_sha256"):
        raise ReaperError("the frozen policy spec hash does not match the spec")
    if data["spec_sha256"] != spec_sha:
        raise ReaperError("the authorization marker has a different spec hash")
    # The marker binds the mandate bytes that authorized the start. Recomputing the current file means a
    # mandate changed or disabled after the start is red, not a cleanup authorization. A disabled commit is
    # not itself a run authorization; it is only the bytes the earlier guard accepted.
    try:
        mandate_sha = _sha(mandate_path)
    except OSError:
        raise ReaperError("the mandate cannot be read") from None
    if not isinstance(data["mandate_sha256"], str) or data["mandate_sha256"] != mandate_sha:
        raise ReaperError("the authorization marker has a different mandate hash")
    return True


def main(argv=None, marker=None, policy_path="ci/policy.json", mandate_path=None):
    marker = marker or AUTH_MARKER
    mandate_path = mandate_path or workflow_guard.MANDATE_PATH
    label = run_label(strict=True)
    try:
        if not _authorized(marker, label, policy_path, mandate_path):
            print("ci.reaper: no authorized start for this run; nothing to reap")
            return 0
    except ReaperError as exc:
        print(f"ci.reaper: {exc}", file=sys.stderr)
        return 1
    try:
        code = reap(label)
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
