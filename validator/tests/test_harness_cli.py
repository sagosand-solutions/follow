# SPDX-License-Identifier: MIT
"""Static CLI tests: verify/plan read and plan, run refuses, and nothing ever launches a process."""
import contextlib
import io
import json
import pathlib
import shutil
import tempfile
import unittest
from unittest import mock

from validator.harness import canonical, cli
from validator.tests.test_harness_manifest import valid


class CliTests(unittest.TestCase):
    def setUp(self):
        self.root = pathlib.Path(tempfile.mkdtemp(prefix="harness-cli-"))
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        self.document = valid()
        self.raw = canonical.dumps_canonical(self.document)
        (self.root / "m.json").write_bytes(self.raw)
        self.binding = {"manifest_uri": "m.json", "manifest_sha256": canonical.manifest_sha256(self.raw),
                        "binding_sha256": self.document["binding_sha256"]}
        self.write_binding(self.binding)

    def write_binding(self, value):
        (self.root / "binding.json").write_text(json.dumps(value), encoding="utf-8")

    def run_cli(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = cli.main(list(argv))
        return code, out.getvalue(), err.getvalue()

    def test_verify_accepts_a_clean_manifest_and_never_executes(self):
        with mock.patch("subprocess.run") as run, mock.patch("subprocess.Popen") as popen, mock.patch("os.execv") as execv:
            code, out, _ = self.run_cli("verify", "--root", str(self.root), "--binding", "binding.json")
            run.assert_not_called()
            popen.assert_not_called()
            execv.assert_not_called()
        self.assertEqual(code, 0)
        self.assertIn("VALID", out)
        self.assertIn("schema validation is the caller", out)

    def test_a_changed_manifest_is_red(self):
        (self.root / "m.json").write_bytes(self.raw + b" ")
        code, _, err = self.run_cli("verify", "--root", str(self.root), "--binding", "binding.json")
        self.assertEqual(code, 1)
        self.assertIn("manifest-not-approved", err)

    def test_a_binding_missing_the_two_links_is_an_error(self):
        self.write_binding({"manifest_sha256": canonical.manifest_sha256(self.raw)})
        code, _, err = self.run_cli("verify", "--root", str(self.root), "--binding", "binding.json")
        self.assertEqual(code, 2)
        self.assertIn("manifest_uri", err)

    def test_a_binding_that_points_outside_the_root_is_refused(self):
        self.write_binding({"manifest_uri": "../m.json", "manifest_sha256": canonical.manifest_sha256(self.raw)})
        code, _, err = self.run_cli("verify", "--root", str(self.root), "--binding", "binding.json")
        self.assertEqual(code, 2)
        self.assertIn("..", err)

    def test_a_missing_manifest_is_an_error(self):
        self.write_binding({"manifest_uri": "nope.json", "manifest_sha256": canonical.manifest_sha256(self.raw)})
        code, _, err = self.run_cli("verify", "--root", str(self.root), "--binding", "binding.json")
        self.assertEqual(code, 2)

    def test_a_manifest_without_argv_is_a_finding_not_a_crash(self):
        value = valid()
        del value["bindings"]["argv"]
        value["binding_sha256"] = canonical.binding_sha256(value)
        raw = canonical.dumps_canonical(value)
        (self.root / "m.json").write_bytes(raw)
        self.write_binding({"manifest_uri": "m.json", "manifest_sha256": canonical.manifest_sha256(raw)})
        code, _, err = self.run_cli("verify", "--root", str(self.root), "--binding", "binding.json")
        self.assertEqual(code, 1)
        self.assertIn("bindings-argv", err)

    def test_an_optional_schema_is_applied_and_claimed_exactly(self):
        schema = {"type": "object", "required": ["manifest_schema_version", "no_such_field"]}
        (self.root / "schema.json").write_text(json.dumps(schema), encoding="utf-8")
        code, _, err = self.run_cli("verify", "--root", str(self.root), "--binding", "binding.json",
                                    "--schema", str(self.root / "schema.json"))
        self.assertEqual(code, 1)
        self.assertIn("schema:", err)
        (self.root / "schema.json").write_text(json.dumps({"type": "object"}), encoding="utf-8")
        code, out, _ = self.run_cli("verify", "--root", str(self.root), "--binding", "binding.json",
                                    "--schema", str(self.root / "schema.json"))
        self.assertEqual(code, 0)
        self.assertIn("schema accepted", out)

    def test_plan_prints_the_isolated_plan_and_runs_nothing(self):
        with mock.patch("subprocess.run") as run, mock.patch("subprocess.Popen") as popen:
            code, out, _ = self.run_cli("plan", "--root", str(self.root), "--binding", "binding.json",
                                        "--runtime", "/opt/pinned-runtime", "--work", "/scratch/run", "--input", "/snapshot/run")
            run.assert_not_called()
            popen.assert_not_called()
        self.assertEqual(code, 0, out)
        plan = json.loads(out)
        self.assertEqual(set(plan), {"bwrap", "cgroup", "exec", "budget", "work", "input"})
        self.assertEqual(plan["bwrap"][0], "bwrap")
        self.assertIn("--unshare-all", plan["bwrap"])
        self.assertIn("--cap-drop", plan["bwrap"])
        self.assertEqual(plan["cgroup"]["pids.max"], "32")

    def test_run_refuses_and_never_calls_subprocess(self):
        with mock.patch("subprocess.run") as run, mock.patch("subprocess.Popen") as popen, mock.patch("os.execv") as execv:
            code, _, err = self.run_cli("run")
            run.assert_not_called()
            popen.assert_not_called()
            execv.assert_not_called()
        self.assertEqual(code, 2)
        self.assertIn("not authorized", err)


if __name__ == "__main__":
    unittest.main()
