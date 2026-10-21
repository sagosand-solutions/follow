# SPDX-License-Identifier: AGPL-3.0-or-later
"""Static tests for the disabled CI fixture boundary: canon, archives, schema, receipt, guard and workflow."""
import glob
import hashlib
import io
import json
import os
import re
import shutil
import threading
import tarfile
import tempfile
import unittest
import zipfile

from ci import archive, canon, limits, schema
from ci import verify as ci_verify
from ci import workflow_guard
from ci.run_fixture import docker_command
from ci.steps import check_attestation_payload, require_pins, run_invocation_pattern

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


ENV = {"TZ": "UTC", "LC_ALL": "C.UTF-8", "LANG": "C.UTF-8", "PATH": "/runtime/bin",
       "HOME": "/home", "TMPDIR": "/tmp", "HOSTNAME": "fixture"}


def h(text):
    return hashlib.sha256(text.encode()).hexdigest()


def policy(tmp):
    return {
        "policy_version": "ci-policy-v1",
        "schema_path": os.path.join(ROOT, "ci/schema/ci-v1.json"),
        "schema_sha256": schema_sha(),
        "licence_allowlist": ["CC-BY-SA-4.0"],
        "fixture_item_prefix": "git-pilot-1-",
        "env": dict(ENV),
        "cgroup_root": "/sys/fs/cgroup",
        "cgroup_parent_dir": "/sys/fs/cgroup/ci-fixture",
        "cgroup_name_prefix": "ci-fixture-",
        "tools": {"gh_version": "2.102.0",
                  "versions": {"git": "2.47.0", "python": "3.14.0", "pytest": "8.3.0"}},
        "limits": {"files": 64, "artifact_bytes": 4194304, "raw_tar_bytes": 4718592,
                   "observation_bytes": 65536, "zip_bytes": 8388608, "entries": 128,
                   "path_bytes": 4096, "total_path_bytes": 65536, "stdout_bytes": 65536,
                   "stderr_bytes": 65536, "compression_ratio": 100, "out_tmpfs_bytes": 8388608,
                   "out_tmpfs_inodes": 128, "wall_ms": 10000, "cpu_usec": 5000000,
                   "memory_bytes": 268435456},
        "image": {"ref": "ghcr.io/example/fixture@sha256:" + "1" * 64,
                  "image_id": "sha256:" + "2" * 64},
        "actions": {},
        "isolation": {
            "network_mode": "none",
            "readonly_rootfs": True,
            "cap_drop": ["ALL"],
            "security_opt": ["no-new-privileges"],
            "privileged": False,
            "user": "65534:65534",
            "mounts": [
                {"role": "input", "destination": "/input", "read_only": True},
                {"role": "out", "destination": "/out", "read_only": False},
            ],
            "env_allowlist": sorted(ENV),
            "memory": 268435456,
            "memory_swap": 268435456,
            "pids_limit": 64,
            "nano_cpus": 1000000000,
            "ulimits": [{"name": "nofile", "soft": 256, "hard": 256},
                        {"name": "nproc", "soft": 64, "hard": 64}],
        },
    }


def schema_sha():
    with open(os.path.join(ROOT, "ci/schema/ci-v1.json"), "rb") as handle:
        return hashlib.sha256(handle.read()).hexdigest()


def spec():
    files = {"a.txt": b"alpha\n", "b.txt": b"beta\n"}
    inputs = [
        {"role": "fixture", "path": "fixture.json", "sha256": h("fixture"), "bytes": len(b"fixture")},
        {"role": "script", "path": "script.py", "sha256": h("script"), "bytes": len(b"script")},
        {"role": "config", "path": "config.json", "sha256": h("config"), "bytes": len(b"config")},
    ]
    return {
        "spec_version": "ci-spec-v1",
        "fixture_id": "git-pilot-1",
        "batch_id": "git-pilot-1",
        "image_ref": "ghcr.io/example/fixture@sha256:" + "1" * 64,
        "argv": ["/runtime/bin/python3", "/input/script.py"],
        "inputs": inputs,
        "config": {"env": dict(ENV), "settings": {}},
        "items": [{"item_id": "git-pilot-1-fixture-1", "licence": {"license": "CC-BY-SA-4.0", "gate": "draft-only"}}],
        "licence_set": ["CC-BY-SA-4.0"],
        "expected_paths": ["a.txt", "b.txt"],
        "artifacts": {
            "a.txt": {"type": "file", "format": "text", "media": "text/plain", "encoding": "utf-8", "is_trace": False},
            "b.txt": {"type": "file", "format": "text", "media": "text/plain", "encoding": "utf-8", "is_trace": False},
        },
    }


def observation(files):
    obs = {
        "image_id": "sha256:" + "2" * 64,
        "image_ref_digest": "sha256:" + "1" * 64,
        "inputs": sorted(spec()["inputs"], key=lambda item: item["path"]),
        "inputs_sha256": ci_verify.inputspec_digest(spec()["inputs"]),
        "argv": spec()["argv"],
        "config_digest": canon.sha256_canonical(spec()["config"]),
        "exit_code": 0,
        "signal": None,
        "started_at": "2026-01-01T00:00:00Z",
        "ended_at": "2026-01-01T00:00:01Z",
        "wall_ms": 1000,
        "cpu_usec": 900000,
        "max_rss_bytes": 123456,
        "stdout_sha256": h("out"),
        "stderr_sha256": h("err"),
        "stdout_bytes": 3,
        "stderr_bytes": 3,
        "sealed_output_sha256": ci_verify.fileset_digest(files),
        "limit_hit": [],
    }
    obs["isolation"] = dict(policy(None)["isolation"])
    obs["isolation"]["env"] = dict(ENV)
    obs["isolation_sha256"] = canon.sha256_canonical(obs["isolation"])
    return obs


def run_doc():
    return {
        "run_id": 1,
        "run_attempt": 1,
        "workflow_sha": "a" * 40,
        "event": "workflow_dispatch",
        "ref": "refs/heads/main",
        "commit_sha": "b" * 40,
        "tree_sha": "c" * 40,
    }


def receipt_bundle():
    files = {"a.txt": b"alpha\n", "b.txt": b"beta\n"}
    outputs = archive.pack_tar(files)
    receipt = ci_verify.build_receipt(
        policy=policy(None), spec=spec(), run=run_doc(), observation=observation(files),
        raw_subjects=[{"name": "raw.tar", "sha256": h("raw")},
                      {"name": "observation.json", "sha256": h("obs")}],
        attestation_bundle_sha256=h("bundle"), outputs_tar=outputs,
        expected_paths=["a.txt", "b.txt"])
    return receipt, outputs


class CanonTests(unittest.TestCase):
    def test_sorted_keys_and_no_whitespace(self):
        self.assertEqual(canon.dumps({"b": 1, "a": 2}), b'{"a":2,"b":1}')

    def test_duplicate_keys_refused(self):
        with self.assertRaises(canon.CanonError):
            canon.loads(b'{"a":1,"a":2}')

    def test_floats_refused(self):
        with self.assertRaises(canon.CanonError):
            canon.loads(b'{"a":1.5}')
        with self.assertRaises(canon.CanonError):
            canon.dumps({"a": 1.5})

    def test_safe_integer_range(self):
        canon.dumps({"a": 2**53})
        with self.assertRaises(canon.CanonError):
            canon.dumps({"a": 2**53 + 1})

    def test_nfc_required(self):
        with self.assertRaises(canon.CanonError):
            canon.dumps({"a": "e\u0301"})

    def test_control_escape(self):
        self.assertEqual(canon.dumps({"a": "\x01"}), b'{"a":"\\u0001"}')


class ArchiveTests(unittest.TestCase):
    def test_tar_roundtrip(self):
        files = {"a.txt": b"alpha", "b.txt": b"beta"}
        data = archive.pack_tar(files)
        self.assertEqual(archive.parse_tar(data), files)

    def test_tar_rejects_traversal(self):
        buffer = io.BytesIO()
        with tarfile.open(fileobj=buffer, mode="w:") as archive_file:
            info = tarfile.TarInfo("../evil")
            info.size = 1
            archive_file.addfile(info, io.BytesIO(b"x"))
        with self.assertRaises(archive.ArchiveError):
            archive.parse_tar(buffer.getvalue())

    def test_tar_rejects_symlink(self):
        buffer = io.BytesIO()
        with tarfile.open(fileobj=buffer, mode="w:") as archive_file:
            info = tarfile.TarInfo("link")
            info.type = tarfile.SYMTYPE
            info.linkname = "/etc/passwd"
            archive_file.addfile(info)
        with self.assertRaises(archive.ArchiveError):
            archive.parse_tar(buffer.getvalue())

    def test_tar_rejects_compressed(self):
        import gzip
        with self.assertRaises(archive.ArchiveError):
            archive.parse_tar(gzip.compress(archive.pack_tar({"a": b"x"})))

    def test_tar_rejects_pax(self):
        buffer = io.BytesIO()
        with tarfile.open(fileobj=buffer, mode="w:", format=tarfile.PAX_FORMAT) as archive_file:
            info = tarfile.TarInfo("å.txt")
            info.size = 1
            archive_file.addfile(info, io.BytesIO(b"x"))
        with self.assertRaises(archive.ArchiveError):
            archive.parse_tar(buffer.getvalue())

    def test_tar_rejects_gnu_longname(self):
        buffer = io.BytesIO()
        with tarfile.open(fileobj=buffer, mode="w:", format=tarfile.GNU_FORMAT) as archive_file:
            info = tarfile.TarInfo("x" * 150)
            info.size = 1
            archive_file.addfile(info, io.BytesIO(b"x"))
        with self.assertRaises(archive.ArchiveError):
            archive.parse_tar(buffer.getvalue())

    def test_tar_rejects_hardlink(self):
        buffer = io.BytesIO()
        with tarfile.open(fileobj=buffer, mode="w:") as archive_file:
            info = tarfile.TarInfo("link")
            info.type = tarfile.LNKTYPE
            info.linkname = "target"
            archive_file.addfile(info)
        with self.assertRaises(archive.ArchiveError):
            archive.parse_tar(buffer.getvalue())

    def test_walk_out_rejects_hardlink(self):
        with tempfile.TemporaryDirectory() as temp:
            with open(os.path.join(temp, "a"), "w") as handle:
                handle.write("x")
            os.link(os.path.join(temp, "a"), os.path.join(temp, "b"))
            with self.assertRaises(archive.ArchiveError):
                archive.walk_out(temp)

    def test_prepare_inputs_rejects_traversal(self):
        from ci.steps import prepare_inputs
        with tempfile.TemporaryDirectory() as temp:
            value = spec()
            value["inputs"][0]["path"] = "../escape.json"
            spec_path = os.path.join(temp, "spec.json")
            with open(spec_path, "w") as handle:
                json.dump(value, handle)
            dest = os.path.join(temp, "dest")
            with self.assertRaises(Exception):
                prepare_inputs(spec_path, dest)

    def test_zip_exact_entries(self):
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as archive_file:
            archive_file.writestr("raw.tar", b"x")
            archive_file.writestr("observation.json", b"{}")
        files = archive.parse_zip(buffer.getvalue(), allowed_names={"raw.tar", "observation.json"})
        self.assertEqual(set(files), {"raw.tar", "observation.json"})

    def test_zip_rejects_extra_entry(self):
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as archive_file:
            archive_file.writestr("raw.tar", b"x")
            archive_file.writestr("observation.json", b"{}")
            archive_file.writestr("extra", b"x")
        with self.assertRaises(archive.ArchiveError):
            archive.parse_zip(buffer.getvalue(), allowed_names={"raw.tar", "observation.json"})

    def test_walk_out_rejects_symlink(self):
        with tempfile.TemporaryDirectory() as temp:
            with open(os.path.join(temp, "ok.txt"), "w") as handle:
                handle.write("ok")
            os.symlink("ok.txt", os.path.join(temp, "link"))
            with self.assertRaises(archive.ArchiveError):
                archive.walk_out(temp)


class SchemaTests(unittest.TestCase):
    def test_receipt_validates(self):
        receipt, _ = receipt_bundle()
        schema.validate(receipt, schema.load_schema(os.path.join(ROOT, "ci/schema/ci-v1.json")))

    def test_missing_key_fails(self):
        receipt, _ = receipt_bundle()
        del receipt["licence_set"]
        with self.assertRaises(schema.SchemaError):
            schema.validate(receipt, schema.load_schema(os.path.join(ROOT, "ci/schema/ci-v1.json")))

    def test_wrong_const_fails(self):
        receipt, _ = receipt_bundle()
        receipt["profile"] = "local-v1.5"
        with self.assertRaises(schema.SchemaError):
            schema.validate(receipt, schema.load_schema(os.path.join(ROOT, "ci/schema/ci-v1.json")))


class ReceiptTests(unittest.TestCase):
    def test_roundtrip(self):
        receipt, outputs = receipt_bundle()
        ci_verify.verify_receipt(receipt, policy=policy(None), spec=spec(), outputs_tar=outputs, observation=observation({"a.txt": b"alpha\n", "b.txt": b"beta\n"}))

    def test_artifact_hash_tamper(self):
        receipt, outputs = receipt_bundle()
        receipt["artifacts"][0]["sha256"] = "0" * 64
        with self.assertRaises(ci_verify.ReceiptError):
            ci_verify.verify_receipt(receipt, policy=policy(None), spec=spec(), outputs_tar=outputs, observation=observation({"a.txt": b"alpha\n", "b.txt": b"beta\n"}))

    def test_mixed_licence(self):
        receipt, outputs = receipt_bundle()
        receipt["items"][0]["licence"]["license"] = "MIT"
        with self.assertRaises(ci_verify.ReceiptError):
            ci_verify.verify_receipt(receipt, policy=policy(None), spec=spec(), outputs_tar=outputs, observation=observation({"a.txt": b"alpha\n", "b.txt": b"beta\n"}))

    def test_limit_hit(self):
        receipt, outputs = receipt_bundle()
        receipt["limits"]["limit_hit"] = ["stdout"]
        with self.assertRaises(ci_verify.ReceiptError):
            ci_verify.verify_receipt(receipt, policy=policy(None), spec=spec(), outputs_tar=outputs, observation=observation({"a.txt": b"alpha\n", "b.txt": b"beta\n"}))

    def test_text_is_never_trace(self):
        with self.assertRaises(ci_verify.ReceiptError):
            ci_verify.build_artifacts({"a.txt": b"x"}, {"a.txt": {"type": "file", "format": "text",
                                                                 "media": "text/plain", "encoding": "utf-8",
                                                                 "is_trace": True}})

    def test_scope_is_fixture_only(self):
        receipt, outputs = receipt_bundle()
        receipt["scope"] = "course-questions"
        with self.assertRaises((ci_verify.ReceiptError, schema.SchemaError)):
            ci_verify.verify_receipt(receipt, policy=policy(None), spec=spec(), outputs_tar=outputs, observation=observation({"a.txt": b"alpha\n", "b.txt": b"beta\n"}))

    def test_item_id_must_be_fixture_scoped(self):
        receipt, outputs = receipt_bundle()
        receipt["items"][0]["item_id"] = "course.question-1"
        with self.assertRaises(ci_verify.ReceiptError):
            ci_verify.verify_receipt(receipt, policy=policy(None), spec=spec(), outputs_tar=outputs, observation=observation({"a.txt": b"alpha\n", "b.txt": b"beta\n"}))

    def test_outputs_tar_mismatch(self):
        receipt, _ = receipt_bundle()
        other = archive.pack_tar({"a.txt": b"changed", "b.txt": b"beta\n"})
        with self.assertRaises(ci_verify.ReceiptError):
            ci_verify.verify_receipt(receipt, policy=policy(None), spec=spec(), outputs_tar=other, observation=observation({"a.txt": b"alpha\n", "b.txt": b"beta\n"}))


class GuardTests(unittest.TestCase):
    def test_guard_pin_and_mandate_are_consistent(self):
        # Either no mandate is registered (pin None, mandate disabled, guard refuses) or exactly one is: the pin is the
        # sha256 of the enabled mandate, which names only the first smoke-test spec and has an expiry.
        with open(os.path.join(ROOT, "ci/MANDATE.json"), "rb") as handle:
            raw = handle.read()
        mandate = json.loads(raw)
        if workflow_guard.PINNED_MANDATE_SHA256 is None:
            self.assertFalse(mandate["enabled"])
            with self.assertRaises(workflow_guard.Refused):
                workflow_guard.check(ROOT)
            return
        self.assertEqual(workflow_guard.PINNED_MANDATE_SHA256, hashlib.sha256(raw).hexdigest())
        self.assertIs(mandate["enabled"], True)
        self.assertEqual(mandate["allowed_specs"], ["ci/specs/git-pilot-1.json"])
        self.assertRegex(mandate["not_after"], r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")

    def test_guard_refuses_a_changed_mandate(self):
        if workflow_guard.PINNED_MANDATE_SHA256 is None:
            self.skipTest("no mandate is registered")
        with tempfile.TemporaryDirectory() as root:
            shutil.copytree(os.path.join(ROOT, "ci"), os.path.join(root, "ci"))
            path = os.path.join(root, "ci/MANDATE.json")
            with open(path, "rb") as handle:
                mandate = json.loads(handle.read())
            mandate["allowed_specs"].append("ci/specs/other.json")
            with open(path, "w") as handle:
                json.dump(mandate, handle)
            with self.assertRaises(workflow_guard.Refused):
                workflow_guard.check(root)


class AttestationStructureTests(unittest.TestCase):
    def payload(self, invocation="https://github.com/o/r/actions/runs/7/attempts/1", issuer="issuer",
                predicate="predicate", digest="a" * 64, name="raw.tar"):
        return [{"verificationResult": {
            "signature": {"certificate": {"issuer": issuer,
                                          "sourceRepositoryURI": "https://github.com/o/r",
                                          "sourceRepositoryDigest": "b" * 40}},
            "statement": {"predicateType": predicate,
                          "subject": [{"name": name, "digest": {"sha256": digest}}],
                          "predicate": {"runDetails": {"metadata": {"invocationId": invocation}}}}}}]

    def test_old_entry_is_ignored(self):
        payload = self.payload()
        old = self.payload("https://github.com/o/r/actions/runs/6/attempts/1")
        check_attestation_payload(old + payload, subject_name="raw.tar", subject_sha256="a" * 64,
                                  issuer="issuer", predicate="predicate", repo="o/r", run_id="7",
                                  attempt="1", source_digest="b" * 40)

    def test_two_entries_for_the_same_run_are_rejected(self):
        payload = self.payload() + self.payload()
        with self.assertRaises(Exception):
            check_attestation_payload(payload, subject_name="raw.tar", subject_sha256="a" * 64,
                                      issuer="issuer", predicate="predicate", repo="o/r", run_id="7",
                                      attempt="1", source_digest="b" * 40)

    def test_exact_fields(self):
        check_attestation_payload(self.payload(), subject_name="raw.tar", subject_sha256="a" * 64,
                                  issuer="issuer", predicate="predicate", repo="o/r", run_id="7",
                                  attempt="1", source_digest="b" * 40)

    def test_attempt_substring_is_not_a_match(self):
        with self.assertRaises(Exception):
            check_attestation_payload(self.payload("https://github.com/o/r/actions/runs/7/attempts/10"),
                                      subject_name="raw.tar", subject_sha256="a" * 64,
                                      issuer="issuer", predicate="predicate", repo="o/r", run_id="7",
                                      attempt="1", source_digest="b" * 40)

    def test_missing_issuer_is_rejected(self):
        payload = self.payload()
        payload[0]["verificationResult"]["signature"]["certificate"] = {}
        with self.assertRaises(Exception):
            check_attestation_payload(payload, subject_name="raw.tar", subject_sha256="a" * 64,
                                      issuer="issuer", predicate="predicate", repo="o/r", run_id="7",
                                      attempt="1", source_digest="b" * 40)

    def test_frozen_example(self):
        with open(os.path.join(ROOT, "ci/fixtures/gh-attestation-v1.example.json"), "rb") as handle:
            payload = json.load(handle)
        check_attestation_payload(payload, subject_name="raw.tar", subject_sha256="1" * 64,
                                  issuer="https://token.actions.githubusercontent.com",
                                  predicate="https://slsa.dev/provenance/v1", repo="example/repo",
                                  run_id="1", attempt="1",
                                  source_digest="0123456789abcdef0123456789abcdef01234567")

    def test_old_certificate_path_does_not_authenticate(self):
        payload = self.payload()
        payload[0]["verificationResult"]["certificate"] = payload[0]["verificationResult"]["signature"].pop("certificate")
        with self.assertRaises(Exception):
            check_attestation_payload(payload, subject_name="raw.tar", subject_sha256="a" * 64,
                                      issuer="issuer", predicate="predicate", repo="o/r", run_id="7",
                                      attempt="1", source_digest="b" * 40)

    def test_nested_metadata_does_not_authenticate(self):
        payload = self.payload()
        payload[0]["verificationResult"]["statement"]["predicate"]["runDetails"]["metadata"]["invocationId"] = (
            "https://github.com/o/r/actions/runs/9/attempts/1")
        payload[0]["verificationResult"]["metadata"] = {
            "invocationId": "https://github.com/o/r/actions/runs/7/attempts/1"}
        with self.assertRaises(Exception):
            check_attestation_payload(payload, subject_name="raw.tar", subject_sha256="a" * 64,
                                      issuer="issuer", predicate="predicate", repo="o/r", run_id="7",
                                      attempt="1", source_digest="b" * 40)

    def test_wrong_subject_is_rejected(self):
        with self.assertRaises(Exception):
            check_attestation_payload(self.payload(name="other.tar"), subject_name="raw.tar",
                                      subject_sha256="a" * 64, issuer="issuer", predicate="predicate",
                                      repo="o/r", run_id="7", attempt="1", source_digest="b" * 40)


class PinSetTests(unittest.TestCase):
    def test_real_policy_pin_set_is_complete(self):
        import json as jsonlib
        from ci.workflow_guard import pin_set_missing
        with open(os.path.join(ROOT, "ci/policy.json"), "rb") as handle:
            loaded = jsonlib.load(handle)
        self.assertEqual(pin_set_missing(loaded), [])

    def test_complete_pin_set(self):
        from ci.workflow_guard import pin_set_missing
        value = {"schema_sha256": "c" * 64,
                 "image": {"ref": "ghcr.io/x/y@sha256:" + "1" * 64, "image_id": "sha256:" + "2" * 64},
                 "tools": {"gh_version": "2", "versions": {"git": "1", "python": "1", "pytest": "1"}},
                 "actions": {"checkout": "a" * 40, "upload": "b" * 40, "attest": "c" * 40},
                 "attestation": {"issuer": "i", "signer_workflow": "w", "signer_ref": "refs/heads/main",
                                 "predicate_type": "p",
                                 "trusted_root_path": "r", "trusted_root_sha256": "d" * 64,
                                 "gh_example_sha256": "e" * 64}}
        self.assertEqual(pin_set_missing(value), [])


class SignerFormatTests(unittest.TestCase):
    def test_real_signer_workflow_and_ref(self):
        import json as jsonlib
        with open(os.path.join(ROOT, "ci/policy.json"), "rb") as handle:
            loaded = jsonlib.load(handle)
        attestation = loaded["attestation"]
        self.assertIn(".github/workflows/", attestation["signer_workflow"])
        self.assertNotIn("@", attestation["signer_workflow"])
        self.assertTrue(attestation["signer_ref"].startswith("refs/"))


class PinsManifestTests(unittest.TestCase):
    def test_pins_manifest_matches_the_files(self):
        bootstrap = os.path.join(ROOT, "ci/pins.json")
        if not os.path.exists(bootstrap):
            self.skipTest("no pins manifest")
        with open(bootstrap, "rb") as handle:
            pins = json.load(handle)
        with open(os.path.join(ROOT, "ci/policy.json"), "rb") as handle:
            policy = json.load(handle)
        self.assertEqual(pins["policy_sha256"], hashlib.sha256(open(os.path.join(ROOT, "ci/policy.json"), "rb").read()).hexdigest())
        self.assertEqual(pins["schema_sha256"], hashlib.sha256(open(os.path.join(ROOT, "ci/schema/ci-v1.json"), "rb").read()).hexdigest())
        self.assertEqual(pins["spec_sha256"], hashlib.sha256(open(os.path.join(ROOT, "ci/specs/git-pilot-1.json"), "rb").read()).hexdigest())
        self.assertEqual(pins["trusted_root_sha256"], hashlib.sha256(open(os.path.join(ROOT, "ci/trust/trusted_root.jsonl"), "rb").read()).hexdigest())
        self.assertEqual(pins["image"]["index_digest"], policy["image"]["ref"].split("@", 1)[1])
        self.assertEqual(pins["image"]["manifest_amd64_digest"], policy["image"]["manifest_digest"])
        self.assertEqual(pins["image"]["config_digest"], policy["image"]["image_id"])
        self.assertEqual(pins["tools"], policy["tools"]["versions"])
        self.assertEqual(pins["actions"], policy["actions"])
        self.assertEqual(pins["signer_ref"], policy["attestation"]["signer_ref"])


class PrepareInputsNegativeTests(unittest.TestCase):
    def _spec(self, temp, inputs):
        spec_dir = os.path.join(temp, "case")
        os.makedirs(spec_dir, exist_ok=True)
        for item in inputs:
            data = item.get("content", b"x")
            parts = item["path"].split("/")
            unsafe = item["path"].startswith("/") or any(part in ("", ".", "..") for part in parts)
            if not unsafe:
                path = os.path.join(spec_dir, item["path"])
                os.makedirs(os.path.dirname(path) or spec_dir, exist_ok=True)
                with open(path, "wb") as handle:
                    handle.write(data)
            item["bytes"] = len(data)
            item["sha256"] = hashlib.sha256(data).hexdigest()
        spec = {"inputs": [{key: item[key] for key in ("role", "path", "sha256", "bytes")}
                           for item in inputs]}
        spec_path = os.path.join(temp, "case.json")
        with open(spec_path, "w") as handle:
            json.dump(spec, handle)
        return spec_path

    def test_content_deviation_is_rejected(self):
        from ci import steps
        with tempfile.TemporaryDirectory() as temp:
            spec_path = self._spec(temp, [{"role": "fixture", "path": "a.txt", "content": b"abc"}])
            source = os.path.join(temp, "case", "a.txt")
            with open(source, "wb") as handle:
                handle.write(b"abd")
            with self.assertRaisesRegex(steps.StepError, "reviewed hash"):
                steps.prepare_inputs(spec_path, os.path.join(temp, "dest"))

    def test_same_size_different_hash_is_rejected(self):
        from ci import steps
        with tempfile.TemporaryDirectory() as temp:
            spec_path = self._spec(temp, [{"role": "fixture", "path": "a.txt", "content": b"abc"}])
            with open(os.path.join(temp, "case", "a.txt"), "wb") as handle:
                handle.write(b"abd")
            with self.assertRaisesRegex(steps.StepError, "reviewed hash"):
                steps.prepare_inputs(spec_path, os.path.join(temp, "dest"))

    def test_unsafe_input_paths_are_rejected(self):
        from ci import steps
        for bad in ("../x", "/abs", "a//b", "./x", "a/../b"):
            with tempfile.TemporaryDirectory() as temp:
                spec_path = self._spec(temp, [{"role": "fixture", "path": bad, "content": b"x"}])
                with self.assertRaisesRegex(steps.StepError, "unsafe input path"):
                    steps.prepare_inputs(spec_path, os.path.join(temp, "dest"))

    def test_leaf_symlink_with_same_content_is_rejected(self):
        from ci import steps
        with tempfile.TemporaryDirectory() as temp:
            spec_path = self._spec(temp, [{"role": "fixture", "path": "a.txt", "content": b"abc"}])
            source = os.path.join(temp, "case", "a.txt")
            target = os.path.join(temp, "case", "real.txt")
            with open(target, "wb") as handle:
                handle.write(b"abc")
            os.unlink(source)
            os.symlink(target, source)
            with self.assertRaises(steps.StepError):
                steps.prepare_inputs(spec_path, os.path.join(temp, "dest"))

    def test_intermediate_symlink_is_rejected(self):
        from ci import steps
        with tempfile.TemporaryDirectory() as temp:
            spec_path = self._spec(temp, [{"role": "fixture", "path": "sub/x.txt", "content": b"x"}])
            case = os.path.join(temp, "case")
            real = os.path.join(case, "real")
            os.makedirs(real)
            with open(os.path.join(real, "x.txt"), "wb") as handle:
                handle.write(b"x")
            os.rename(os.path.join(case, "sub", "x.txt"), os.path.join(real, "x.txt"))
            os.rmdir(os.path.join(case, "sub"))
            os.symlink(real, os.path.join(case, "sub"))
            with self.assertRaises(steps.StepError):
                steps.prepare_inputs(spec_path, os.path.join(temp, "dest"))

    def test_spec_directory_symlink_is_rejected(self):
        from ci import steps
        with tempfile.TemporaryDirectory() as temp:
            real = os.path.join(temp, "real")
            os.makedirs(real)
            data = b"x"
            with open(os.path.join(real, "a.txt"), "wb") as handle:
                handle.write(data)
            os.symlink(real, os.path.join(temp, "case"))
            spec = {"inputs": [{"role": "fixture", "path": "a.txt",
                                "sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data)}]}
            spec_path = os.path.join(temp, "case.json")
            with open(spec_path, "w") as handle:
                json.dump(spec, handle)
            with self.assertRaises(steps.StepError):
                steps.prepare_inputs(spec_path, os.path.join(temp, "dest"))

    def test_directory_input_is_rejected(self):
        from ci import steps
        with tempfile.TemporaryDirectory() as temp:
            spec_path = self._spec(temp, [{"role": "fixture", "path": "a.txt", "content": b"x"}])
            source = os.path.join(temp, "case", "a.txt")
            os.unlink(source)
            os.makedirs(source)
            with self.assertRaisesRegex(steps.StepError, "not a regular file"):
                steps.prepare_inputs(spec_path, os.path.join(temp, "dest"))

    def test_oversized_input_is_rejected(self):
        from ci import limits, steps
        with tempfile.TemporaryDirectory() as temp:
            spec_path = self._spec(temp, [{"role": "fixture", "path": "a.txt",
                                           "content": b"x" * (limits.MAX_ARTIFACT_BYTES + 1)}])
            with self.assertRaisesRegex(steps.StepError, "exceeds"):
                steps.prepare_inputs(spec_path, os.path.join(temp, "dest"))

    def test_output_mode_is_0444(self):
        from ci import steps
        with tempfile.TemporaryDirectory() as temp:
            spec_path = self._spec(temp, [{"role": "fixture", "path": "a.txt", "content": b"x"}])
            dest = os.path.join(temp, "dest")
            steps.prepare_inputs(spec_path, dest)
            mode = os.stat(os.path.join(dest, "a.txt")).st_mode & 0o777
            self.assertEqual(mode, 0o444)

    def test_nonempty_dest_is_rejected(self):
        from ci import steps
        with tempfile.TemporaryDirectory() as temp:
            spec_path = self._spec(temp, [{"role": "fixture", "path": "a.txt", "content": b"x"}])
            dest = os.path.join(temp, "dest")
            os.makedirs(dest)
            with open(os.path.join(dest, "existing"), "w") as handle:
                handle.write("x")
            with self.assertRaisesRegex(steps.StepError, "must be empty"):
                steps.prepare_inputs(spec_path, dest)

    def test_missing_file_is_rejected(self):
        from ci import steps
        with tempfile.TemporaryDirectory() as temp:
            spec_path = self._spec(temp, [{"role": "fixture", "path": "a.txt", "content": b"x"}])
            os.unlink(os.path.join(temp, "case", "a.txt"))
            with self.assertRaises(steps.StepError):
                steps.prepare_inputs(spec_path, os.path.join(temp, "dest"))

    def test_nested_input_gets_its_parent(self):
        from ci import steps
        with tempfile.TemporaryDirectory() as temp:
            spec_path = self._spec(temp, [{"role": "fixture", "path": "sub/x.txt", "content": b"x"}])
            dest = os.path.join(temp, "dest")
            steps.prepare_inputs(spec_path, dest)
            self.assertTrue(os.path.isfile(os.path.join(dest, "sub", "x.txt")))

    def test_spec_without_its_directory_is_rejected(self):
        from ci import steps
        with tempfile.TemporaryDirectory() as temp:
            spec_path = os.path.join(temp, "orphan.json")
            with open(spec_path, "w") as handle:
                json.dump({"inputs": []}, handle)
            with self.assertRaisesRegex(steps.StepError, "spec input directory is missing"):
                steps.prepare_inputs(spec_path, os.path.join(temp, "dest"))


class ReadBeneathTests(unittest.TestCase):
    def test_leaf_symlink(self):
        from ci.steps import _read_beneath
        import ci.steps as steps
        with tempfile.TemporaryDirectory() as temp:
            with open(os.path.join(temp, "real"), "wb") as handle:
                handle.write(b"x")
            os.symlink("real", os.path.join(temp, "link"))
            with self.assertRaises((OSError, steps.StepError)):
                _read_beneath(temp, "link", 10)

    def test_root_symlink(self):
        from ci.steps import _read_beneath
        import ci.steps as steps
        with tempfile.TemporaryDirectory() as temp:
            real = os.path.join(temp, "real")
            os.makedirs(real)
            with open(os.path.join(real, "a"), "wb") as handle:
                handle.write(b"x")
            link = os.path.join(temp, "link")
            os.symlink(real, link)
            with self.assertRaises((OSError, steps.StepError)):
                _read_beneath(link, "a", 10)

    def test_intermediate_symlink(self):
        from ci.steps import _read_beneath
        import ci.steps as steps
        with tempfile.TemporaryDirectory() as temp:
            real = os.path.join(temp, "real")
            os.makedirs(real)
            with open(os.path.join(real, "a"), "wb") as handle:
                handle.write(b"x")
            os.symlink(real, os.path.join(temp, "sub"))
            with self.assertRaises((OSError, steps.StepError)):
                _read_beneath(temp, "sub/a", 10)

    def test_directory_and_fifo(self):
        from ci.steps import _read_beneath
        import ci.steps as steps
        with tempfile.TemporaryDirectory() as temp:
            os.makedirs(os.path.join(temp, "dir"))
            with self.assertRaisesRegex(steps.StepError, "not a regular file"):
                _read_beneath(temp, "dir", 10)
            os.mkfifo(os.path.join(temp, "fifo"))
            outcome = {}
            def call():
                try:
                    _read_beneath(temp, "fifo", 10)
                except BaseException as exc:
                    outcome["error"] = exc
            thread = threading.Thread(target=call, daemon=True)
            thread.start()
            thread.join(2)
            self.assertFalse(thread.is_alive(), "FIFO open blocked without O_NONBLOCK")
            self.assertIn("error", outcome)
            self.assertRegex(str(outcome["error"]), "not a regular file")

    def test_write_new_refuses_an_existing_path(self):
        from ci.steps import _write_new
        with tempfile.TemporaryDirectory() as temp:
            path = os.path.join(temp, "a")
            _write_new(path, b"x")
            with self.assertRaises(FileExistsError):
                _write_new(path, b"y")


class RealSpecInputTests(unittest.TestCase):
    def test_prepare_inputs_on_every_real_spec(self):
        from ci.steps import prepare_inputs
        specs = sorted(glob.glob(os.path.join(ROOT, "ci/specs/*.json")))
        self.assertTrue(specs)
        for spec_path in specs:
            with tempfile.TemporaryDirectory() as temp:
                dest = os.path.join(temp, "input")
                prepare_inputs(spec_path, dest)
                with open(spec_path, "rb") as handle:
                    spec = json.load(handle)
                for item in spec["inputs"]:
                    path = os.path.join(dest, item["path"])
                    self.assertTrue(os.path.isfile(path))
                    with open(path, "rb") as handle:
                        data = handle.read()
                    self.assertEqual(len(data), item["bytes"])
                    self.assertEqual(hashlib.sha256(data).hexdigest(), item["sha256"])


class ToolCheckTests(unittest.TestCase):
    def test_check_tools_refuses_without_process(self):
        from unittest import mock
        from ci import steps, workflow_guard
        with mock.patch.object(workflow_guard, "require_mandate", side_effect=workflow_guard.Refused("no")), \
             mock.patch("ci.steps.subprocess.run") as run_mock:
            with self.assertRaises(steps.StepError):
                steps.check_tools("ci/policy.json")
            run_mock.assert_not_called()

    def test_write_run_refuses_without_process(self):
        from unittest import mock
        from ci import steps, workflow_guard
        with mock.patch.object(workflow_guard, "require_mandate", side_effect=workflow_guard.Refused("no")), \
             mock.patch("ci.steps.subprocess.run") as run_mock:
            with self.assertRaises(steps.StepError):
                steps.write_run("/tmp/opencode/run.json")
            run_mock.assert_not_called()

    def test_run_fixture_cli_refuses_without_process(self):
        from unittest import mock
        from ci import run_fixture, workflow_guard
        with mock.patch.object(workflow_guard, "require_mandate", side_effect=workflow_guard.Refused("no")), \
             mock.patch("ci.run_fixture.subprocess.run") as run_mock, \
             mock.patch("ci.run_fixture.subprocess.Popen") as popen_mock:
            code = run_fixture.main(["--spec", "ci/specs/git-pilot-1.json", "--input", "/input",
                                     "--out", "/out", "--raw", "/tmp/opencode/raw.tar",
                                     "--observation", "/tmp/opencode/observation.json"])
            self.assertNotEqual(code, 0)
            run_mock.assert_not_called()
            popen_mock.assert_not_called()

    def test_fetch_cli_refuses_without_network(self):
        from unittest import mock
        from ci import fetch_artifact, workflow_guard
        with mock.patch.object(workflow_guard, "require_mandate", side_effect=workflow_guard.Refused("no")), \
             mock.patch("ci.fetch_artifact.urllib.request.urlopen") as open_mock, \
             mock.patch.dict(os.environ, {"GH_TOKEN": "x"}):
            code = fetch_artifact.main(["--name", "n", "--out", "/tmp/opencode/o.zip"])
            self.assertNotEqual(code, 0)
            open_mock.assert_not_called()


class RealPolicyShapeTests(unittest.TestCase):
    def test_check_isolation_reads_top_level_env(self):
        import json as jsonlib
        from ci.verify import check_isolation
        with open(os.path.join(ROOT, "ci/policy.json"), "rb") as handle:
            loaded = jsonlib.load(handle)
        observed = dict(loaded["isolation"])
        observed["env"] = dict(loaded["env"])
        check_isolation(observed, loaded)
        observed["env"]["HOME"] = "/elsewhere"
        with self.assertRaises(Exception):
            check_isolation(observed, loaded)


class CounterTests(unittest.TestCase):
    def test_final_counters(self):
        from ci.run_fixture import _read_final_counters
        with tempfile.TemporaryDirectory() as temp:
            with open(os.path.join(temp, "cgroup.events"), "w") as handle:
                handle.write("populated 0\n")
            with open(os.path.join(temp, "cpu.stat"), "w") as handle:
                handle.write("usage_usec 123\n")
            with open(os.path.join(temp, "memory.peak"), "w") as handle:
                handle.write("456\n")
            self.assertEqual(_read_final_counters(temp), (123, 456))

    def test_cgroup_driver_must_be_cgroupfs(self):
        from ci.run_fixture import _require_cgroupfs
        _require_cgroupfs("cgroupfs")
        with self.assertRaises(Exception):
            _require_cgroupfs("systemd")

    def test_cgroup_parent_argument_is_a_name(self):
        command = docker_command(spec(), policy(None), "/input", "/out", "name",
                                 cgroup_parent="ci-fixture-123")
        self.assertIn("--cgroup-parent", command)
        self.assertEqual(command[command.index("--cgroup-parent") + 1], "ci-fixture-123")

    def test_populated_cgroup_is_rejected(self):
        from ci.run_fixture import _read_final_counters
        with tempfile.TemporaryDirectory() as temp:
            with open(os.path.join(temp, "cgroup.events"), "w") as handle:
                handle.write("populated 1\n")
            with self.assertRaises(Exception):
                _read_final_counters(temp)

    def test_missing_peak_is_rejected(self):
        from ci.run_fixture import _read_final_counters
        with tempfile.TemporaryDirectory() as temp:
            with open(os.path.join(temp, "cgroup.events"), "w") as handle:
                handle.write("populated 0\n")
            with open(os.path.join(temp, "cpu.stat"), "w") as handle:
                handle.write("usage_usec 123\n")
            with self.assertRaises(Exception):
                _read_final_counters(temp)


class ConsumerTests(unittest.TestCase):
    def test_attested_a_but_consumed_b_is_rejected(self):
        from unittest import mock
        from ci import consume, steps, workflow_guard
        receipt, outputs_a = receipt_bundle()
        files_b = {"a.txt": b"different\n", "b.txt": b"beta\n"}
        outputs_b = archive.pack_tar(files_b)
        context = {"repo": "o/r", "source_digest": run_doc()["commit_sha"],
                   "run_id": "1", "run_attempt": "1"}
        with tempfile.TemporaryDirectory() as temp:
            spec_path = os.path.join(temp, "spec.json")
            policy_path = os.path.join(temp, "policy.json")
            with open(spec_path, "w") as handle:
                json.dump(spec(), handle)
            with open(policy_path, "w") as handle:
                json.dump(policy(None), handle)
            raw = os.path.join(temp, "raw.tar")
            obs = os.path.join(temp, "observation.json")
            out = os.path.join(temp, "outputs.tar")
            rec = os.path.join(temp, "receipt.json")
            sib = os.path.join(temp, "receipt.sha256")
            run = os.path.join(temp, "run.json")
            observation_doc = observation({"a.txt": b"alpha\n", "b.txt": b"beta\n"})
            with open(raw, "wb") as handle:
                handle.write(b"raw-bytes")
            with open(obs, "wb") as handle:
                handle.write(canon.dumps(observation_doc))
            with open(out, "wb") as handle:
                handle.write(outputs_b)
            receipt["provenance"]["raw"]["subjects"] = [
                {"name": "raw.tar", "sha256": hashlib.sha256(b"raw-bytes").hexdigest()},
                {"name": "observation.json", "sha256": hashlib.sha256(canon.dumps(observation_doc)).hexdigest()},
            ]
            data = canon.dumps(receipt)
            with open(rec, "wb") as handle:
                handle.write(data)
            with open(sib, "w") as handle:
                handle.write(hashlib.sha256(data).hexdigest() + "\n")
            with open(run, "wb") as handle:
                handle.write(canon.dumps(run_doc()))
            bundle = os.path.join(temp, "bundle.bin")
            bundle_bytes = b"bundle-bytes"
            with open(bundle, "wb") as handle:
                handle.write(bundle_bytes)
            receipt["provenance"]["raw"]["attestation_bundle_sha256"] = hashlib.sha256(bundle_bytes).hexdigest()
            with mock.patch.object(workflow_guard, "require_mandate", return_value=True), \
                 mock.patch.object(steps, "attest_verify", return_value=0) as attest_mock, \
                 mock.patch.dict(os.environ, {"GH_TOKEN": "x"}):
                with self.assertRaises(consume.ConsumeError):
                    consume.consume(policy_path=policy_path, spec_path=spec_path,
                                    observation_path=obs, outputs_path=out, receipt_path=rec,
                                    sibling_path=sib, run_path=run,
                                    raw_subjects=[raw], receipt_subjects=[out, rec],
                                    bundle_dir=os.path.join(temp, "bundles"), raw_bundle_path=bundle,
                                    context=context)

    def test_trusted_context_a_with_receipt_b_is_rejected(self):
        from unittest import mock
        from ci import consume, steps, workflow_guard
        receipt, outputs = receipt_bundle()
        run_b = dict(run_doc())
        run_b["run_id"] = 2
        receipt["run"] = run_b
        observation_doc = observation({"a.txt": b"alpha\n", "b.txt": b"beta\n"})
        observation_bytes = canon.dumps(observation_doc)
        raw_bytes = b"raw-bytes"
        receipt["provenance"]["raw"]["subjects"] = [
            {"name": "raw.tar", "sha256": hashlib.sha256(raw_bytes).hexdigest()},
            {"name": "observation.json", "sha256": hashlib.sha256(observation_bytes).hexdigest()},
        ]
        context_a = {"repo": "o/r", "source_digest": run_doc()["commit_sha"],
                     "run_id": "1", "run_attempt": "1"}
        with tempfile.TemporaryDirectory() as temp:
            spec_path = os.path.join(temp, "spec.json")
            policy_path = os.path.join(temp, "policy.json")
            with open(spec_path, "w") as handle:
                json.dump(spec(), handle)
            with open(policy_path, "w") as handle:
                json.dump(policy(None), handle)
            raw = os.path.join(temp, "raw.tar")
            obs = os.path.join(temp, "observation.json")
            out = os.path.join(temp, "outputs.tar")
            rec = os.path.join(temp, "receipt.json")
            sib = os.path.join(temp, "receipt.sha256")
            run = os.path.join(temp, "run.json")
            bundle = os.path.join(temp, "bundle.bin")
            bundle_bytes = b"bundle-bytes"
            receipt["provenance"]["raw"]["attestation_bundle_sha256"] = hashlib.sha256(bundle_bytes).hexdigest()
            data = canon.dumps(receipt)
            for path, payload in ((raw, raw_bytes), (obs, observation_bytes), (out, outputs),
                                  (rec, data), (bundle, bundle_bytes), (run, canon.dumps(run_b))):
                with open(path, "wb") as handle:
                    handle.write(payload)
            with open(sib, "w") as handle:
                handle.write(hashlib.sha256(data).hexdigest() + "\n")
            with mock.patch.object(workflow_guard, "require_mandate", return_value=True), \
                 mock.patch.object(steps, "attest_verify", return_value=context_a), \
                 mock.patch.dict(os.environ, {"GH_TOKEN": "x"}):
                with self.assertRaises(consume.ConsumeError):
                    consume.consume(policy_path=policy_path, spec_path=spec_path,
                                    observation_path=obs, outputs_path=out, receipt_path=rec,
                                    sibling_path=sib, run_path=run, raw_subjects=[raw, obs],
                                    receipt_subjects=[out, rec], bundle_dir=os.path.join(temp, "bundles"),
                                    raw_bundle_path=bundle, context=context_a)

    def test_consumer_uses_a_copy_of_the_bundle_snapshot(self):
        from unittest import mock
        from ci import consume, steps, workflow_guard
        receipt, outputs = receipt_bundle()
        observation_doc = observation({"a.txt": b"alpha\n", "b.txt": b"beta\n"})
        observation_bytes = canon.dumps(observation_doc)
        raw_bytes = b"raw-bytes"
        receipt["provenance"]["raw"]["subjects"] = [
            {"name": "raw.tar", "sha256": hashlib.sha256(raw_bytes).hexdigest()},
            {"name": "observation.json", "sha256": hashlib.sha256(observation_bytes).hexdigest()},
        ]
        context = {"repo": "o/r", "source_digest": run_doc()["commit_sha"],
                   "run_id": "1", "run_attempt": "1"}
        with tempfile.TemporaryDirectory() as temp:
            spec_path = os.path.join(temp, "spec.json")
            policy_path = os.path.join(temp, "policy.json")
            with open(spec_path, "w") as handle:
                json.dump(spec(), handle)
            with open(policy_path, "w") as handle:
                json.dump(policy(None), handle)
            raw = os.path.join(temp, "raw.tar")
            obs = os.path.join(temp, "observation.json")
            out = os.path.join(temp, "outputs.tar")
            rec = os.path.join(temp, "receipt.json")
            sib = os.path.join(temp, "receipt.sha256")
            run = os.path.join(temp, "run.json")
            bundle = os.path.join(temp, "bundle.bin")
            bundle_bytes = b"bundle-bytes"
            receipt["provenance"]["raw"]["attestation_bundle_sha256"] = hashlib.sha256(bundle_bytes).hexdigest()
            data = canon.dumps(receipt)
            for path, payload in ((raw, raw_bytes), (obs, observation_bytes), (out, outputs),
                                  (rec, data), (bundle, bundle_bytes)):
                with open(path, "wb") as handle:
                    handle.write(payload)
            with open(sib, "w") as handle:
                handle.write(hashlib.sha256(data).hexdigest() + "\n")
            with open(run, "wb") as handle:
                handle.write(canon.dumps(run_doc()))
            with mock.patch.object(workflow_guard, "require_mandate", return_value=True), \
                 mock.patch.object(steps, "attest_verify", return_value=0) as attest_mock, \
                 mock.patch.dict(os.environ, {"GH_TOKEN": "x"}):
                consume.consume(policy_path=policy_path, spec_path=spec_path,
                                observation_path=obs, outputs_path=out, receipt_path=rec,
                                sibling_path=sib, run_path=run, raw_subjects=[raw, obs],
                                receipt_subjects=[out, rec], bundle_dir=os.path.join(temp, "bundles"),
                                raw_bundle_path=bundle, context=context)
                passed = attest_mock.call_args_list[0].kwargs.get("bundle_paths")
                self.assertTrue(passed)
                self.assertNotEqual(passed[0], bundle)
                self.assertEqual(os.path.basename(passed[0]), "bundle.bin")

    def test_consumer_requires_pins_before_any_subprocess(self):
        from unittest import mock
        from ci import consume
        with mock.patch("ci.steps.subprocess.run") as run_mock:
            with self.assertRaises(Exception):
                consume.consume(policy_path="ci/policy.json", spec_path="ci/specs/git-pilot-1.json",
                                observation_path="observation.json", outputs_path="outputs.tar",
                                receipt_path="receipt.json", sibling_path="receipt.sha256",
                                raw_subjects=["raw.tar"], receipt_subjects=["outputs.tar"],
                                bundle_dir="/tmp/opencode/bundles", run_path="run.json",
                                raw_bundle_path=None)
            run_mock.assert_not_called()


class EntryPointGuardTests(unittest.TestCase):
    # Each launch/network entry point must consult the guard before any process or socket. The guard is forced to refuse
    # here so the tests hold whether or not a mandate is currently registered.
    def setUp(self):
        from unittest import mock
        patcher = mock.patch.object(workflow_guard, "require_mandate",
                                    side_effect=workflow_guard.Refused("no registered test mandate (test)"))
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_fetch_refuses_without_process_or_socket(self):
        from unittest import mock
        from ci import fetch_artifact
        with mock.patch("ci.fetch_artifact.urllib.request.urlopen") as open_mock:
            with self.assertRaises(fetch_artifact.FetchError):
                fetch_artifact.fetch("o/r", "1", "name", "token", "/tmp/opencode/out.zip", 1024)
            open_mock.assert_not_called()

    def test_attest_verify_refuses_without_subprocess(self):
        from unittest import mock
        from ci import steps
        with mock.patch("ci.steps.subprocess.run") as run_mock:
            with self.assertRaises(steps.StepError):
                steps.attest_verify("ci/policy.json", ["raw.tar"], "/tmp/opencode/bundle")
            run_mock.assert_not_called()

    def test_consume_refuses_without_subprocess(self):
        from unittest import mock
        from ci import consume
        with mock.patch("ci.steps.subprocess.run") as run_mock:
            with self.assertRaises(consume.ConsumeError):
                consume.consume(policy_path="ci/policy.json", spec_path="ci/specs/git-pilot-1.json",
                                observation_path="observation.json", outputs_path="outputs.tar",
                                receipt_path="receipt.json", sibling_path="receipt.sha256",
                                raw_subjects=["raw.tar"], receipt_subjects=["outputs.tar"],
                                bundle_dir="/tmp/opencode/bundles", run_path="run.json",
                                raw_bundle_path=None)
            run_mock.assert_not_called()


class CleanupTests(unittest.TestCase):
    def test_cleanup_runs_when_the_inner_run_fails(self):
        from unittest import mock
        from ci import run_fixture, workflow_guard
        with mock.patch.object(workflow_guard, "require_mandate", return_value=True), \
             mock.patch("ci.run_fixture._create_cgroup", return_value=("ci-fixture-test", "/tmp/opencode/fake-cgroup")), \
             mock.patch("ci.run_fixture.os.rmdir", return_value=None), \
             mock.patch("ci.run_fixture._run_inner", side_effect=run_fixture.RunError("boom")), \
             mock.patch("ci.run_fixture.subprocess.run") as run_mock:
            with self.assertRaises(run_fixture.RunError):
                run_fixture._run(spec(), policy(None), "/input", "/out")
            commands = [call.args[0] for call in run_mock.call_args_list]
            self.assertTrue(any(command[:3] == ["docker", "rm", "-f"] for command in commands))
            self.assertTrue(any(command[:2] == ["docker", "kill"] for command in commands))

    def test_cgroup_is_sampled_before_removal(self):
        with open(os.path.join(ROOT, "ci/run_fixture.py"), encoding="utf-8") as handle:
            source = handle.read()
        self.assertIn("_read_final_counters", source)
        self.assertNotIn("_sample_cgroup", source)


class JoinTests(unittest.TestCase):
    def test_surviving_reader_thread_fails_closed(self):
        from ci.run_fixture import _join_or_fail
        class Alive:
            def join(self, timeout=None):
                return None
            def is_alive(self):
                return True
        with self.assertRaises(Exception):
            _join_or_fail(Alive(), "stdout")


class FullChainTests(unittest.TestCase):
    def test_run_chain_uses_the_absolute_cgroup_path(self):
        from unittest import mock
        from ci import run_fixture, workflow_guard
        value = policy(None)
        image = value["image"]["ref"]
        image_id = "sha256:" + "2" * 64
        inspect_doc = {
            "Id": "abc", "Image": image_id,
            "Config": {"Entrypoint": [], "Cmd": spec()["argv"], "User": "65534:65534",
                       "Env": [f"{k}={v}" for k, v in value["env"].items()]},
            "State": {"ExitCode": 0, "OOMKilled": False},
            "HostConfig": {"NetworkMode": "none", "ReadonlyRootfs": True, "CapDrop": ["ALL"],
                           "SecurityOpt": ["no-new-privileges"], "Privileged": False,
                           "Memory": 268435456, "MemorySwap": 268435456, "PidsLimit": 64,
                           "NanoCpus": 1000000000,
                           "Ulimits": [{"Name": "nofile", "Soft": 256, "Hard": 256},
                                       {"Name": "nproc", "Soft": 64, "Hard": 64}]},
            "Mounts": [{"Destination": "/input", "RW": False}, {"Destination": "/out", "RW": True}],
        }

        class FakeProcess:
            def __init__(self):
                self.stdout = io.BytesIO(b"out")
                self.stderr = io.BytesIO(b"err")

            def wait(self, timeout=None):
                return 0

        class Completed:
            def __init__(self, code=0, out=b""):
                self.returncode = code
                self.stdout = out
                self.stderr = b""

        def fake_run(command, **kwargs):
            if len(command) >= 5 and command[1] == "-m" and command[2] == "ci.limit_exec":
                command = command[4:]
            if command[:2] == ["docker", "pull"]:
                return Completed()
            if command[:3] == ["docker", "image", "inspect"]:
                return Completed(0, json.dumps([{"Id": image_id, "RepoDigests": [image]}]).encode())
            if command[:2] == ["docker", "inspect"]:
                payload = json.dumps([inspect_doc]).encode()
                handle = kwargs.get("stdout")
                if hasattr(handle, "write"):
                    handle.write(payload)
                    handle.flush()
                return Completed(0, payload)
            if command[:2] == ["docker", "kill"]:
                return Completed()
            if command[:3] == ["docker", "rm", "-f"]:
                return Completed()
            return Completed()

        with tempfile.TemporaryDirectory() as temp:
            with open(os.path.join(temp, "cgroup.events"), "w") as handle:
                handle.write("populated 0\n")
            with open(os.path.join(temp, "cpu.stat"), "w") as handle:
                handle.write("usage_usec 123\n")
            with open(os.path.join(temp, "memory.peak"), "w") as handle:
                handle.write("456\n")
            with mock.patch.object(workflow_guard, "require_mandate", return_value=True), \
                 mock.patch("ci.run_fixture._create_cgroup", return_value=("ci-fixture-test", temp)), \
                 mock.patch("ci.run_fixture.os.rmdir", return_value=None), \
                 mock.patch("ci.run_fixture.subprocess.Popen", return_value=FakeProcess()), \
                 mock.patch("ci.run_fixture.subprocess.run", side_effect=fake_run) as run_mock:
                observed = run_fixture._run(spec(), value, "/input", "/out")
                self.assertEqual(observed["cpu_usec"], 123)
                self.assertEqual(observed["max_rss_bytes"], 456)
                commands = [call.args[0] for call in run_mock.call_args_list]
                self.assertTrue(any(command[:3] == ["docker", "rm", "-f"] for command in commands))


class StepsCliTests(unittest.TestCase):
    def test_attest_verify_cli_returns_zero_on_success(self):
        from unittest import mock
        from ci import steps
        with mock.patch.object(steps, "attest_verify", return_value={"repo": "o/r"}):
            code = steps.main(["attest-verify", "--policy", "ci/policy.json",
                               "--bundle-out", "/tmp/opencode/bundle", "x"])
        self.assertEqual(code, 0)

    def test_attest_verify_cli_returns_nonzero_on_error(self):
        from unittest import mock
        from ci import steps
        with mock.patch.object(steps, "attest_verify", side_effect=steps.StepError("no")):
            code = steps.main(["attest-verify", "--policy", "ci/policy.json",
                               "--bundle-out", "/tmp/opencode/bundle", "x"])
        self.assertNotEqual(code, 0)


class LaunchRefusalTests(unittest.TestCase):
    def test_direct_api_refuses_before_any_process(self):
        from unittest import mock
        from ci import run_fixture
        with mock.patch("ci.run_fixture.subprocess.run") as run_mock, \
             mock.patch("ci.run_fixture.subprocess.Popen") as popen_mock:
            with self.assertRaises(run_fixture.RunError):
                run_fixture.run("ci/specs/git-pilot-1.json", "ci/policy.json", "/input", "/out",
                                "raw.tar", "observation.json")
            run_mock.assert_not_called()
            popen_mock.assert_not_called()

    def test_dry_run_stays_static(self):
        from unittest import mock
        from ci import run_fixture
        context = {"repo": "o/r", "source_digest": run_doc()["commit_sha"],
                   "run_id": "1", "run_attempt": "1"}
        with tempfile.TemporaryDirectory() as temp:
            spec_path = os.path.join(temp, "spec.json")
            policy_path = os.path.join(temp, "policy.json")
            with open(spec_path, "w") as handle:
                json.dump(spec(), handle)
            with open(policy_path, "w") as handle:
                json.dump(policy(None), handle)
            with mock.patch("ci.run_fixture.subprocess.run") as run_mock:
                code = run_fixture.run(spec_path, policy_path, "/input", "/out",
                                       "raw.tar", "observation.json", dry_run=True)
                self.assertEqual(code, 0)
                run_mock.assert_not_called()


class OnceTests(unittest.TestCase):
    def test_pagination_covers_all_pages(self):
        from ci import once
        pages = {1: ([{"id": 1}], 2), 2: ([{"id": 2}], 2)}
        self.assertEqual(once._paginate(lambda page: pages[page]), [{"id": 1}, {"id": 2}])

    def test_pagination_gap_is_rejected(self):
        from ci import once
        with self.assertRaises(once.OnceError):
            once._paginate(lambda page: ([{"id": 1}], 2) if page == 1 else ([], 2))

    def test_pagination_total_mismatch_is_rejected(self):
        from ci import once
        with self.assertRaises(once.OnceError):
            once._paginate(lambda page: ([{"id": 1}], 2) if page == 1 else ([{"id": 2}], 3))

    def test_once_refuses_without_a_mandate_and_no_network(self):
        from unittest import mock
        from ci import once, workflow_guard
        with mock.patch.object(workflow_guard, "require_mandate", side_effect=workflow_guard.Refused("no")), \
             mock.patch("ci.once.urllib.request.urlopen") as open_mock:
            code = once.main([])
        self.assertNotEqual(code, 0)
        open_mock.assert_not_called()

    def test_prior_run_is_rejected(self):
        from ci import once
        runs = [{"id": 1}, {"id": 2}]
        with self.assertRaises(once.OnceError):
            once.check_once("o/r", "ci-fixture.yml", "a" * 40, 2, 1, "t", fetch=lambda *args: runs)

    def test_attempt_two_is_rejected(self):
        from ci import once
        with self.assertRaises(once.OnceError):
            once.check_once("o/r", "ci-fixture.yml", "a" * 40, 1, 2, "t", fetch=lambda *args: [{"id": 1}])

    def test_single_run_is_accepted(self):
        from ci import once
        self.assertTrue(once.check_once("o/r", "ci-fixture.yml", "a" * 40, 1, 1, "t",
                                        fetch=lambda *args: [{"id": 1}]))


class WorkflowTests(unittest.TestCase):
    def _text(self):
        with open(os.path.join(ROOT, ".github/workflows/ci-fixture.yml"), encoding="utf-8") as handle:
            return handle.read()

    def test_dispatch_only(self):
        text = self._text()
        self.assertIn("workflow_dispatch:", text)
        for forbidden in ("pull_request", "push:", "schedule:", "workflow_run"):
            self.assertNotIn(forbidden, text)

    def test_all_actions_pinned(self):
        for line in self._text().splitlines():
            if "uses:" in line:
                value = line.split("uses:", 1)[1].strip().split()[0]
                self.assertRegex(value, r"^[^@]+@[0-9a-f]{40}$")

    def test_five_jobs_and_guard(self):
        text = self._text()
        for job in ("gate:", "generate:", "attest:", "verify:", "attest-receipt:"):
            self.assertIn(job, text)
        self.assertEqual(text.count("python3 -m ci.workflow_guard"), 5)

    def test_generate_keeps_empty_permissions(self):
        text = self._text()
        block = text.split("  generate:", 1)[1].split("  attest:", 1)[0]
        self.assertIn("permissions: {}", block)
        self.assertNotIn("actions: read", block)
        self.assertNotIn("GH_TOKEN", block)

    def test_no_user_input_interpolation(self):
        text = self._text()
        self.assertNotIn("inputs.", text)
        self.assertNotIn("github.event.inputs", text)

    def test_attest_permissions(self):
        text = self._text()
        self.assertIn("id-token: write", text)
        self.assertIn("attestations: write", text)
        self.assertIn("attest-verify", text)
        with open(os.path.join(ROOT, "ci/steps.py"), encoding="utf-8") as handle:
            self.assertIn("--deny-self-hosted-runners", handle.read())

    def test_action_pins_match_the_policy(self):
        import re
        with open(os.path.join(ROOT, "ci/policy.json"), "rb") as handle:
            loaded = json.load(handle)
        text = self._text()
        mapping = {"checkout": "checkout", "upload-artifact": "upload", "attest-build-provenance": "attest"}
        seen = 0
        for name, sha in re.findall(r"uses:\s*actions/([a-z-]+)@([0-9a-f]{40})", text):
            if name in mapping:
                self.assertEqual(sha, loaded["actions"][mapping[name]])
                seen += 1
        self.assertGreaterEqual(seen, 3)

    def test_verify_has_no_id_token(self):
        text = self._text()
        verify_block = text.split("  verify:", 1)[1].split("  attest-receipt:", 1)[0]
        self.assertNotIn("id-token", verify_block)


class PolicyTests(unittest.TestCase):
    def test_schema_hash_matches(self):
        with open(os.path.join(ROOT, "ci/policy.json"), "rb") as handle:
            loaded = json.loads(handle.read())
        self.assertEqual(loaded["schema_sha256"], schema_sha())

    def test_limits_match(self):
        with open(os.path.join(ROOT, "ci/policy.json"), "rb") as handle:
            loaded = json.loads(handle.read())
        self.assertEqual(loaded["limits"]["files"], limits.MAX_ARTIFACT_FILES)
        self.assertEqual(loaded["limits"]["artifact_bytes"], limits.MAX_ARTIFACT_BYTES)
        self.assertEqual(loaded["limits"]["raw_tar_bytes"], limits.MAX_RAW_TAR_BYTES)


class HardeningTests(unittest.TestCase):
    def test_forged_observation_is_rejected(self):
        receipt, outputs = receipt_bundle()
        obs = observation({"a.txt": b"alpha\n", "b.txt": b"beta\n"})
        obs["argv"] = ["/runtime/bin/python3", "/input/other.py"]
        obs["inputs"] = [{"role": "fixture", "path": "fixture.json", "sha256": "0" * 64, "bytes": 0}]
        with self.assertRaises(ci_verify.ReceiptError):
            ci_verify.verify_receipt(receipt, policy=policy(None), spec=spec(), outputs_tar=outputs,
                                     observation=obs)

    def test_isolation_mismatch_is_rejected(self):
        receipt, outputs = receipt_bundle()
        obs = observation({"a.txt": b"alpha\n", "b.txt": b"beta\n"})
        obs["isolation"]["memory"] = 1
        obs["isolation_sha256"] = canon.sha256_canonical(obs["isolation"])
        with self.assertRaises(ci_verify.ReceiptError):
            ci_verify.verify_receipt(receipt, policy=policy(None), spec=spec(), outputs_tar=outputs,
                                     observation=obs)

    def test_env_extra_is_rejected(self):
        receipt, outputs = receipt_bundle()
        obs = observation({"a.txt": b"alpha\n", "b.txt": b"beta\n"})
        obs["isolation"]["env_allowlist"] = sorted(obs["isolation"]["env_allowlist"] + ["SECRET_TOKEN"])
        obs["isolation_sha256"] = canon.sha256_canonical(obs["isolation"])
        with self.assertRaises(ci_verify.ReceiptError):
            ci_verify.verify_receipt(receipt, policy=policy(None), spec=spec(), outputs_tar=outputs,
                                     observation=obs)

    def test_policy_image_id_mismatch_is_rejected(self):
        receipt, outputs = receipt_bundle()
        value = policy(None)
        value["image"]["image_id"] = "sha256:" + "9" * 64
        with self.assertRaises(ci_verify.ReceiptError):
            ci_verify.verify_receipt(receipt, policy=value, spec=spec(), outputs_tar=outputs,
                                     observation=observation({"a.txt": b"alpha\n", "b.txt": b"beta\n"}))

    def test_tools_mismatch_is_rejected(self):
        receipt, outputs = receipt_bundle()
        receipt["image"]["tools"]["git"] = "9.9.9"
        with self.assertRaises(ci_verify.ReceiptError):
            ci_verify.verify_receipt(receipt, policy=policy(None), spec=spec(), outputs_tar=outputs,
                                     observation=observation({"a.txt": b"alpha\n", "b.txt": b"beta\n"}))

    def test_trusted_root_needs_path_and_hash(self):
        value = {"schema_sha256": "c" * 64,
                 "image": {"ref": "ghcr.io/example/fixture@sha256:" + "1" * 64,
                           "image_id": "sha256:" + "2" * 64},
                 "tools": {"gh_version": "2.63.2",
                           "versions": {"git": "1", "python": "1", "pytest": "1"}},
                 "actions": {"checkout": "a" * 40, "upload": "b" * 40, "attest": "c" * 40},
                 "attestation": {"trusted_root_path": None, "trusted_root_sha256": "a" * 64,
                                 "gh_profile": "gh-attestation-v1",
                                 "gh_example_path": "ci/fixtures/gh-attestation-v1.example.json",
                                 "gh_example_sha256": "f8ce946ec520846ead9f9e25a40cb766feb1de7d813d18cb33e30d1a6357c479",
                                 "issuer": "issuer", "signer_workflow": "wf", "signer_ref": "refs/heads/main",
                                 "predicate_type": "p"}}
        with self.assertRaises(Exception):
            require_pins(value)
        value["attestation"]["trusted_root_path"] = "root.json"
        require_pins(value)

    def test_example_hash_mismatch_is_rejected(self):
        value = {"tools": {"gh_version": "2.63.2"},
                 "attestation": {"trusted_root_path": "root.json", "trusted_root_sha256": "a" * 64,
                                 "gh_profile": "gh-attestation-v1",
                                 "gh_example_path": "ci/fixtures/gh-attestation-v1.example.json",
                                 "gh_example_sha256": "0" * 64}}
        with self.assertRaises(Exception):
            require_pins(value)

    def test_cgroup_is_created_below_the_delegated_parent(self):
        from unittest import mock
        from ci.run_fixture import _create_cgroup
        with tempfile.TemporaryDirectory() as temp:
            parent = os.path.join(temp, "parent")
            os.makedirs(parent)
            with mock.patch("ci.run_fixture._cgroup_driver", return_value="cgroupfs"):
                name, path = _create_cgroup("ci-fixture-", parent, temp)
            self.assertTrue(path.startswith(parent))
            self.assertEqual(name, os.path.relpath(path, temp))
            with open(os.path.join(path, "cgroup.subtree_control"), encoding="ascii") as handle:
                content = handle.read()
            self.assertIn("+cpu", content)
            self.assertIn("+memory", content)

    def test_run_attempt_match_is_exact(self):
        pattern = run_invocation_pattern("o/r", 7, 1)
        self.assertIsNone(re.search(pattern, "https://github.com/o/r/actions/runs/7/attempts/10"))
        self.assertIsNotNone(re.search(pattern, "https://github.com/o/r/actions/runs/7/attempts/1"))

    def test_observation_limit_hit_is_rejected(self):
        receipt, outputs = receipt_bundle()
        obs = observation({"a.txt": b"alpha\n", "b.txt": b"beta\n"})
        obs["limit_hit"] = ["stdout"]
        with self.assertRaises(ci_verify.ReceiptError):
            ci_verify.verify_receipt(receipt, policy=policy(None), spec=spec(), outputs_tar=outputs,
                                     observation=obs)

    def test_receipt_limit_hit_mismatch_is_rejected(self):
        receipt, outputs = receipt_bundle()
        receipt["limits"]["limit_hit"] = ["stdout"]
        with self.assertRaises(ci_verify.ReceiptError):
            ci_verify.verify_receipt(receipt, policy=policy(None), spec=spec(), outputs_tar=outputs,
                                     observation=observation({"a.txt": b"alpha\n", "b.txt": b"beta\n"}))

    def test_zip_per_entry_cap(self):
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive_file:
            archive_file.writestr("raw.tar", b"x" * (limits.MAX_RAW_TAR_BYTES + 1))
            archive_file.writestr("observation.json", b"{}")
        with self.assertRaises(archive.ArchiveError):
            archive.parse_zip(buffer.getvalue(), allowed_names={"raw.tar", "observation.json"},
                              entry_caps={"raw.tar": limits.MAX_RAW_TAR_BYTES})

    def test_non_canonical_tar_rejected(self):
        data = archive.pack_tar({"a.txt": b"alpha"})
        # Flip one header byte in the mode field: the bytes still parse, but not canonically.
        broken = bytearray(data)
        broken[100] = ord("7")
        with self.assertRaises(archive.ArchiveError):
            archive.parse_tar(bytes(broken), require_canonical=True)

    def test_small_compressible_zip_is_accepted(self):
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive_file:
            archive_file.writestr("raw.tar", b"a" * 200)
            archive_file.writestr("observation.json", b"{}")
        files = archive.parse_zip(buffer.getvalue(), allowed_names={"raw.tar", "observation.json"})
        self.assertEqual(len(files["raw.tar"]), 200)


class DockerCommandTests(unittest.TestCase):
    def test_flags(self):
        command = docker_command(spec(), policy(None), "/input", "/out", "name")
        joined = " ".join(command)
        for flag in ("--network none", "--user 65534:65534", "--read-only", "--cap-drop ALL",
                     "--security-opt no-new-privileges", "--memory-swap 256m", "--cpus 1"):
            self.assertIn(flag, joined)
        self.assertNotIn("--privileged", joined)
        self.assertNotIn("/var/run/docker.sock", joined)

    def test_forbidden_env_refused(self):
        value = policy(None)
        value["env"]["GITHUB_TOKEN"] = "x"
        with self.assertRaises(Exception):
            docker_command(spec(), value, "/input", "/out", "name")


if __name__ == "__main__":
    unittest.main()
