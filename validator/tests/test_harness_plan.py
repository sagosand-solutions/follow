# SPDX-License-Identifier: MIT
"""Static tests for the launch/mount/cgroup/exec-policy plan. Nothing is launched; no fixture runs."""
import unittest
from unittest import mock

from validator.harness import plan


class BudgetAndRuntimeTests(unittest.TestCase):
    def test_the_budget_is_the_contract_s_budget(self):
        self.assertEqual(plan.BUDGET, {
            "wall_seconds": 10, "cpu_soft_seconds": 4, "cpu_hard_seconds": 5,
            "memory_bytes": 268435456, "swap_bytes": 0, "pids": 32,
            "output_bytes_per_stream": 1048576, "artifact_bytes": 4194304, "artifact_files": 64,
            "scratch_bytes": 67108864, "scratch_inodes": 4096, "open_fds": 64, "core_bytes": 0,
        })

    def test_the_runtime_has_no_shell_and_only_image_relative_binds(self):
        runtime = plan.runtime_plan("/opt/pinned-runtime")
        self.assertEqual(runtime.executables, ("/runtime/bin/python3", "/runtime/bin/git"))
        for host, sandbox in runtime.bind_ro:
            self.assertFalse(host.startswith("/"), host)
            self.assertTrue(sandbox.startswith("/runtime/"), sandbox)
        for exe in runtime.executables:
            self.assertNotIn(exe.rsplit("/", 1)[-1].lower(), plan.SHELL_NAMES)
        with self.assertRaises(plan.PlanError):
            plan.Runtime(image="/opt/r", executables=("/runtime/bin/sh",), bind_ro=())
        with self.assertRaises(plan.PlanError):
            plan.Runtime(image="/opt/r", executables=("/runtime/bin/python3",), bind_ro=(("/usr", "/runtime/usr"),))

    def test_the_cgroup_plan_carries_the_contract_limits(self):
        cgroup = plan.cgroup_plan()
        self.assertEqual(cgroup["memory.max"], "268435456")
        self.assertEqual(cgroup["memory.swap.max"], "0")
        self.assertEqual(cgroup["pids.max"], "32")
        self.assertEqual(cgroup["cpu.max"], "400000 1000000")
        self.assertEqual(cgroup["cgroup.kill"], "1")
        self.assertEqual(cgroup["rlimit"]["CPU"], (4, 5))
        self.assertEqual(cgroup["rlimit"]["NOFILE"], 64)
        self.assertEqual(cgroup["rlimit"]["CORE"], 0)


class BwrapPlanTests(unittest.TestCase):
    def setUp(self):
        self.runtime = plan.runtime_plan("/opt/pinned-runtime")
        self.argv = ["/runtime/bin/git", "-C", "/work/repo", "diff", "--no-color"]

    def command(self, **over):
        kw = dict(work="/scratch/run-1", input_dir="/snapshot/run-1", argv=self.argv, env={"TZ": "UTC", "LC_ALL": "C"})
        kw.update(over)
        return plan.bwrap_argv(self.runtime, **kw)

    def test_the_isolation_flags_are_all_present(self):
        command = self.command()
        self.assertEqual(command[0], "bwrap")
        for pair in (("--unshare-all",), ("--die-with-parent",), ("--new-session",), ("--cap-drop", "ALL"),
                     ("--no-new-privs",), ("--proc", "/proc"), ("--dev", "/dev"), ("--tmpfs", "/tmp"), ("--clearenv",)):
            with self.subTest(pair=pair):
                joined = " ".join(command)
                self.assertIn(" ".join(pair), joined)
        self.assertNotIn("--share-net", command)

    def test_nothing_from_a_whole_host_directory_is_bound(self):
        command = self.command()
        for index, entry in enumerate(command):
            if entry in ("--ro-bind", "--bind"):
                source = command[index + 1]
                self.assertNotIn(source, plan.HOST_WHOLE_DIRS, source)
                if entry == "--ro-bind":
                    self.assertTrue(source.startswith("/opt/pinned-runtime/") or source == "/snapshot/run-1", source)

    def test_work_is_read_write_input_is_read_only_and_the_command_is_last(self):
        command = self.command()
        bind = command.index("--bind")
        self.assertEqual(command[bind + 1:bind + 3], ["/scratch/run-1", "/work"])
        input_ro = command.index("--ro-bind", bind)
        self.assertEqual(command[input_ro + 1:input_ro + 3], ["/snapshot/run-1", "/input"])
        self.assertEqual(command[command.index("--") + 1:], self.argv)

    def test_argv_is_a_list_of_strings_and_the_executable_is_allowlisted(self):
        for argv in ([], "git diff", ["/runtime/bin/sh", "-c", "x"], ["/runtime/bin/git", "a\x00b"], [1]):
            with self.subTest(argv=argv):
                with self.assertRaises(plan.PlanError):
                    self.command(argv=argv)

    def test_a_bare_command_name_maps_to_the_pinned_runtime_path(self):
        command = self.command(argv=["git", "status"])
        self.assertEqual(command[command.index("--") + 1:], ["/runtime/bin/git", "status"])

    def test_a_whole_host_directory_as_work_is_refused(self):
        for work in ("/tmp", "/usr", "/etc", "/home", "/run", "/root", "relative"):
            with self.subTest(work=work):
                with self.assertRaises(plan.PlanError):
                    self.command(work=work)

    def test_a_hostile_environment_value_cannot_inject_an_argument(self):
        with self.assertRaises(plan.PlanError):
            self.command(env={"X": "a\x00b"})

    def test_the_exec_policy_allows_only_the_pinned_executables_and_no_shell(self):
        policy = plan.exec_policy(self.runtime)
        self.assertEqual(policy["allow"], ["/runtime/bin/python3", "/runtime/bin/git"])
        self.assertTrue(policy["deny_shell"])
        self.assertEqual(policy["network"], "denied")


class RunPlanTests(unittest.TestCase):
    def test_run_plan_never_executes_anything(self):
        runtime = plan.runtime_plan("/opt/r")
        full = plan.plan_command(runtime, work="/scratch/run", input_dir="/snapshot/run",
                                 argv=["/runtime/bin/git", "status"], env={"TZ": "UTC"})
        with mock.patch("subprocess.run") as run, mock.patch("subprocess.Popen") as popen, mock.patch("os.execv") as execv:
            with self.assertRaises(plan.NotAuthorized):
                plan.run_plan(full)
            run.assert_not_called()
            popen.assert_not_called()
            execv.assert_not_called()
        with self.assertRaises(plan.NotAuthorized):
            plan.run_plan(full, authorization="try anyway")

    def test_the_full_plan_carries_command_cgroup_policy_budget_and_internal_paths(self):
        runtime = plan.runtime_plan("/opt/r")
        full = plan.plan_command(runtime, work="/scratch/run", input_dir="/snapshot/run",
                                 argv=["/runtime/bin/git", "status"], env={"TZ": "UTC"})
        self.assertEqual(set(full), {"bwrap", "cgroup", "exec", "budget", "work", "input"})
        self.assertEqual((full["work"], full["input"]), ("/work", "/input"))
        self.assertEqual(full["budget"], plan.BUDGET)


if __name__ == "__main__":
    unittest.main()
