# SPDX-License-Identifier: MIT
"""Launch, mount, cgroup and exec policy for the offline fixture harness — planning only.

This module builds the exact ``bwrap`` argument list, the read-only runtime binds, the cgroup-v2 limits and the executable
allowlist. It launches nothing: :func:`run_plan` refuses unless a separate safety-test authorization exists (none does), so
no fixture can run through this code until an explicit, separate execution authorization is granted.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Mapping, Sequence

BUDGET = {
    "wall_seconds": 10,
    "cpu_soft_seconds": 4,
    "cpu_hard_seconds": 5,
    "memory_bytes": 256 * 1024 * 1024,
    "swap_bytes": 0,
    "pids": 32,
    "output_bytes_per_stream": 1024 * 1024,
    "artifact_bytes": 4 * 1024 * 1024,
    "artifact_files": 64,
    "scratch_bytes": 64 * 1024 * 1024,
    "scratch_inodes": 4096,
    "open_fds": 64,
    "core_bytes": 0,
}

SHELL_NAMES = frozenset({"sh", "bash", "dash", "zsh", "ksh", "fish", "csh", "tcsh", "busybox"})
HOST_WHOLE_DIRS = ("/", "/usr", "/etc", "/home", "/run", "/tmp", "/root")


class PlanError(ValueError):
    """The plan is not safe or not well formed."""


class NotAuthorized(RuntimeError):
    """Fixture execution is not authorized (and is not implemented here)."""


@dataclass(frozen=True)
class Runtime:
    """A pinned minimal runtime image: only the executables and libraries it needs, without a shell."""
    image: str
    executables: tuple[str, ...]
    bind_ro: tuple[tuple[str, str], ...]

    def __post_init__(self):
        if not self.image.startswith("/"):
            raise PlanError("the runtime image must be an absolute host path")
        if not self.executables:
            raise PlanError("the runtime needs at least one executable")
        for exe in self.executables:
            if not exe.startswith("/") or "\x00" in exe:
                raise PlanError(f"executable {exe!r} is not an absolute sandbox path")
            if exe.rsplit("/", 1)[-1].lower() in SHELL_NAMES:
                raise PlanError(f"a shell ({exe}) is not allowed in the runtime")
        for host, sandbox in self.bind_ro:
            if host.startswith("/") or ".." in host.split("/") or not sandbox.startswith("/"):
                raise PlanError(f"bind {host!r} -> {sandbox!r} must be a relative image path to an absolute sandbox path")


def runtime_plan(image: str) -> Runtime:
    """The minimal read-only runtime: the pinned Python, Git and their libraries — no shell, no host /usr, /etc or /home."""
    return Runtime(
        image=image,
        executables=("/runtime/bin/python3", "/runtime/bin/git"),
        bind_ro=(
            ("bin/python3", "/runtime/bin/python3"),
            ("bin/git", "/runtime/bin/git"),
            # A normal glibc Python asks PT_INTERP for /lib64/...; a patched image may ask for /runtime/lib64/...
            ("lib64/ld-linux-x86-64.so.2", "/lib64/ld-linux-x86-64.so.2"),
            ("lib64/ld-linux-x86-64.so.2", "/runtime/lib64/ld-linux-x86-64.so.2"),
            ("lib/python3.14", "/runtime/lib/python3.14"),
            ("lib/x86_64-linux-gnu", "/runtime/lib/x86_64-linux-gnu"),
        ),
    )


def exec_policy(runtime: Runtime) -> dict:
    allow = list(runtime.executables)
    if any(path.rsplit("/", 1)[-1].lower() in SHELL_NAMES for path in allow):
        raise PlanError("the exec allowlist must not contain a shell")
    return {"allow": allow, "deny_shell": True, "network": "denied"}


def cgroup_plan(budget: Mapping[str, int] = BUDGET) -> dict:
    return {
        "memory.max": str(budget["memory_bytes"]),
        "memory.swap.max": "0",
        "pids.max": str(budget["pids"]),
        # 4 CPU-seconds over a 10-second job: rate 0.4 CPU, with RLIMIT_CPU soft 4 / hard 5 as the hard stop.
        "cpu.max": f"{budget['cpu_soft_seconds'] * 100_000} 1000000",
        "cgroup.kill": "1",
        "rlimit": {"CPU": (budget["cpu_soft_seconds"], budget["cpu_hard_seconds"]), "NOFILE": budget["open_fds"],
                   "CORE": budget["core_bytes"], "FSIZE": budget["artifact_bytes"]},
    }


def _sandbox_path(value: str, name: str) -> str:
    if not isinstance(value, str) or not value.startswith("/") or "\x00" in value:
        raise PlanError(f"{name} must be an absolute sandbox path")
    return value


def _reject_whole_host_dir(value: str, name: str) -> None:
    """Resolve the path the way the kernel would (symlinks and '.'/'..' included) and refuse a whole host directory."""
    real = os.path.realpath(value)
    real = "/" + real.lstrip("/")           # '//' is the same directory as '/' on Linux
    if real == "/" or real in HOST_WHOLE_DIRS:
        raise PlanError(f"{name}={value!r} resolves to {real!r}, a whole host directory")


def _validate_argv(runtime: Runtime, argv: Sequence[str]) -> list[str]:
    if isinstance(argv, (str, bytes)) or not isinstance(argv, Sequence) or not argv:
        raise PlanError("argv must be a non-empty list of strings")
    for entry in argv:
        if not isinstance(entry, str) or "\x00" in entry:
            raise PlanError("every argv entry must be a string without NUL")
    exe = argv[0]
    if exe.rsplit("/", 1)[-1].lower() in SHELL_NAMES:
        raise PlanError("a shell is never the harness command")
    if exe in runtime.executables:
        return list(argv)
    by_name = {path.rsplit("/", 1)[-1]: path for path in runtime.executables}
    if exe in by_name:                       # the manifest may name the tool by its exact command name
        return [by_name[exe], *argv[1:]]
    raise PlanError(f"{exe!r} is not in the pinned runtime's exec allowlist")


def bwrap_argv(runtime: Runtime, *, work: str, input_dir: str, argv: Sequence[str],
               env: Mapping[str, str] | None = None) -> list[str]:
    """The exact bwrap command. ``work`` (RW scratch) and ``input_dir`` (RO snapshot) are host paths; nothing else is bound."""
    _sandbox_path(work, "work")
    _sandbox_path(input_dir, "input_dir")
    command = _validate_argv(runtime, argv)
    _reject_whole_host_dir(work, "work")
    _reject_whole_host_dir(input_dir, "input_dir")
    result = [
        "bwrap",
        "--unshare-all",            # user, mount, pid, net, ipc, uts, cgroup
        "--die-with-parent",
        "--new-session",
        "--cap-drop", "ALL",
        "--no-new-privs",
        "--proc", "/proc",
        "--dev", "/dev",
        "--tmpfs", "/tmp",          # a private tmpfs, never the host's /tmp
        "--clearenv",
    ]
    for key, value in sorted((env or {}).items()):
        if not isinstance(key, str) or not isinstance(value, str) or "\x00" in key or "\x00" in value:
            raise PlanError("environment entries are strings without NUL")
        result += ["--setenv", key, value]
    for host, sandbox in runtime.bind_ro:
        result += ["--ro-bind", f"{runtime.image}/{host}", sandbox]
    result += ["--bind", work, "/work", "--ro-bind", input_dir, "/input", "--chdir", "/work", "--", *command]
    return result


def plan_command(runtime: Runtime, *, work: str, input_dir: str, argv: Sequence[str],
                 env: Mapping[str, str] | None = None, budget: Mapping[str, int] = BUDGET) -> dict:
    """The complete plan (command, mounts, cgroup, exec policy, budget). It does not run anything."""
    return {
        "bwrap": bwrap_argv(runtime, work=work, input_dir=input_dir, argv=argv, env=env),
        "cgroup": cgroup_plan(budget),
        "exec": exec_policy(runtime),
        "budget": dict(budget),
        "work": "/work",
        "input": "/input",
    }


def run_plan(plan: dict, *, authorization: str | None = None) -> None:
    """Never runs here: execution needs a separate, explicit safety-test authorization that this delivery does not have."""
    raise NotAuthorized("fixture execution requires an explicit, separate execution authorization; none has been granted")
