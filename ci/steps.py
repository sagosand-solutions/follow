# SPDX-License-Identifier: AGPL-3.0-or-later
"""The small trusted steps used by the disabled CI fixture workflow.

Every command here runs on the trusted side of its job. The fixture container never sees these files or tokens, and the
verify job treats the raw wrapper and the raw tar as opaque bytes until the bounded parsers accept them.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile

from . import archive, canon, limits, schema, workflow_guard


class StepError(RuntimeError):
    pass


def _sha(data):
    return hashlib.sha256(data).hexdigest()


def _read(path, cap):
    with open(path, "rb") as handle:
        data = handle.read(cap + 1)
    if len(data) > cap:
        raise StepError(f"{path} exceeds {cap} bytes")
    return data


def _write_new(path, data, mode=0o444):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC, mode)
    with os.fdopen(fd, "wb") as handle:
        handle.write(data)


def _read_beneath(root, relative, cap):
    parts = relative.split("/")
    if relative.startswith("/") or any(part in ("", ".", "..") for part in parts):
        raise StepError(f"unsafe input path: {relative!r}")
    opened = []
    try:
        current = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
        opened.append(current)
        for part in parts[:-1]:
            current = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=current)
            opened.append(current)
        fd = os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC, dir_fd=current)
        opened.append(fd)
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise StepError(f"input {relative!r} is not a regular file")
        chunks, total = [], 0
        while total <= cap:
            chunk = os.read(fd, min(65536, cap + 1 - total))
            if not chunk:
                break
            chunks.append(chunk)
            total += len(chunk)
        if total > cap:
            raise StepError(f"input {relative!r} exceeds {cap} bytes")
        return b"".join(chunks)
    finally:
        for fd in reversed(opened):
            os.close(fd)


def prepare_inputs(spec_path, dest):
    with open(spec_path, "rb") as handle:
        spec = canon.loads(handle.read())
    base = os.path.splitext(os.path.abspath(spec_path))[0]
    if not os.path.isdir(base):
        raise StepError(f"spec input directory is missing: {base}")
    os.makedirs(dest, exist_ok=True)
    if os.listdir(dest):
        raise StepError("the input directory must be empty")
    for item in spec["inputs"]:
        try:
            data = _read_beneath(base, item["path"], limits.MAX_ARTIFACT_BYTES)
        except OSError as exc:
            raise StepError(f"input {item['path']!r} cannot be read: {exc.strerror}") from None
        if len(data) != item["bytes"] or _sha(data) != item["sha256"]:
            raise StepError(f"input {item['path']!r} does not match its reviewed hash")
        target = os.path.join(dest, item["path"])
        os.makedirs(os.path.dirname(target) or dest, exist_ok=True)
        _write_new(target, data, 0o444)
    return 0


def check_raw(directory):
    names = sorted(os.listdir(directory))
    if names != ["observation.json", "raw.tar"]:
        raise StepError("the raw artifact must contain exactly raw.tar and observation.json")
    tar = _read(os.path.join(directory, "raw.tar"), limits.MAX_RAW_TAR_BYTES)
    obs = _read(os.path.join(directory, "observation.json"), limits.MAX_OBSERVATION_BYTES)
    print(json.dumps({"raw.tar": _sha(tar), "observation.json": _sha(obs)}, sort_keys=True))
    return 0


def extract_wrapper(zip_path, dest, allowed=None):
    allowed = allowed or ["raw.tar", "observation.json"]
    caps = {"raw.tar": limits.MAX_RAW_TAR_BYTES, "observation.json": limits.MAX_OBSERVATION_BYTES,
            "receipt.json": limits.MAX_OBSERVATION_BYTES, "receipt.sha256": 128,
            "outputs.tar": limits.MAX_RAW_TAR_BYTES}
    data = _read(zip_path, limits.MAX_ZIP_BYTES)
    files = archive.parse_zip(data, allowed_names=set(allowed),
                              entry_caps={name: caps[name] for name in allowed if name in caps})
    os.makedirs(dest, exist_ok=True)
    for name, payload in files.items():
        _write_new(os.path.join(dest, name), payload, 0o444)
    return 0


def pack_outputs(raw_path, out_path):
    data = _read(raw_path, limits.MAX_RAW_TAR_BYTES)
    files = archive.parse_tar(data, require_canonical=True)
    packed = archive.pack_tar(files)
    with open(out_path, "wb") as handle:
        handle.write(packed)
    print(_sha(packed))
    return 0


def write_run(out_path):
    try:
        workflow_guard.require_mandate()
    except workflow_guard.Refused as exc:
        raise StepError(f"no registered test mandate: {exc}") from None
    required = ("GITHUB_RUN_ID", "GITHUB_RUN_ATTEMPT", "GITHUB_SHA", "GITHUB_REF", "GITHUB_EVENT_NAME")
    values = {}
    for name in required:
        value = os.environ.get(name)
        if not value:
            raise StepError(f"{name} is not set")
        values[name] = value
    if values["GITHUB_EVENT_NAME"] != "workflow_dispatch":
        raise StepError("only workflow_dispatch is accepted")
    tree = subprocess.run(["git", "rev-parse", "HEAD^{tree}"], check=True,
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE).stdout.decode().strip()
    workflow_sha = os.environ.get("GITHUB_WORKFLOW_SHA") or values["GITHUB_SHA"]
    run = {
        "run_id": int(values["GITHUB_RUN_ID"]),
        "run_attempt": int(values["GITHUB_RUN_ATTEMPT"]),
        "workflow_sha": workflow_sha,
        "event": "workflow_dispatch",
        "ref": values["GITHUB_REF"],
        "commit_sha": values["GITHUB_SHA"],
        "tree_sha": tree,
    }
    with open(out_path, "wb") as handle:
        handle.write(canon.dumps(run))
    return 0


def run_invocation_pattern(repo, run_id, attempt):
    return (rf"https://github\.com/{re.escape(repo)}/actions/runs/{re.escape(str(run_id))}/attempts/"
            rf"{re.escape(str(attempt))}(?![0-9])")


GH_ATTESTATION_PROFILE = "gh-attestation-v1"


def _repo_root():
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def require_pins(policy, root=None):
    missing = workflow_guard.pin_set_missing(policy)
    if missing:
        raise StepError(f"the pin set is incomplete: {', '.join(missing)}")
    wanted = policy.get("tools", {}).get("gh_version")
    if not wanted:
        raise StepError("the gh version is not pinned in the policy")
    attestation = policy.get("attestation", {})
    if not attestation.get("signer_workflow") or not attestation.get("signer_ref"):
        raise StepError("the signer workflow and source ref must both be pinned")
    if not attestation.get("trusted_root_path") or not attestation.get("trusted_root_sha256"):
        raise StepError("the Sigstore trusted root path and hash must both be pinned")
    if attestation.get("gh_profile") != GH_ATTESTATION_PROFILE or not attestation.get("gh_example_sha256"):
        raise StepError("the gh-attestation-v1 profile and its frozen example hash must be pinned")
    example_path = attestation.get("gh_example_path")
    if not example_path:
        raise StepError("the frozen gh example path is not pinned")
    resolved = os.path.join(root or _repo_root(), example_path)
    try:
        with open(resolved, "rb") as handle:
            example = handle.read(4 * 1024 * 1024)
    except OSError as exc:
        raise StepError(f"the frozen gh example cannot be read: {exc.strerror}") from None
    if _sha(example) != attestation["gh_example_sha256"]:
        raise StepError("the frozen gh example does not match its pinned hash")


def check_tools(policy_path):
    try:
        workflow_guard.require_mandate()
    except workflow_guard.Refused as exc:
        raise StepError(f"no registered test mandate: {exc}") from None
    with open(policy_path, "rb") as handle:
        policy = canon.loads(handle.read())
    require_pins(policy)
    wanted = policy["tools"]["gh_version"]
    result = subprocess.run(["gh", "--version"], check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    first = result.stdout.decode().splitlines()[0]
    match = re.search(r"gh version (\S+)", first)
    if match is None or match.group(1) != wanted:
        raise StepError(f"gh version does not match the pinned {wanted!r}")
    isolation = policy["isolation"]
    image = policy["image"]["ref"]

    def in_image(args):
        return _run_bounded(["docker", "run", "--rm", "--network", isolation["network_mode"],
                             "--read-only", "--user", isolation["user"], "--cap-drop", "ALL",
                             "--security-opt", "no-new-privileges",
                             "--pids-limit", str(isolation["pids_limit"]),
                             "--memory", f"{isolation['memory'] // (1024 * 1024)}m",
                             "--memory-swap", f"{isolation['memory_swap'] // (1024 * 1024)}m",
                             "--cpus", "1", "--log-driver", "none",
                             "--tmpfs", "/tmp:rw,noexec,nosuid,nodev,size=16m,nr_inodes=64",
                             image] + args, timeout=120, limit=64 * 1024)

    git_pin = policy["tools"]["versions"].get("git")
    if git_pin and git_pin != "unused":
        upstream = git_pin.split(":", 1)[-1].split("-", 1)[0]
        if in_image(["/usr/bin/git", "--version"]).decode().strip() != f"git version {upstream}":
            raise StepError(f"git --version does not match the pinned {git_pin!r}")
        if in_image(["/usr/bin/dpkg-query", "-W", "-f=${Version}", "git"]).decode().strip() != git_pin:
            raise StepError("the image dpkg git version does not match the pin")
    python_pin = policy["tools"]["versions"].get("python")
    if python_pin and python_pin != "unused":
        if in_image(["/usr/local/bin/python3", "--version"]).decode().strip() != f"Python {python_pin}":
            raise StepError(f"python3 --version does not match the pinned {python_pin!r}")
    return 0


def _collect(value, key):
    found = []
    if isinstance(value, dict):
        for name, item in value.items():
            if name == key:
                found.append(item)
            found.extend(_collect(item, key))
    elif isinstance(value, list):
        for item in value:
            found.extend(_collect(item, key))
    return found


def _require_dict(value, label):
    if not isinstance(value, dict):
        raise StepError(f"the attestation has no {label} object")
    return value


def check_attestation_payload(payload, *, subject_name, subject_sha256, issuer, predicate, repo, run_id,
                              attempt, source_digest):
    """Explicit adapter for the pinned gh-attestation-v1 shape.

    Only the exact verificationResult.statement/certificate fields are read; nothing is searched recursively. A nested
    metadata object that happens to carry the right names is not an authenticated claim and is ignored. Unknown, missing
    or ambiguous shapes are red.
    """
    expected_uri = f"https://github.com/{repo}"
    expected_invocation = f"{expected_uri}/actions/runs/{run_id}/attempts/{attempt}"
    entries = payload if isinstance(payload, list) else [payload]
    matching = []
    for entry in entries:
        result = entry.get("verificationResult") if isinstance(entry, dict) else None
        if not isinstance(result, dict):
            continue
        statement = result.get("statement")
        signature = result.get("signature")
        certificate = signature.get("certificate") if isinstance(signature, dict) else None
        if not isinstance(statement, dict) or not isinstance(certificate, dict):
            continue
        run_details = statement.get("predicate", {}).get("runDetails", {})
        metadata = run_details.get("metadata", {}) if isinstance(run_details, dict) else {}
        if not isinstance(metadata, dict) or metadata.get("invocationId") != expected_invocation:
            continue
        subjects = statement.get("subject")
        if not isinstance(subjects, list):
            continue
        exact = 0
        for candidate in subjects:
            if not isinstance(candidate, dict):
                continue
            digest = candidate.get("digest")
            if not isinstance(digest, dict):
                continue
            if candidate.get("name") == subject_name and digest.get("sha256") == subject_sha256:
                exact += 1
        if exact > 1:
            raise StepError("the verified output has an ambiguous subject in one attestation entry")
        if exact == 1:
            matching.append((result, statement, certificate))
    if len(matching) != 1:
        raise StepError("the verified output has no single gh-attestation-v1 entry for this run, attempt and subject")
    result, statement, certificate = matching[0]
    if certificate.get("issuer") != issuer:
        raise StepError("the verified certificate does not carry exactly the pinned issuer")
    if statement.get("predicateType") != predicate:
        raise StepError("the verified statement does not carry exactly the pinned predicate")
    if certificate.get("sourceRepositoryURI") != expected_uri:
        raise StepError("the verified certificate does not carry exactly the pinned source repository")
    if certificate.get("sourceRepositoryDigest") != source_digest:
        raise StepError("the verified certificate does not carry exactly the pinned source digest")
    subjects = statement.get("subject")
    if not isinstance(subjects, list):
        raise StepError("the verified statement has no subject list")
    matched = 0
    for candidate in subjects:
        if not isinstance(candidate, dict):
            raise StepError("a subject entry is not an object")
        digest = candidate.get("digest")
        if not isinstance(digest, dict):
            raise StepError("a subject entry has no digest object")
        if candidate.get("name") == subject_name and digest.get("sha256") == subject_sha256:
            matched += 1
        elif digest.get("sha256") == subject_sha256:
            raise StepError("a subject digest matches under a different name")
    if matched != 1:
        raise StepError("the verified statement does not cover exactly the chosen subject")
    return True


def _limit_wrapper(command, limit):
    return [sys.executable, "-m", "ci.limit_exec", str(limit)] + list(command)


def _read_regular(path, cap):
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC)
    except OSError as exc:
        raise StepError(f"cannot read {path}: {exc.strerror}") from None
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise StepError(f"{path} is not a regular file")
        data = os.read(fd, cap + 1)
        if len(data) > cap:
            raise StepError(f"{path} exceeds {cap} bytes")
        return data
    finally:
        os.close(fd)


GH_ATTESTATION_LIMIT = 30


def _download_bundle(subject, repo, predicate, download_dir):
    subject = os.path.abspath(subject)
    subject_data = _read_regular(subject, limits.MAX_RAW_TAR_BYTES)
    expected = f"sha256:{_sha(subject_data)}.jsonl"
    command = ["bash", "-c", 'cd "$1" || exit 97; shift; exec "$@"', "_", download_dir,
               "gh", "attestation", "download", subject, "--repo", repo,
               "--predicate-type", predicate, "--limit", str(GH_ATTESTATION_LIMIT)]
    _run_bounded(command, timeout=120, limit=4 * 1024 * 1024)
    if sorted(os.listdir(download_dir)) != [expected]:
        raise StepError("the attestation download did not produce exactly its expected bundle")
    target = os.path.join(download_dir, expected)
    return target, _read_regular(target, 4 * 1024 * 1024)


def _run_bounded(command, timeout, limit):
    with tempfile.TemporaryDirectory() as temp:
        stdout_path = os.path.join(temp, "stdout")
        stderr_path = os.path.join(temp, "stderr")
        with open(stdout_path, "wb") as out, open(stderr_path, "wb") as err:
            try:
                subprocess.run(_limit_wrapper(command, limit), check=True, stdout=out, stderr=err, timeout=timeout)
            except subprocess.CalledProcessError as exc:
                raise StepError(f"the bounded command failed with exit code {exc.returncode}") from None
            except subprocess.TimeoutExpired:
                raise StepError("the bounded command timed out") from None
        if os.path.getsize(stdout_path) > limit or os.path.getsize(stderr_path) > limit:
            raise StepError("the command output exceeded its cap")
        with open(stdout_path, "rb") as handle:
            return handle.read(limit)


def attest_verify(policy_path, subjects, bundle_out, bundle_paths=None, context=None):
    try:
        workflow_guard.require_mandate()
    except workflow_guard.Refused as exc:
        raise StepError(f"no registered test mandate: {exc}") from None
    with open(policy_path, "rb") as handle:
        policy = canon.loads(handle.read())
    require_pins(policy)
    attestation = policy.get("attestation", {})
    signer = attestation.get("signer_workflow")
    signer_ref = attestation.get("signer_ref")
    issuer = attestation.get("issuer")
    predicate = attestation.get("predicate_type")
    if not signer or not signer_ref or not issuer or not predicate:
        raise StepError("the attestation signer workflow/ref, issuer and predicate are not pinned")
    root_path = attestation.get("trusted_root_path")
    if not root_path:
        raise StepError("the trusted root path is not pinned")
    if _sha(_read(root_path, 4 * 1024 * 1024)) != attestation["trusted_root_sha256"]:
        raise StepError("the trusted root does not match its pinned hash")
    if context is None:
        context = {"repo": os.environ["GITHUB_REPOSITORY"],
                   "source_digest": os.environ["GITHUB_SHA"],
                   "run_id": str(os.environ["GITHUB_RUN_ID"]),
                   "run_attempt": str(os.environ["GITHUB_RUN_ATTEMPT"])}
    repo = context["repo"]
    run_id = str(context["run_id"])
    attempt = str(context["run_attempt"])
    source_digest = context["source_digest"]
    digests = {}
    chunks = []
    if bundle_paths is not None and len(bundle_paths) != len(subjects):
        raise StepError("the provided bundle list does not match the subject list")
    with tempfile.TemporaryDirectory() as temp:
        for index, subject in enumerate(subjects):
            if bundle_paths is not None:
                target = bundle_paths[index]
                data = _read_regular(target, 4 * 1024 * 1024)
            else:
                download_dir = os.path.join(temp, f"download-{index}")
                os.makedirs(download_dir)
                target, data = _download_bundle(subject, repo, predicate, download_dir)
            command = ["gh", "attestation", "verify", subject, "--repo", repo,
                       "--bundle", target, "--signer-workflow", signer, "--source-ref", signer_ref,
                       "--cert-oidc-issuer", issuer, "--source-digest", source_digest,
                       "--deny-self-hosted-runners", "--format", "json",
                       "--predicate-type", predicate, "--custom-trusted-root", root_path]
            output = _run_bounded(command, timeout=120, limit=4 * 1024 * 1024)
            after = _read_regular(target, 4 * 1024 * 1024)
            if after != data:
                raise StepError("the attestation bundle changed during verification")
            data = after
            payload = json.loads(output.decode("utf-8"))
            with open(subject, "rb") as handle:
                subject_sha = _sha(handle.read(limits.MAX_RAW_TAR_BYTES + 1))
            check_attestation_payload(payload, subject_name=os.path.basename(subject),
                                      subject_sha256=subject_sha, issuer=issuer, predicate=predicate,
                                      repo=repo, run_id=run_id, attempt=attempt,
                                      source_digest=source_digest)
            digests[os.path.basename(subject)] = _sha(data)
            chunks.append(data)
    bundle = b"".join(chunks)
    with open(bundle_out, "wb") as handle:
        handle.write(bundle)
    print(json.dumps({"bundles": digests, "bundle_sha256": _sha(bundle)}, sort_keys=True))
    return context


def main(argv=None):
    parser = argparse.ArgumentParser(prog="ci.steps")
    sub = parser.add_subparsers(dest="command", required=True)
    prep = sub.add_parser("prepare-inputs")
    prep.add_argument("--spec", required=True)
    prep.add_argument("--dest", required=True)
    raw = sub.add_parser("check-raw")
    raw.add_argument("--dir", required=True)
    wrapper = sub.add_parser("extract-wrapper")
    wrapper.add_argument("--zip", required=True)
    wrapper.add_argument("--dest", required=True)
    wrapper.add_argument("--allow", nargs="+", default=["raw.tar", "observation.json"])
    pack = sub.add_parser("pack-outputs")
    pack.add_argument("--raw", required=True)
    pack.add_argument("--out", required=True)
    run = sub.add_parser("write-run")
    run.add_argument("--out", required=True)
    tools = sub.add_parser("check-tools")
    tools.add_argument("--policy", default="ci/policy.json")
    att = sub.add_parser("attest-verify")
    att.add_argument("--policy", default="ci/policy.json")
    att.add_argument("--bundle-out", required=True)
    att.add_argument("subjects", nargs="+")
    args = parser.parse_args(argv)
    try:
        if args.command == "prepare-inputs":
            return prepare_inputs(args.spec, args.dest)
        if args.command == "check-raw":
            return check_raw(args.dir)
        if args.command == "extract-wrapper":
            return extract_wrapper(args.zip, args.dest, args.allow)
        if args.command == "pack-outputs":
            return pack_outputs(args.raw, args.out)
        if args.command == "write-run":
            return write_run(args.out)
        if args.command == "check-tools":
            return check_tools(args.policy)
        if args.command == "attest-verify":
            attest_verify(args.policy, args.subjects, args.bundle_out)
            return 0
    except (StepError, archive.ArchiveError, canon.CanonError, OSError, subprocess.CalledProcessError) as exc:
        print(f"ci.steps: {exc}", file=sys.stderr)
        return 1
    return 2


if __name__ == "__main__":
    sys.exit(main())
