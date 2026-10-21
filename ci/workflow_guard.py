# SPDX-License-Identifier: AGPL-3.0-or-later
"""The workflow guard: the CI fixture workflow refuses to run without the registered test mandate.

This is intentionally a hard stop, not a warning. Until a reviewed commit sets ``enabled: true`` in ``ci/MANDATE.json``
**and** the same commit replaces ``PINNED_MANDATE_SHA256`` below with that file's sha256, every real launch entry point
exits non-zero before any Docker or fixture step. The checkout that precedes the guard in the workflow is a trusted read,
not a fixture or launch step. The pin is what a writer cannot silently satisfy in a different commit: the consumer also
pins the workflow/commit digest externally.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import sys

# Replaced only when the registered test mandate exists. None means "the workflow can never run".
PINNED_MANDATE_SHA256 = None


class Refused(Exception):
    pass


def _sha(path):
    with open(path, "rb") as handle:
        return hashlib.sha256(handle.read()).hexdigest()


def _load(path):
    with open(path, "rb") as handle:
        return json.loads(handle.read())


def check(root="."):
    mandate_path = os.path.join(root, "ci/MANDATE.json")
    policy_path = os.path.join(root, "ci/policy.json")
    active_path = os.path.join(root, "ci/ACTIVE.json")
    if PINNED_MANDATE_SHA256 is None:
        raise Refused("no test mandate is registered (guard pin is unset)")
    if _sha(mandate_path) != PINNED_MANDATE_SHA256:
        raise Refused("the mandate file does not match the pinned sha256")
    mandate = _load(mandate_path)
    if mandate.get("enabled") is not True:
        raise Refused("the mandate is not enabled")
    not_after = mandate.get("not_after")
    if not_after:
        try:
            deadline = dt.datetime.fromisoformat(not_after.replace("Z", "+00:00"))
        except ValueError:
            raise Refused("the mandate expiry is not ISO 8601") from None
        now = dt.datetime.now(dt.timezone.utc)
        if now > deadline:
            raise Refused("the mandate has expired")
    active = _load(active_path)
    spec_rel = active.get("spec")
    if not isinstance(spec_rel, str) or not spec_rel.startswith("ci/specs/"):
        raise Refused("the active spec path is not under ci/specs/")
    if spec_rel not in mandate.get("allowed_specs", []):
        raise Refused("the active spec is not in the mandate's allowlist")
    spec = _load(os.path.join(root, spec_rel))
    policy = _load(policy_path)
    if not policy.get("image", {}).get("ref") or not policy.get("image", {}).get("image_id"):
        raise Refused("the runtime image ref and image id are not pinned")
    if not spec.get("image_ref"):
        raise Refused("the spec has no pinned image")
    if spec["image_ref"] != policy["image"]["ref"]:
        raise Refused("the spec image differs from the policy image")
    for item in spec.get("inputs", []):
        digest = item.get("sha256", "")
        if len(digest) != 64 or set(digest) == {"0"}:
            raise Refused(f"input {item.get('path')!r} has no reviewed hash")
    if not spec.get("expected_paths"):
        raise Refused("the spec has no expected output set")
    return True


def require_mandate():
    """Every launch/network entry point calls this before any subprocess or socket."""
    check(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def main(argv=None):
    root = (argv or sys.argv[1:] or ["."])[0]
    try:
        check(root)
    except (Refused, OSError, json.JSONDecodeError) as exc:
        print(f"ci-fixture refused: {exc}", file=sys.stderr)
        return 2
    print("ci-fixture mandate accepted")
    return 0


if __name__ == "__main__":
    sys.exit(main())
