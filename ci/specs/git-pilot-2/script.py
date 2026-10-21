# SPDX-License-Identifier: AGPL-3.0-or-later
"""Build the three synthetic git-pilot-2 repositories and record the raw pilot cases.

The fixture contains no question text, options, answers, feedback or records: three tiny repositories and the exact
stdout/stderr/exit records for a diff, a fast-forward merge and a revert. All identities and dates are fixed; no remote,
no conflict, no merge commit, no hooks, no GPG and no host configuration. Repositories live under a temporary directory
with an empty template and are removed after each case, so the inode quota of ``/tmp`` is never exhausted.
"""
import json
import os
import pathlib
import shutil
import subprocess

INPUT = pathlib.Path(os.environ.get("CI_PILOT_INPUT", "/input"))
OUT = pathlib.Path(os.environ.get("CI_PILOT_OUT", "/out"))
REPOS = pathlib.Path(os.environ.get("CI_PILOT_TMP", "/tmp/git-pilot-2"))
GIT = "/usr/bin/git"
BASE = 1700000000
FIXTURE = json.loads((INPUT / "fixture.json").read_text(encoding="utf-8"))
AUTHOR = FIXTURE["author"]
MESSAGES = FIXTURE["messages"]
ENV = {
    "PATH": "/usr/local/bin:/usr/local/sbin:/usr/bin:/bin",
    "HOME": "/tmp",
    "LC_ALL": "C",
    "GIT_PAGER": "cat",
    "GIT_CONFIG_NOSYSTEM": "1",
    "GIT_CONFIG_GLOBAL": "/dev/null",
    "GIT_AUTHOR_NAME": AUTHOR["name"],
    "GIT_AUTHOR_EMAIL": AUTHOR["email"],
    "GIT_COMMITTER_NAME": AUTHOR["name"],
    "GIT_COMMITTER_EMAIL": AUTHOR["email"],
}


def _env(offset):
    value = dict(ENV)
    stamp = f"@{BASE + offset} +0000"
    value["GIT_AUTHOR_DATE"] = stamp
    value["GIT_COMMITTER_DATE"] = stamp
    return value


def _git(repo, args, offset=0):
    command = [GIT, "-C", str(repo), "-c", "core.abbrev=7", "-c", "core.autocrlf=false",
               "-c", "core.hooksPath=/dev/null", *args]
    return command, subprocess.run(command, env=_env(offset), text=True,
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE)


def _checked(repo, args, offset=0):
    command, completed = _git(repo, args, offset)
    if completed.returncode != 0:
        raise SystemExit(f"setup command failed ({completed.returncode}): {' '.join(command)}\n{completed.stderr}")
    return completed


def _entries(repo, limit=60):
    count = sum(1 for _ in repo.rglob("*"))
    if count > limit:
        raise SystemExit(f"repository has {count} entries, above the inode budget {limit}")


def _write(repo, path, text):
    target = repo / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8", newline="")


def _save(case, index, command, completed, records):
    directory = OUT / case
    directory.mkdir(parents=True, exist_ok=True)
    stem = f"{index:02d}"
    (directory / f"{stem}.stdout").write_text(completed.stdout, encoding="utf-8", newline="")
    (directory / f"{stem}.stderr").write_text(completed.stderr, encoding="utf-8", newline="")
    records.append({"index": index, "command": " ".join(command),
                    "stdout": f"{stem}.stdout", "stderr": f"{stem}.stderr",
                    "exit": completed.returncode, "expected_exit": 0})


def _cat(case, index, repo, path, records):
    command = ["cat", str(repo / path)]
    completed = subprocess.CompletedProcess(command, 0, (repo / path).read_text(encoding="utf-8"), "")
    _save(case, index, command, completed, records)


def _init(case):
    repo = REPOS / case
    if repo.exists():
        shutil.rmtree(repo)
    repo.mkdir(parents=True)
    _checked(repo, ["init", "-q", "-b", "main", f"--template={REPOS / 'template'}"])
    _entries(repo)
    return repo


def _commit(repo, offset, message):
    _checked(repo, ["commit", "-q", "-m", message], offset)
    _entries(repo)
    return _checked(repo, ["rev-parse", "HEAD"]).stdout.strip()


def _meta(case, records):
    version = subprocess.run([GIT, "--version"], env=ENV, text=True,
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE).stdout.strip()
    data = {"case": case, "git_version": version, "records": records}
    (OUT / case / "meta.json").write_text(json.dumps(data, indent=2, sort_keys=True) + "\n",
                                          encoding="utf-8", newline="")


def case_one():
    case = "case1"
    repo = _init(case)
    try:
        _write(repo, "list.txt", "milk\neggs\nbread\nsalt\ntea\n")
        _checked(repo, ["add", "list.txt"])
        _commit(repo, 0, MESSAGES["case1_initial"])
        _write(repo, "list.txt", "butter\nbread\nsalt\ntea\n")
        _checked(repo, ["add", "list.txt"])
        _commit(repo, 60, MESSAGES["case1_change"])
        records = []
        command, completed = _git(repo, ["diff", "--no-color", "--no-ext-diff", "-U3",
                                         "HEAD~1", "HEAD", "--", "list.txt"], 120)
        if completed.returncode != 0:
            raise SystemExit(completed.stderr)
        _save(case, 1, command, completed, records)
        _meta(case, records)
    finally:
        shutil.rmtree(repo, ignore_errors=True)


def case_two():
    case = "case2"
    repo = _init(case)
    try:
        lines, hashes = [], {}
        for index, letter in enumerate("ABCDE"):
            lines.append(letter)
            _write(repo, "log.txt", "\n".join(lines) + "\n")
            _checked(repo, ["add", "log.txt"])
            hashes[letter] = _commit(repo, 100 + index * 60, MESSAGES["case2"][index])
        _checked(repo, ["checkout", "-q", "--detach", hashes["E"]])
        _checked(repo, ["branch", "feature", hashes["E"]])
        _checked(repo, ["branch", "-f", "main", hashes["C"]])
        _checked(repo, ["checkout", "-q", "main"])
        records = []
        for index, args in enumerate((["log", "--oneline", "--decorate", "--graph", "--all"],
                                      ["merge", "feature"],
                                      ["log", "--oneline", "--decorate", "--graph", "--all"],
                                      ["rev-parse", "main", "feature"]), start=1):
            command, completed = _git(repo, args, 500)
            if completed.returncode != 0:
                raise SystemExit(completed.stderr)
            _save(case, index, command, completed, records)
        _meta(case, records)
    finally:
        shutil.rmtree(repo, ignore_errors=True)


def case_three():
    case = "case3"
    repo = _init(case)
    try:
        _write(repo, "README.txt", "fixture\n")
        _checked(repo, ["add", "README.txt"])
        _commit(repo, 400, MESSAGES["case3"][0])
        _write(repo, "total.txt", "total = 3\n")
        _checked(repo, ["add", "total.txt"])
        _commit(repo, 460, MESSAGES["case3"][1])
        _write(repo, "total.txt", "total = 5\n")
        _checked(repo, ["add", "total.txt"])
        c_sha = _commit(repo, 520, MESSAGES["case3"][2])
        records = []
        command, completed = _git(repo, ["log", "--oneline", "--decorate"], 600)
        _save(case, 1, command, completed, records)
        _cat(case, 2, repo, "total.txt", records)
        command, completed = _git(repo, ["revert", "--no-edit", c_sha], 640)
        if completed.returncode != 0:
            raise SystemExit(completed.stderr)
        _save(case, 3, command, completed, records)
        command, completed = _git(repo, ["log", "--oneline", "--decorate"], 700)
        _save(case, 4, command, completed, records)
        _cat(case, 5, repo, "total.txt", records)
        command, completed = _git(repo, ["show", "--stat", "--format=%B", "HEAD"], 760)
        _save(case, 6, command, completed, records)
        _meta(case, records)
    finally:
        shutil.rmtree(repo, ignore_errors=True)


def main():
    REPOS.mkdir(parents=True, exist_ok=True)
    (REPOS / "template").mkdir(exist_ok=True)
    case_one()
    case_two()
    case_three()


if __name__ == "__main__":
    main()
