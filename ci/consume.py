# SPDX-License-Identifier: AGPL-3.0-or-later
"""Fail-closed consumer path for a ci-v1 receipt.

This is the authenticity gate that the structural ``ci.verify verify`` command is not: it authenticates **both** the raw
subjects and the sealed subjects against the pinned repository/workflow/issuer/predicate/source digest/run/attempt,
verifies the two bundles, and only then validates the receipt semantics against the sealed bytes and the attested
observation.
"""
from __future__ import annotations

import argparse
import hashlib
import os
import sys
import tempfile

from . import canon, steps, verify, workflow_guard


class ConsumeError(RuntimeError):
    pass


def _load(path):
    with open(path, "rb") as handle:
        return canon.loads(handle.read())


def _read(path, cap):
    with open(path, "rb") as handle:
        data = handle.read(cap + 1)
    if len(data) > cap:
        raise ConsumeError(f"{path} exceeds its cap")
    return data


def _write(path, data):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
    with os.fdopen(fd, "wb") as handle:
        handle.write(data)


def consume(*, policy_path, spec_path, observation_path, outputs_path, receipt_path, sibling_path,
            run_path, raw_subjects, receipt_subjects, bundle_dir, raw_bundle_path=None, context=None):
    try:
        workflow_guard.require_mandate()
    except workflow_guard.Refused as exc:
        raise ConsumeError(f"no registered test mandate: {exc}") from None
    if not os.environ.get("GH_TOKEN"):
        raise ConsumeError("GH_TOKEN is required for the attestation checks")
    if not context:
        raise ConsumeError("an explicit trusted repository/run/source context is required")
    if not raw_bundle_path:
        raise ConsumeError("the sealed raw attestation bundle is required")
    os.makedirs(bundle_dir, exist_ok=True)
    snapshot = {
        "observation": _read(observation_path, 65_536),
        "outputs": _read(outputs_path, 4_718_592),
        "receipt": _read(receipt_path, 65_536),
        "sibling": _read(sibling_path, 128),
        "raw": {os.path.basename(path): _read(path, 4_718_592) for path in raw_subjects},
        "sealed": {os.path.basename(path): _read(path, 4_718_592) for path in receipt_subjects},
        "bundle": _read(raw_bundle_path, 4 * 1024 * 1024),
    }
    if set(snapshot["raw"]) != {"raw.tar", "observation.json"}:
        raise ConsumeError("the raw subject set is not exactly raw.tar + observation.json")
    if set(snapshot["sealed"]) != {"outputs.tar", "receipt.json"}:
        raise ConsumeError("the sealed subject set is not exactly outputs.tar + receipt.json")
    if os.path.basename(observation_path) != "observation.json" or snapshot["observation"] != snapshot["raw"]["observation.json"]:
        raise ConsumeError("the consumed observation is not the attested observation")
    if os.path.basename(outputs_path) != "outputs.tar" or snapshot["outputs"] != snapshot["sealed"]["outputs.tar"]:
        raise ConsumeError("the consumed outputs are not the attested outputs")
    if os.path.basename(receipt_path) != "receipt.json" or snapshot["receipt"] != snapshot["sealed"]["receipt.json"]:
        raise ConsumeError("the consumed receipt is not the attested receipt")
    if snapshot["sibling"].decode("ascii").strip() != hashlib.sha256(snapshot["receipt"]).hexdigest():
        raise ConsumeError("receipt.sha256 does not match the receipt snapshot")
    receipt = canon.loads(snapshot["receipt"])
    observation = canon.loads(snapshot["observation"])
    expected_run = canon.loads(_read(run_path, 65_536))
    if receipt["run"] != expected_run:
        raise ConsumeError("the receipt run does not match the expected run metadata")
    if (str(receipt["run"]["run_id"]) != str(context["run_id"])
            or str(receipt["run"]["run_attempt"]) != str(context["run_attempt"])
            or receipt["run"]["commit_sha"] != context["source_digest"]):
        raise ConsumeError("the receipt run does not match the trusted verification context")
    if expected_run.get("commit_sha") != context["source_digest"]:
        raise ConsumeError("the run file source digest differs from the trusted context")
    if receipt["provenance"]["raw"]["attestation_bundle_sha256"] != hashlib.sha256(snapshot["bundle"]).hexdigest():
        raise ConsumeError("the receipt does not name the sealed attestation bundle")
    with tempfile.TemporaryDirectory() as temp:
        raw_dir = os.path.join(temp, "raw")
        sealed_dir = os.path.join(temp, "sealed")
        os.makedirs(raw_dir)
        os.makedirs(sealed_dir)
        raw_paths = []
        for name, data in snapshot["raw"].items():
            target = os.path.join(raw_dir, name)
            _write(target, data)
            raw_paths.append(target)
        sealed_paths = []
        for name, data in snapshot["sealed"].items():
            target = os.path.join(sealed_dir, name)
            _write(target, data)
            sealed_paths.append(target)
        raw_bundle = os.path.join(bundle_dir, "raw.bundle")
        bundle_copy = os.path.join(temp, "bundle.bin")
        _write(bundle_copy, snapshot["bundle"])
        raw_context = steps.attest_verify(policy_path, raw_paths, raw_bundle,
                                          bundle_paths=[bundle_copy] * len(raw_paths), context=context)
        sealed_context = steps.attest_verify(policy_path, sealed_paths,
                                             os.path.join(bundle_dir, "sealed.bundle"), context=context)
        if raw_context != sealed_context:
            raise ConsumeError("the raw and sealed attestations name different contexts")
    raw_digests = {name: hashlib.sha256(data).hexdigest() for name, data in snapshot["raw"].items()}
    declared = {item["name"]: item["sha256"] for item in receipt["provenance"]["raw"]["subjects"]}
    if declared != raw_digests:
        raise ConsumeError("the receipt does not bind the raw subjects that were verified")
    if receipt["outputs_artifact_sha256"] != hashlib.sha256(snapshot["outputs"]).hexdigest():
        raise ConsumeError("the receipt does not bind the sealed outputs")
    policy = _load(policy_path)
    spec = _load(spec_path)
    verify.verify_receipt(receipt, policy=policy, spec=spec, outputs_tar=snapshot["outputs"],
                          observation=observation)
    print("ci-v1 receipt consumed (both attestations and the sealed snapshot verified)")
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(prog="ci.consume")
    parser.add_argument("--policy", required=True)
    parser.add_argument("--spec", required=True)
    parser.add_argument("--observation", required=True)
    parser.add_argument("--outputs", required=True)
    parser.add_argument("--receipt", required=True)
    parser.add_argument("--sibling", required=True)
    parser.add_argument("--raw", nargs="+", required=True)
    parser.add_argument("--sealed", nargs="+", required=True)
    parser.add_argument("--bundle-dir", required=True)
    parser.add_argument("--run", required=True)
    parser.add_argument("--raw-bundle", help="the sealed raw attestation bundle artifact")
    parser.add_argument("--repo", required=True)
    parser.add_argument("--source-digest", required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--run-attempt", required=True)
    args = parser.parse_args(argv)
    try:
        return consume(policy_path=args.policy, spec_path=args.spec, observation_path=args.observation,
                       outputs_path=args.outputs, receipt_path=args.receipt, sibling_path=args.sibling,
                       raw_subjects=args.raw, receipt_subjects=args.sealed, bundle_dir=args.bundle_dir,
                       run_path=args.run, raw_bundle_path=args.raw_bundle,
                       context={"repo": args.repo, "source_digest": args.source_digest,
                                "run_id": args.run_id, "run_attempt": args.run_attempt})
    except (ConsumeError, steps.StepError, verify.ReceiptError, OSError, ValueError, KeyError) as exc:
        print(f"ci.consume: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
