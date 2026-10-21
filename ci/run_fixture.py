# SPDX-License-Identifier: AGPL-3.0-or-later
"""Run one fixture in a nested, bounded container and write the observation and the raw tar.

The trusted host side owns every step: pulls the pinned image, creates the container with an explicit minimal mount set,
starts it, caps stdout/stderr, enforces the wall limit, reads the cgroup counters, inspects the applied isolation flags,
removes the container, and only then walks the host tmpfs and packs ``raw.tar``. The fixture itself gets no token, no
runner socket, no workspace and no inherited file descriptors.

``--dry-run`` prints the command and the isolation subset without touching Docker; the static tests use it.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import shutil
import resource
import subprocess
import sys
import tempfile
import threading
import time

from . import archive, canon, labels, limits, workflow_guard


class RunError(RuntimeError):
    pass


def _now():
    return dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z")


def _sha(data):
    return hashlib.sha256(data).hexdigest()


def fileset_digest(files):
    return canon.sha256_canonical([{"path": path, "sha256": _sha(files[path]), "bytes": len(files[path])}
                                   for path in sorted(files)])


def _require(condition, message):
    if not condition:
        raise RunError(message)


def docker_command(spec, policy, input_dir, out_dir, container_name, cgroup_parent=None):
    isolation = policy["isolation"]
    command = [
        "docker", "run", "--name", container_name, "--label", labels.label_argument(),
        "--network", isolation["network_mode"],
        *(["--cgroup-parent", cgroup_parent] if cgroup_parent else []),
        "--user", isolation["user"],
        "--read-only",
        "--log-driver", "none",
        "--tmpfs", f"/tmp:{isolation['tmpfs']['/tmp']}",
        "--cap-drop", "ALL",
        "--security-opt", "no-new-privileges",
        "--pids-limit", "64",
        "--memory", "256m",
        "--memory-swap", "256m",
        "--cpus", "1",
        "--ulimit", "nofile=256:256",
        "--ulimit", "nproc=64",
        "-v", f"{input_dir}:/input:ro",
        "-v", f"{out_dir}:/out:rw",
    ]
    for key, value in sorted(policy["env"].items()):
        _require(key in isolation["env_allowlist"], f"env {key!r} is not in the allowlist")
        command += ["-e", f"{key}={value}"]
    command.append(spec["image_ref"])
    command += list(spec["argv"])
    return command


def _read_limited(stream, cap, sink):
    total = 0
    truncated = False
    while True:
        chunk = stream.read(65536)
        if not chunk:
            break
        if total < cap:
            take = chunk[: cap - total]
            sink.append(take)
            total += len(take)
            if len(take) != len(chunk):
                truncated = True
        else:
            truncated = True
    return truncated


def _cgroup_driver():
    result = subprocess.run(["docker", "info", "--format", "{{.CgroupDriver}}"], check=True,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    return result.stdout.decode().strip()


def _require_cgroupfs(driver):
    if driver != "cgroupfs":
        raise RunError(f"the cgroup driver {driver!r} is not the supported cgroupfs form")


CONTROLLERS = ("cpu", "memory", "pids")


def _enable_controllers(path):
    line = " ".join(f"+{name}" for name in CONTROLLERS)
    try:
        with open(os.path.join(path, "cgroup.subtree_control"), "w", encoding="ascii") as handle:
            handle.write(line)
    except OSError as exc:
        raise RunError(f"cannot enable the controllers in {path}: {exc.strerror}") from None


def _join_or_fail(thread, label):
    thread.join(timeout=5)
    if thread.is_alive():
        raise RunError(f"the {label} reader thread survived; refusing further subprocesses")


def _inspect(name):
    with tempfile.TemporaryDirectory() as temp:
        path = os.path.join(temp, "inspect.json")
        command = [sys.executable, "-m", "ci.limit_exec", str(4 * 1024 * 1024), "docker", "inspect", name]
        with open(path, "wb") as handle:
            try:
                subprocess.run(command, check=True, stdout=handle,
                               stderr=subprocess.DEVNULL, timeout=30)
            except subprocess.CalledProcessError as exc:
                raise RunError(f"docker inspect failed with {exc.returncode}") from None
        if os.path.getsize(path) > 4 * 1024 * 1024:
            raise RunError("docker inspect output exceeded its cap")
        with open(path, "rb") as handle:
            data = handle.read(4 * 1024 * 1024 + 1)
    if len(data) > 4 * 1024 * 1024:
        raise RunError("docker inspect output exceeded its cap")
    return json.loads(data)[0]


def _create_cgroup(prefix, parent_dir, root="/sys/fs/cgroup"):
    _require_cgroupfs(_cgroup_driver())
    name = f"{prefix}{os.getpid()}"
    path = os.path.join(parent_dir, name)
    try:
        os.makedirs(path, exist_ok=False)
    except OSError as exc:
        raise RunError(f"cannot create the dedicated cgroup: {exc.strerror}") from None
    _enable_controllers(path)
    return os.path.relpath(path, root), path


def _wait_populated_empty(path, deadline_seconds=5):
    deadline = time.monotonic() + deadline_seconds
    while time.monotonic() < deadline:
        try:
            with open(os.path.join(path, "cgroup.events"), encoding="ascii") as handle:
                events = handle.read()
        except OSError as exc:
            raise RunError(f"cannot read cgroup.events: {exc.strerror}") from None
        if "populated 0" in events:
            return
        time.sleep(0.05)
    raise RunError("the cgroup did not become empty before the deadline")


def _read_final_counters(path):
    _wait_populated_empty(path)
    cpu = None
    try:
        with open(os.path.join(path, "cpu.stat"), encoding="ascii") as handle:
            for line in handle:
                if line.startswith("usage_usec "):
                    cpu = int(line.split()[1])
    except OSError:
        cpu = None
    if cpu is None:
        raise RunError("the final cpu.stat counter is missing")
    try:
        with open(os.path.join(path, "memory.peak"), encoding="ascii") as handle:
            memory = int(handle.read().strip())
    except OSError:
        raise RunError("the final memory.peak counter is missing") from None
    return cpu, memory


def isolation_subset(inspect, policy):
    host = inspect["HostConfig"]
    config = inspect["Config"]
    container_env = {}
    for item in config.get("Env") or []:
        if "=" in item:
            key, value = item.split("=", 1)
            container_env[key] = value
    declared = {item["destination"]: item for item in policy["isolation"]["mounts"]}
    mounts = []
    seen = set()
    for mount in inspect.get("Mounts", []):
        destination = mount.get("Destination")
        if destination not in declared:
            raise RunError(f"unexpected mount in the container: {destination!r}")
        seen.add(destination)
        mounts.append({"role": declared[destination]["role"], "destination": destination,
                       "read_only": not mount.get("RW", True)})
    if seen != set(declared):
        raise RunError("the container is missing one of the declared mounts")
    return {
        "network_mode": host.get("NetworkMode"),
        "readonly_rootfs": host.get("ReadonlyRootfs"),
        "cap_drop": sorted(host.get("CapDrop") or []),
        "security_opt": sorted(host.get("SecurityOpt") or []),
        "privileged": host.get("Privileged"),
        "user": config.get("User"),
        "memory": host.get("Memory"),
        "memory_swap": host.get("MemorySwap"),
        "pids_limit": host.get("PidsLimit"),
        "nano_cpus": host.get("NanoCpus"),
        "tmpfs": host.get("Tmpfs") or {},
        "ulimits": sorted(({"name": item.get("Name"), "soft": item.get("Soft"), "hard": item.get("Hard")}
                           for item in (host.get("Ulimits") or [])), key=lambda item: item["name"]),
        "mounts": sorted(mounts, key=lambda item: item["role"]),
        "env": {name: container_env[name] for name in sorted(container_env)},
    }


def _run_inner(spec, policy, input_dir, out_dir, wall_seconds, name, cgroup_parent_name, cgroup_path):
    image = spec["image_ref"]
    _require(image.startswith("ghcr.io/") or image.startswith("docker.io/"), "the image is not pinned by a registry digest")
    _require("@sha256:" in image, "the image is not pinned by digest")
    subprocess.run(["docker", "pull", image], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    pulled = subprocess.run(["docker", "image", "inspect", image], check=True,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    inspect_image = json.loads(pulled.stdout)[0]
    image_id = inspect_image["Id"]
    wanted = image.split("@", 1)[1]
    if wanted not in (inspect_image.get("RepoDigests") or []):
        # RepoDigests entries are repo@sha256:..., compare the digest suffix.
        digests = {item.rsplit("@", 1)[-1] for item in inspect_image.get("RepoDigests") or []}
        _require(wanted in digests, "the pulled image digest differs from the pinned digest")

    command = docker_command(spec, policy, input_dir, out_dir, name, cgroup_parent_name)
    started = time.monotonic()
    started_at = _now()
    process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    out_chunks, err_chunks = [], []
    truncated = {}
    out_thread = threading.Thread(target=lambda: truncated.__setitem__(
        "stdout", _read_limited(process.stdout, limits.MAX_STDOUT_BYTES, out_chunks)))
    err_thread = threading.Thread(target=lambda: truncated.__setitem__(
        "stderr", _read_limited(process.stderr, limits.MAX_STDERR_BYTES, err_chunks)))
    out_thread.start()
    err_thread.start()
    limit_hit = []
    timed_out = False
    try:
        process.wait(timeout=wall_seconds)
    except subprocess.TimeoutExpired:
        timed_out = True
        limit_hit.append("wall")
        subprocess.run(["docker", "kill", name], check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        process.wait(timeout=5)
    _join_or_fail(out_thread, "stdout")
    _join_or_fail(err_thread, "stderr")
    cpu_usec, max_rss = _read_final_counters(cgroup_path)
    wall_ms = int((time.monotonic() - started) * 1000)
    ended_at = _now()
    if truncated.get("stdout"):
        limit_hit.append("stdout")
    if truncated.get("stderr"):
        limit_hit.append("stderr")
    inspect = _inspect(name)
    entrypoint = inspect["Config"].get("Entrypoint") or []
    cmd = inspect["Config"].get("Cmd") or []
    if entrypoint:
        raise RunError("the image entrypoint is not empty; the observed argv would not be the spec argv")
    if cmd != spec["argv"]:
        raise RunError("the observed container command differs from the spec argv")
    container_env = {}
    for item in inspect["Config"].get("Env") or []:
        if "=" in item:
            key, value = item.split("=", 1)
            container_env[key] = value
    if container_env != policy["env"]:
        raise RunError("the container environment is not exactly the pinned name -> value set")
    if spec["config"].get("env") != policy["env"]:
        raise RunError("the spec environment is not exactly the pinned name -> value set")
    observed_config = {"env": {key: container_env[key] for key in sorted(container_env)},
                       "settings": spec["config"].get("settings", {})}
    if inspect["Image"] != image_id:
        raise RunError("the container image id differs from the pulled image id")
    stdout = b"".join(out_chunks)
    stderr = b"".join(err_chunks)
    isolation = isolation_subset(inspect, policy)
    return {
        "exit_code": 124 if timed_out else int(inspect["State"]["ExitCode"]),
        "signal": "SIGKILL" if (timed_out or inspect["State"].get("OOMKilled")) else None,
        "started_at": started_at,
        "ended_at": ended_at,
        "wall_ms": wall_ms,
        "cpu_usec": cpu_usec,
        "max_rss_bytes": max_rss,
        "stdout_sha256": _sha(stdout),
        "stderr_sha256": _sha(stderr),
        "limit_hit": sorted(set(limit_hit)),
        "isolation": isolation,
        "image_id": inspect["Image"],
        "image_ref_digest": wanted,
        "argv": cmd,
        "config_digest": canon.sha256_canonical(observed_config),
        "stdout_bytes": len(stdout),
        "stderr_bytes": len(stderr),
    }


def _run(spec, policy, input_dir, out_dir, wall_seconds=10):
    try:
        workflow_guard.require_mandate()
    except workflow_guard.Refused as exc:
        raise RunError(f"no registered test mandate: {exc}") from None
    name = f"ci-fixture-{os.getpid()}"
    cgroup_name, cgroup_path = _create_cgroup(policy["cgroup_name_prefix"],
                                              policy["cgroup_parent_dir"], policy["cgroup_root"])
    clean = True
    try:
        return _run_inner(spec, policy, input_dir, out_dir, wall_seconds, name, cgroup_name, cgroup_path)
    except BaseException:
        clean = False
        raise
    finally:
        subprocess.run(["docker", "kill", name], check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        removed = subprocess.run(["docker", "rm", "-f", name], stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        try:
            os.rmdir(cgroup_path)
        except OSError:
            if clean:
                raise RunError("the dedicated cgroup could not be removed")
        if clean and removed.returncode != 0:
            raise RunError("the container could not be removed; refusing to pack")


def run(spec_path, policy_path, input_dir, out_dir, raw_path, observation_path, wall_seconds=10, dry_run=False):
    if not dry_run:
        try:
            workflow_guard.require_mandate()
        except workflow_guard.Refused as exc:
            raise RunError(f"no registered test mandate: {exc}") from None
    with open(spec_path, "rb") as handle:
        spec = canon.loads(handle.read())
    with open(policy_path, "rb") as handle:
        policy = canon.loads(handle.read())
    _require(policy["image"]["ref"] == spec["image_ref"], "the spec image differs from the policy image")
    _require(spec["image_ref"], "the spec has no pinned image")
    if dry_run:
        print(json.dumps({"command": docker_command(spec, policy, input_dir, out_dir, "ci-fixture-dry-run"),
                          "isolation": policy["isolation"]}, sort_keys=True))
        return 0
    if not os.path.isdir(input_dir) or not os.path.isdir(out_dir):
        raise RunError("input and output directories must exist")
    marker = os.environ.get("CI_AUTH_MARKER", "ci-artifacts/run-authorized.json")
    os.makedirs(os.path.dirname(marker) or ".", exist_ok=True)
    with open(marker, "w", encoding="utf-8") as handle:
        json.dump({"label": labels.run_label()}, handle, sort_keys=True)
        handle.write("\n")
    from .steps import check_image_tools
    tool_probes = check_image_tools(policy_path)
    image_tools = tool_probes["versions"]
    if os.listdir(out_dir):
        raise RunError("the output tmpfs must be empty before the run")
    observed = _run(spec, policy, input_dir, out_dir, wall_seconds)
    files = archive.walk_out(out_dir)
    raw = archive.pack_tar(files)
    with open(raw_path, "wb") as handle:
        handle.write(raw)
    inputs = []
    for item in spec["inputs"]:
        source = os.path.join(input_dir, item["path"])
        with open(source, "rb") as handle:
            data = handle.read()
        inputs.append({"role": item["role"], "path": item["path"], "sha256": _sha(data), "bytes": len(data)})
    observation = {
        "image_id": observed["image_id"],
        "image_ref_digest": observed["image_ref_digest"],
        "isolation": observed["isolation"],
        "isolation_sha256": canon.sha256_canonical(observed["isolation"]),
        "inputs": sorted(inputs, key=lambda item: item["path"]),
        "inputs_sha256": canon.sha256_canonical(sorted(inputs, key=lambda item: item["path"])),
        "argv": observed["argv"],
        "config_digest": observed["config_digest"],
        "exit_code": observed["exit_code"],
        "signal": observed["signal"],
        "started_at": observed["started_at"],
        "ended_at": observed["ended_at"],
        "wall_ms": observed["wall_ms"],
        "cpu_usec": observed["cpu_usec"],
        "max_rss_bytes": observed["max_rss_bytes"],
        "stdout_sha256": observed["stdout_sha256"],
        "stderr_sha256": observed["stderr_sha256"],
        "stdout_bytes": observed["stdout_bytes"],
        "stderr_bytes": observed["stderr_bytes"],
        "limit_hit": observed["limit_hit"],
        "sealed_output_sha256": fileset_digest(files),
        "image_tools": image_tools,
        "tool_probes": tool_probes["probes"],
    }
    data = canon.dumps(observation)
    if len(data) > limits.MAX_OBSERVATION_BYTES:
        raise RunError("observation.json exceeds its cap")
    with open(observation_path, "wb") as handle:
        handle.write(data)
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(prog="ci.run_fixture")
    parser.add_argument("--spec", required=True)
    parser.add_argument("--policy", default="ci/policy.json")
    parser.add_argument("--input", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--raw", required=True)
    parser.add_argument("--observation", required=True)
    parser.add_argument("--wall-seconds", type=int, default=10)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    try:
        return run(args.spec, args.policy, args.input, args.out, args.raw, args.observation,
                   args.wall_seconds, args.dry_run)
    except (RunError, OSError, subprocess.CalledProcessError, ValueError) as exc:
        print(f"ci.run_fixture: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
