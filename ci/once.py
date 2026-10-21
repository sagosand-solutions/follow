# SPDX-License-Identifier: AGPL-3.0-or-later
"""Single-use enforcement for the first CI test mandate.

The guard cannot know whether a previous dispatch already consumed the mandate, so this step asks the Actions API for
the runs of this workflow at the reviewed commit. It refuses when the run attempt is not 1 or when any run other than
the current one exists. The token stays in the trusted step; the fixture container never sees it.
"""
from __future__ import annotations

import json
import os
import sys
import urllib.request

from . import workflow_guard

API = "https://api.github.com"
WORKFLOW = "ci-fixture.yml"


class OnceError(RuntimeError):
    pass


def _page(repo, workflow, commit, token, page):
    url = (f"{API}/repos/{repo}/actions/workflows/{workflow}/runs"
           f"?head_sha={commit}&per_page=100&page={page}")
    request = urllib.request.Request(url, headers={
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "follow-ci-once",
    })
    with urllib.request.urlopen(request, timeout=30) as response:
        payload = json.loads(response.read(2 * 1024 * 1024))
    return payload.get("workflow_runs", []), int(payload.get("total_count", 0))


def _paginate(fetch_page, max_pages=10):
    runs = []
    total = None
    for page in range(1, max_pages + 1):
        batch, reported = fetch_page(page)
        if total is None:
            total = reported
        elif reported != total:
            raise OnceError("total_count changed during pagination")
        runs.extend(batch)
        if len(runs) >= total:
            break
        if not batch:
            raise OnceError("pagination ended before total_count")
    if total is None or len(runs) != total:
        raise OnceError("pagination did not cover total_count")
    return runs


def _fetch_runs(repo, workflow, commit, token):
    return _paginate(lambda page: _page(repo, workflow, commit, token, page))


def check_once(repo, workflow, commit, run_id, attempt, token, fetch=None):
    if str(attempt) != "1":
        raise OnceError("run_attempt must be 1 for the first test mandate")
    fetch = fetch or _fetch_runs
    runs = fetch(repo, workflow, commit, token)
    if not runs:
        raise OnceError("the current run is not visible through the API")
    if any(int(item["id"]) != int(run_id) for item in runs):
        raise OnceError("a previous run exists for this workflow and commit")
    if len(runs) != 1:
        raise OnceError("more than one run exists for this workflow and commit")
    return True


def main(argv=None):
    try:
        workflow_guard.require_mandate()
    except workflow_guard.Refused as exc:
        print(f"ci.once refused: {exc}", file=sys.stderr)
        return 2
    values = {name: os.environ.get(name) for name in
              ("GITHUB_REPOSITORY", "GITHUB_RUN_ID", "GITHUB_RUN_ATTEMPT", "GITHUB_SHA", "GH_TOKEN")}
    missing = [name for name, value in values.items() if not value]
    if missing:
        print(f"ci.once: missing {', '.join(missing)}", file=sys.stderr)
        return 2
    try:
        check_once(values["GITHUB_REPOSITORY"], WORKFLOW, values["GITHUB_SHA"],
                   values["GITHUB_RUN_ID"], values["GITHUB_RUN_ATTEMPT"], values["GH_TOKEN"])
    except (OnceError, OSError, ValueError) as exc:
        print(f"ci.once refused: {exc}", file=sys.stderr)
        return 1
    print("ci.once: single first run confirmed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
