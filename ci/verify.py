# SPDX-License-Identifier: AGPL-3.0-or-later
"""Build and verify a ci-v1 receipt.

Two algorithms are deliberately separate:

* ``build_receipt`` runs only in the trusted verify job, after the fixture container has stopped and the raw output has
  been sealed. It is the only place a receipt is created.
* ``verify_receipt`` is the consumer/verifier path: it authenticates nothing itself (attestations are checked outside),
  but it validates the receipt's semantics and recomputes every hash from the final sealed bytes.

The local v1.5/bwrap validator is never imported here.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys

from . import archive, canon, limits, schema


class ReceiptError(ValueError):
    """A receipt or its inputs are not acceptable under ci-v1."""


def _sha(data):
    return hashlib.sha256(data).hexdigest()


def _sorted_inputs(inputs):
    ordered = sorted(inputs, key=lambda item: item["path"])
    paths = [item["path"] for item in ordered]
    if len(set(paths)) != len(paths):
        raise ReceiptError("duplicate input path")
    return ordered


def inputspec_digest(inputs):
    return canon.sha256_canonical(_sorted_inputs(inputs))


def fixture_digest(inputs):
    picked = [{"path": item["path"], "sha256": item["sha256"], "bytes": item["bytes"]}
              for item in _sorted_inputs(inputs) if item["role"] == "fixture"]
    return canon.sha256_canonical(picked)


def script_digest(inputs):
    picked = [{"path": item["path"], "sha256": item["sha256"], "bytes": item["bytes"]}
              for item in _sorted_inputs(inputs) if item["role"] == "script"]
    return canon.sha256_canonical(picked)


def config_digest(config):
    return canon.sha256_canonical(config)


def check_isolation(observed, policy):
    expected = policy["isolation"]
    for key in ("network_mode", "readonly_rootfs", "cap_drop", "security_opt", "privileged", "user",
                "memory", "memory_swap", "pids_limit"):
        if key in expected and observed.get(key) != expected[key]:
            raise ReceiptError(f"isolation field {key!r} differs from the policy")
    if observed.get("mounts") != expected.get("mounts"):
        raise ReceiptError("isolation mounts differ from the policy")
    if observed.get("env") != policy.get("env"):
        raise ReceiptError("the observed environment is not exactly the pinned name -> value set")


def fileset_digest(files):
    return canon.sha256_canonical([{"path": path, "sha256": _sha(files[path]), "bytes": len(files[path])}
                                   for path in sorted(files)])


def build_artifacts(files, artifact_meta):
    paths = sorted(files)
    if set(paths) != set(artifact_meta):
        raise ReceiptError("artifact metadata does not cover exactly the sealed files")
    artifacts = []
    for path in paths:
        meta = artifact_meta[path]
        data = files[path]
        is_trace = bool(meta.get("is_trace", False))
        if meta.get("format") == "text" and is_trace:
            raise ReceiptError("text is never a trace")
        if is_trace and meta.get("type") != "trace":
            raise ReceiptError("is_trace requires type=trace")
        artifacts.append({
            "id": path,
            "path": path,
            "type": meta["type"],
            "format": meta["format"],
            "media": meta["media"],
            "encoding": meta["encoding"],
            "executable": False,
            "is_trace": is_trace,
            "sha256": _sha(data),
            "bytes": len(data),
            "truncated": False,
            "complete": True,
        })
    return artifacts


def _check_declared(declared):
    inputs = _sorted_inputs(declared["inputs"])
    if declared["inputspec_sha256"] != inputspec_digest(inputs):
        raise ReceiptError("inputspec_sha256 does not cover the declared inputs")
    if declared["config_digest"] != config_digest(declared["config"]):
        raise ReceiptError("config_digest does not cover the declared config")
    return inputs


def build_receipt(*, policy, spec, run, observation, raw_subjects, attestation_bundle_sha256,
                  outputs_tar, expected_paths):
    """Create the ci-v1 document from the observed run and the final sealed output."""
    inputs = _sorted_inputs(spec["inputs"])
    declared = {
        "fixture_id": spec["fixture_id"],
        "inputs": inputs,
        "inputspec_sha256": inputspec_digest(inputs),
        "argv": list(spec["argv"]),
        "config": spec["config"],
        "config_digest": config_digest(spec["config"]),
    }
    _check_declared(declared)
    if observation["inputs_sha256"] != declared["inputspec_sha256"]:
        raise ReceiptError("the observed input binding differs from the declared one")
    if observation["argv"] != declared["argv"]:
        raise ReceiptError("the observed argv differs from the declared one")
    if observation["config_digest"] != declared["config_digest"]:
        raise ReceiptError("the observed config differs from the declared one")
    files = archive.parse_tar(outputs_tar, require_canonical=True)
    if observation["sealed_output_sha256"] != fileset_digest(files):
        raise ReceiptError("the sealed output hash does not match the observed output")
    if observation.get("isolation") is not None:
        check_isolation(observation["isolation"], policy)
        if observation["isolation_sha256"] != canon.sha256_canonical(observation["isolation"]):
            raise ReceiptError("isolation_sha256 does not cover the observed isolation")
    artifacts = build_artifacts(files, spec["artifacts"])
    if set(files) != set(expected_paths):
        raise ReceiptError("the sealed output set differs from the fixture's expected set")
    total = sum(item["bytes"] for item in artifacts)
    if total > limits.MAX_ARTIFACT_BYTES:
        raise ReceiptError("artifact bytes exceed the cap")
    limit_hit = list(observation.get("limit_hit", []))
    complete = (observation["exit_code"] == 0 and observation["signal"] is None
                and not limit_hit and set(files) == set(expected_paths))
    for item in artifacts:
        item["complete"] = complete
    receipt = {
        "profile": "ci-v1",
        "scope": "fixture-only",
        "schema_sha256": policy["schema_sha256"],
        "run": run,
        "provenance": {"raw": {"subjects": sorted(raw_subjects, key=lambda item: item["name"]),
                               "attestation_bundle_sha256": attestation_bundle_sha256}},
        "image": {"ref_digest": observation["image_ref_digest"], "image_id": observation["image_id"],
                  "tools": dict(policy["tools"]["versions"])},
        "batch_id": spec["batch_id"],
        "items": sorted(spec["items"], key=lambda item: item["item_id"]),
        "declared": declared,
        "observed": {key: observation[key] for key in (
            "image_id", "isolation_sha256", "inputs_sha256", "argv", "config_digest",
            "exit_code", "signal", "started_at", "ended_at", "wall_ms", "cpu_usec",
            "max_rss_bytes", "stdout_sha256", "stderr_sha256", "stdout_bytes", "stderr_bytes",
            "sealed_output_sha256")},
        "artifacts": artifacts,
        "limits": {"files": limits.MAX_ARTIFACT_FILES, "bytes": limits.MAX_ARTIFACT_BYTES,
                   "limit_hit": limit_hit, "usage": {"artifact_bytes": total}},
        "outputs_artifact_sha256": _sha(outputs_tar),
        "licence_set": sorted(spec["licence_set"]),
    }
    return receipt


OBSERVED_KEYS = ("image_id", "isolation_sha256", "inputs_sha256", "argv", "config_digest",
                 "exit_code", "signal", "started_at", "ended_at", "wall_ms", "cpu_usec",
                 "max_rss_bytes", "stdout_sha256", "stderr_sha256", "stdout_bytes", "stderr_bytes",
                 "sealed_output_sha256")


def verify_receipt(receipt, *, policy, spec, outputs_tar, observation):
    """Validate semantics and recompute every hash from the final sealed bytes."""
    with open(policy["schema_path"], "rb") as handle:
        if _sha(handle.read()) != policy["schema_sha256"]:
            raise ReceiptError("the schema file does not match the frozen schema hash")
    schema.validate(receipt, schema.load_schema(policy["schema_path"]))
    if receipt["schema_sha256"] != policy["schema_sha256"]:
        raise ReceiptError("schema_sha256 does not match the frozen policy")
    if receipt["profile"] != "ci-v1":
        raise ReceiptError("wrong profile")
    if receipt.get("scope") != "fixture-only":
        raise ReceiptError("only the fixture-only scope is accepted")
    if receipt["outputs_artifact_sha256"] != _sha(outputs_tar):
        raise ReceiptError("outputs_artifact_sha256 does not match the sealed outputs")
    if receipt["batch_id"] != spec["batch_id"]:
        raise ReceiptError("batch_id does not match the spec")
    if receipt["items"] != sorted(spec["items"], key=lambda item: item["item_id"]):
        raise ReceiptError("items do not match the spec")
    if receipt["image"]["ref_digest"] != spec["image_ref"].split("@", 1)[1]:
        raise ReceiptError("image ref_digest does not match the pinned image")
    if receipt["image"]["ref_digest"] != observation["image_ref_digest"]:
        raise ReceiptError("image ref_digest does not match the observation")
    if receipt["declared"]["fixture_id"] != spec["fixture_id"]:
        raise ReceiptError("fixture_id does not match the spec")
    expected_inputs = sorted(spec["inputs"], key=lambda item: item["path"])
    if receipt["declared"]["inputs"] != expected_inputs:
        raise ReceiptError("declared inputs do not match the approved spec")
    if receipt["declared"]["inputspec_sha256"] != inputspec_digest(spec["inputs"]):
        raise ReceiptError("inputspec_sha256 does not cover the approved spec inputs")
    if receipt["declared"]["argv"] != spec["argv"]:
        raise ReceiptError("declared argv does not match the approved spec")
    if spec["config"].get("env") != policy["env"]:
        raise ReceiptError("the spec environment is not exactly the pinned name -> value set")
    if receipt["declared"]["config_digest"] != config_digest({"env": policy["env"],
                                                              "settings": spec["config"].get("settings", {})}):
        raise ReceiptError("declared config does not match the approved spec")
    if receipt["image"]["image_id"] != receipt["observed"]["image_id"]:
        raise ReceiptError("image_id does not match the observation")
    if not policy["image"].get("image_id"):
        raise ReceiptError("the policy image_id is not pinned")
    if receipt["image"]["image_id"] != policy["image"]["image_id"]:
        raise ReceiptError("image_id does not match the policy pin")
    if receipt["image"]["tools"] != policy["tools"]["versions"]:
        raise ReceiptError("the tool set differs from the frozen policy")
    items = receipt["items"]
    licences = receipt["licence_set"]
    if len(licences) != 1:
        raise ReceiptError("a receipt must carry exactly one licence")
    if licences[0] not in policy["licence_allowlist"]:
        raise ReceiptError("licence is not in the trusted allowlist")
    prefix = policy.get("fixture_item_prefix", "")
    for item in items:
        if not prefix or not item["item_id"].startswith(prefix):
            raise ReceiptError("item id is not a fixture-scoped id")
        if item["licence"]["license"] != licences[0]:
            raise ReceiptError("item licence differs from licence_set")
        if item["licence"]["gate"] != "draft-only":
            raise ReceiptError("licence gate is not draft-only")
    _check_declared(receipt["declared"])
    observed = receipt["observed"]
    if observed["inputs_sha256"] != receipt["declared"]["inputspec_sha256"]:
        raise ReceiptError("observed inputs differ from declared inputs")
    if observed["argv"] != receipt["declared"]["argv"]:
        raise ReceiptError("observed argv differs from declared argv")
    if observed["config_digest"] != receipt["declared"]["config_digest"]:
        raise ReceiptError("observed config differs from declared config")
    files = archive.parse_tar(outputs_tar, require_canonical=True)
    if receipt["observed"]["sealed_output_sha256"] != fileset_digest(files):
        raise ReceiptError("observed.sealed_output_sha256 does not cover the sealed bytes")
    if receipt["declared"]["inputs"] != observation["inputs"]:
        raise ReceiptError("declared inputs differ from the observation")
    if receipt["declared"]["argv"] != observation["argv"]:
        raise ReceiptError("declared argv differs from the observation")
    if receipt["declared"]["config_digest"] != observation["config_digest"]:
        raise ReceiptError("declared config differs from the observation")
    if observation.get("isolation") is None:
        raise ReceiptError("the observation carries no isolation object")
    check_isolation(observation["isolation"], policy)
    if observation["isolation_sha256"] != canon.sha256_canonical(observation["isolation"]):
        raise ReceiptError("observation isolation hash is not self-consistent")
    if set(receipt["observed"]) != set(OBSERVED_KEYS):
        raise ReceiptError("the observed block does not have exactly the expected keys")
    for key in OBSERVED_KEYS:
        if receipt["observed"][key] != observation[key]:
            raise ReceiptError(f"observed.{key} differs from the observation file")
    artifacts = receipt["artifacts"]
    paths = [item["path"] for item in artifacts]
    if paths != sorted(paths) or len(set(paths)) != len(paths):
        raise ReceiptError("artifact paths must be unique and sorted")
    if set(paths) != set(files) or set(paths) != set(spec["expected_paths"]):
        raise ReceiptError("artifact set does not match the sealed output and expected set")
    for item in artifacts:
        if item["id"] != item["path"]:
            raise ReceiptError("artifact id must equal path")
        data = files[item["path"]]
        if item["sha256"] != _sha(data) or item["bytes"] != len(data):
            raise ReceiptError(f"artifact hash/size mismatch: {item['path']!r}")
        if item["executable"] or item["truncated"] or not item["complete"]:
            raise ReceiptError("artifact flags are not acceptable")
        if item["format"] == "text" and item["is_trace"]:
            raise ReceiptError("text is never a trace")
        meta = spec["artifacts"][item["path"]]
        for key in ("type", "format", "media", "encoding"):
            if item[key] != meta[key]:
                raise ReceiptError(f"artifact {key} differs from the spec: {item['path']!r}")
        if bool(item["is_trace"]) != bool(meta.get("is_trace", False)):
            raise ReceiptError(f"artifact is_trace differs from the spec: {item['path']!r}")
    total = sum(item["bytes"] for item in artifacts)
    if total > limits.MAX_ARTIFACT_BYTES or len(files) > limits.MAX_ARTIFACT_FILES:
        raise ReceiptError("artifact limits exceeded")
    if receipt["limits"]["limit_hit"]:
        raise ReceiptError("limit_hit must be empty for a green receipt")
    if "limit_hit" not in observation:
        raise ReceiptError("the observation carries no explicit limit_hit")
    if observation["limit_hit"]:
        raise ReceiptError("the observation recorded a limit hit")
    policy_limits = policy["limits"]
    if observed["wall_ms"] > policy_limits["wall_ms"]:
        raise ReceiptError("wall time exceeds the policy limit")
    if observed["cpu_usec"] > policy_limits["cpu_usec"]:
        raise ReceiptError("CPU use exceeds the policy limit")
    if observed["max_rss_bytes"] > policy_limits["memory_bytes"]:
        raise ReceiptError("memory use exceeds the policy limit")
    if observed["stdout_bytes"] > policy_limits["stdout_bytes"]:
        raise ReceiptError("stdout exceeds the policy limit")
    if observed["stderr_bytes"] > policy_limits["stderr_bytes"]:
        raise ReceiptError("stderr exceeds the policy limit")
    if receipt["limits"]["limit_hit"] != observation.get("limit_hit", []):
        raise ReceiptError("limit_hit differs from the observation")
    if receipt["limits"]["usage"]["artifact_bytes"] != total:
        raise ReceiptError("usage.artifact_bytes does not equal the artifact sum")
    complete = (observed["exit_code"] == 0 and observed["signal"] is None
                and set(paths) == set(spec["expected_paths"]))
    if not complete:
        raise ReceiptError("a receipt may only be green for a complete observed run")
    return receipt


def _load(path):
    with open(path, "rb") as handle:
        return canon.loads(handle.read())


def main(argv=None):
    parser = argparse.ArgumentParser(prog="ci.verify")
    sub = parser.add_subparsers(dest="mode", required=True)
    build = sub.add_parser("build")
    build.add_argument("--policy", required=True)
    build.add_argument("--spec", required=True)
    build.add_argument("--run", required=True)
    build.add_argument("--observation", required=True)
    build.add_argument("--raw", required=True, help="raw.tar (for the raw subject hash)")
    build.add_argument("--bundle", required=True, help="attestation bundle bytes")
    build.add_argument("--outputs", required=True, help="outputs.tar")
    build.add_argument("--out", required=True)
    verify = sub.add_parser("verify")
    verify.add_argument("--policy", required=True)
    verify.add_argument("--spec", required=True)
    verify.add_argument("--receipt", required=True)
    verify.add_argument("--outputs", required=True)
    verify.add_argument("--sibling", help="receipt.sha256 sibling to check")
    verify.add_argument("--observation", required=True, help="attested observation.json to cross-check")
    args = parser.parse_args(argv)
    policy = _load(args.policy)
    spec = _load(args.spec)
    if args.mode == "build":
        run = _load(args.run)
        observation = _load(args.observation)
        with open(args.raw, "rb") as handle:
            raw = handle.read()
        with open(args.bundle, "rb") as handle:
            bundle = handle.read()
        with open(args.outputs, "rb") as handle:
            outputs = handle.read()
        receipt = build_receipt(policy=policy, spec=spec, run=run, observation=observation,
                                raw_subjects=[{"name": "raw.tar", "sha256": _sha(raw)},
                                              {"name": "observation.json",
                                               "sha256": _sha(open(args.observation, "rb").read())}],
                                attestation_bundle_sha256=_sha(bundle), outputs_tar=outputs,
                                expected_paths=spec["expected_paths"])
        data = canon.dumps(receipt)
        with open(args.out, "wb") as handle:
            handle.write(data)
        sibling = os.path.splitext(args.out)[0] + ".sha256"
        with open(sibling, "wb") as handle:
            handle.write((_sha(data) + "\n").encode("ascii"))
        return 0
    with open(args.outputs, "rb") as handle:
        outputs = handle.read()
    receipt = _load(args.receipt)
    observation = _load(args.observation)
    verify_receipt(receipt, policy=policy, spec=spec, outputs_tar=outputs, observation=observation)
    if args.sibling:
        with open(args.sibling, "rb") as handle:
            sibling = handle.read().decode("ascii").strip()
        with open(args.receipt, "rb") as handle:
            if sibling != _sha(handle.read()):
                raise ReceiptError("receipt.sha256 does not match receipt.json")
    print("ci-v1 receipt structurally verified (not an authenticity gate; use ci.consume)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
