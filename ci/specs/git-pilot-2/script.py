# SPDX-License-Identifier: AGPL-3.0-or-later
"""Build the synthetic git-pilot-2 repository and render the three pilot Git cases.

The fixture contains no question text, options, answers, feedback or records: only a tiny synthetic repository and the
deterministic Git output for (1) a hunk header, (2) a fast-forward merge and (3) a revert commit. Dates, author and
committer are fixed, and ``GIT_CONFIG_NOSYSTEM`` is set so no host configuration is read.
"""
import json
import pathlib
import subprocess


def main():
    fixture = json.loads(pathlib.Path("/input/fixture.json").read_text(encoding="utf-8"))
    author = fixture["author"]
    env = {
        "PATH": "/usr/local/bin:/usr/local/sbin:/usr/bin:/bin",
        "HOME": "/tmp",
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": "/dev/null",
        "GIT_AUTHOR_NAME": author["name"],
        "GIT_AUTHOR_EMAIL": author["email"],
        "GIT_COMMITTER_NAME": author["name"],
        "GIT_COMMITTER_EMAIL": author["email"],
        "GIT_AUTHOR_DATE": "@0 +0000",
        "GIT_COMMITTER_DATE": "@0 +0000",
    }
    repo = pathlib.Path("/out/repo")
    repo.mkdir(parents=True, exist_ok=True)

    def git(*args):
        return subprocess.run(["/usr/bin/git", "-C", str(repo), *args], env=env, check=True,
                              text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE).stdout

    def write(path, content):
        (repo / path).write_text(content, encoding="utf-8", newline="")

    git("init", "-q", "-b", "main")
    first, second = fixture["commits"][0], fixture["commits"][1]
    write(first["path"], first["content"])
    git("add", first["path"])
    git("commit", "-q", "-m", first["message"])
    write(second["path"], second["content"])
    git("add", second["path"])
    git("commit", "-q", "-m", second["message"])
    hunk = git("diff", "HEAD~1", "HEAD")

    git("checkout", "-q", "-b", fixture["branch"])
    write("feature.txt", "feature\n")
    git("add", "feature.txt")
    git("commit", "-q", "-m", "feature")
    git("checkout", "-q", "main")
    git("merge", "-q", "--ff-only", fixture["branch"])
    fast_forward = git("log", "--oneline", "--graph", "--decorate", "-3")

    git("revert", "--no-edit", "HEAD~1")
    revert = git("log", "--oneline", "-3") + git("show", "--stat", "--oneline", "HEAD")

    output = ("=== hunk ===\n" + hunk + "=== fast-forward ===\n" + fast_forward
              + "=== revert ===\n" + revert)
    pathlib.Path("/out/git-pilot-2.txt").write_text(output, encoding="utf-8", newline="")


if __name__ == "__main__":
    main()
