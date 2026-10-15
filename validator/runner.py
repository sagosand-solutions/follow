# SPDX-License-Identifier: MIT
"""Execution boundary. TrustedLocalRunner is NOT a security sandbox."""
from dataclasses import dataclass, field
import math
import os
from pathlib import Path
import selectors
import signal
import subprocess
import sys
import tempfile
import time
from typing import Literal, Protocol

Status = Literal["completed", "timeout", "output-limit", "memory-limit", "unavailable", "error"]


@dataclass(frozen=True)
class Limits:
    wall_seconds: float = 2
    memory_bytes: int = 134217728
    output_bytes: int = 65536
    processes: int = 16

    def __post_init__(self):
        if not math.isfinite(self.wall_seconds) or self.wall_seconds <= 0:
            raise ValueError("wall_seconds must be finite and positive")
        for name in ("memory_bytes", "output_bytes", "processes"):
            value = getattr(self, name)
            if type(value) is not int or value <= 0:
                raise ValueError(f"{name} must be a positive integer")


@dataclass(frozen=True)
class RunResult:
    status: Status
    stdout: str = ""
    stderr: str = ""
    exit_code: int | None = None
    elapsed_seconds: float = 0
    python_version: str = sys.version.split()[0]
    capabilities: dict = field(default_factory=dict)


class Runner(Protocol):
    def run(self, code: str, stdin: str, limits: Limits) -> RunResult: ...


class TrustedLocalRunner:
    name = "trusted-local"
    isolation = "trusted-code-only-no-network-isolation"

    def __init__(self, *, allow_trusted_code=False):
        self.allowed = allow_trusted_code

    def unavailable_reason(self):
        if not self.allowed:
            return "Explicit --allow-trusted-code acknowledgment required"
        if sys.platform != "linux":
            return "Trusted-local execution requires Linux resource controls"
        if os.geteuid() == 0:
            return "Refusing root: RLIMIT_NPROC does not constrain privileged processes"
        try:
            import resource
            for name in ("RLIMIT_AS", "RLIMIT_CPU", "RLIMIT_FSIZE", "RLIMIT_NPROC"):
                resource.getrlimit(getattr(resource, name))
        except (ImportError, AttributeError, OSError, ValueError) as exc:
            return f"Required resource controls unavailable: {exc}"
        return None

    def run(self, code, stdin, limits):
        reason = self.unavailable_reason()
        if reason:
            return RunResult("unavailable", stderr=reason)
        import resource
        start = time.monotonic()
        capabilities = {"isolation": self.isolation, "network_isolated": False,
                        "host_filesystem_isolated": False, "environment_cleared": True,
                        "rlimits": ["AS", "CPU", "FSIZE", "NPROC"],
                        "process_limit_scope": "per-real-user; not a per-run process sandbox"}
        buffers = {"stdout": bytearray(), "stderr": bytearray()}
        status, process = "completed", None

        def restrict():
            def cap(kind, value):
                _, hard = resource.getrlimit(kind)
                if hard != resource.RLIM_INFINITY:
                    value = min(value, hard)
                resource.setrlimit(kind, (value, value))
            cap(resource.RLIMIT_AS, limits.memory_bytes)
            cap(resource.RLIMIT_CPU, max(1, math.ceil(limits.wall_seconds)))
            cap(resource.RLIMIT_FSIZE, limits.output_bytes)
            cap(resource.RLIMIT_NPROC, limits.processes)
            os.umask(0o077)

        try:
            with tempfile.TemporaryDirectory(prefix="code-check-") as directory:
                script = Path(directory) / "snippet.py"
                script.write_text(code, encoding="utf-8")
                input_path = Path(directory) / "stdin"
                input_path.write_bytes(stdin.encode("utf-8"))
                with input_path.open("rb") as input_file, selectors.DefaultSelector() as selector:
                    try:
                        process = subprocess.Popen(
                            [sys.executable, "-I", str(script)], stdin=input_file,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, cwd=directory,
                            env={"LANG": "C.UTF-8"}, shell=False, close_fds=True,
                            start_new_session=True, preexec_fn=restrict)
                        for name in buffers:
                            stream = getattr(process, name)
                            os.set_blocking(stream.fileno(), False)
                            selector.register(stream, selectors.EVENT_READ, name)
                        while selector.get_map():
                            remaining = limits.wall_seconds - (time.monotonic() - start)
                            if remaining <= 0:
                                status = "timeout"
                                break
                            for key, _ in selector.select(min(remaining, 0.05)):
                                chunk = os.read(key.fd, 8192)
                                if not chunk:
                                    selector.unregister(key.fileobj)
                                    continue
                                room = limits.output_bytes - sum(map(len, buffers.values()))
                                buffers[key.data].extend(chunk[:room])
                                if len(chunk) > room:
                                    status = "output-limit"
                                    break
                            if status != "completed":
                                break
                        if status == "completed":
                            remaining = limits.wall_seconds - (time.monotonic() - start)
                            try:
                                process.wait(timeout=max(0, remaining))
                            except subprocess.TimeoutExpired:
                                status = "timeout"
                    finally:
                        # Kill the entire group even if the script exited but left children.
                        if process is not None:
                            try:
                                os.killpg(process.pid, signal.SIGKILL)
                            except ProcessLookupError:
                                pass
                            process.wait()
                            process.stdout.close()
                            process.stderr.close()
        except (OSError, subprocess.SubprocessError, ValueError) as exc:
            return RunResult("unavailable", stderr=f"Local runner setup failed: {exc}",
                             elapsed_seconds=time.monotonic() - start, capabilities=capabilities)
        try:
            stdout, stderr = (bytes(buffers[n]).decode("utf-8") for n in ("stdout", "stderr"))
        except UnicodeDecodeError:
            return RunResult("error", stderr="Output is not valid UTF-8", exit_code=process.returncode,
                             elapsed_seconds=time.monotonic() - start, capabilities=capabilities)
        if status == "completed" and process.returncode < 0:
            status = "error"
        # Conservative: resource exhaustion must never validate an expected crash.
        if status == "completed" and "MemoryError" in stderr:
            status = "memory-limit"
        resource_errors = ("OSError: [Errno 11]", "OSError: [Errno 12]", "OSError: [Errno 24]", "OSError: [Errno 28]")
        if status == "completed" and any(error in stderr for error in resource_errors):
            status = "error"
        return RunResult(status, stdout, stderr, process.returncode,
                         time.monotonic() - start, capabilities=capabilities)


def verify_question(question, runner: Runner, limits=None):
    """Return (issues, infrastructure_failure); injectable without spawning processes."""
    from .core import Issue
    limits = limits or Limits()
    snippets = {s["id"]: s["code"] for s in question["snippets"]}
    issues, infrastructure = [], False
    for i, case in enumerate(question["verification"]["cases"]):
        result = runner.run(snippets[case["snippet_id"]], case["stdin"], limits)
        path = f"/verification/cases/{i}"
        if result.status != "completed" or result.exit_code is None or result.exit_code < 0:
            issues.append(Issue("runner-" + result.status, path, "Execution failed or exceeded resources; not an expected crash"))
            infrastructure = True
            continue
        expected = case["expected"]
        if result.stdout != expected["stdout"]:
            issues.append(Issue("stdout", path + "/expected/stdout", "Observed stdout differs (including newlines)"))
        if result.exit_code != expected["exit_code"]:
            issues.append(Issue("exit-code", path + "/expected/exit_code", f"Observed exit code {result.exit_code}"))
        if "stderr_contains" in expected:
            if expected["stderr_contains"] not in result.stderr:
                issues.append(Issue("stderr", path + "/expected/stderr_contains", "Required literal substring absent from stderr"))
        elif result.stderr:
            issues.append(Issue("stderr", path + "/expected", "Unexpected stderr"))
    return issues, infrastructure
