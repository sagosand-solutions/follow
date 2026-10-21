# SPDX-License-Identifier: AGPL-3.0-or-later
"""Bounded fetch of one Actions artifact through the API.

``actions/download-artifact`` extracts a zip with no byte budget, so the trusted verifier does not use it: this module
asks the API for the artifact metadata, enforces the compressed cap, streams the zip with a hard limit, and only then
compares the computed sha256 with the API's ``digest``. Nothing is extracted here; ``ci.archive.parse_zip`` does that
under its own caps.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request

from . import workflow_guard

API = "https://api.github.com"


class FetchError(RuntimeError):
    pass


class _SafeRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        new = super().redirect_request(req, fp, code, msg, headers, newurl)
        if new is not None and urllib.parse.urlsplit(newurl).netloc != urllib.parse.urlsplit(req.full_url).netloc:
            new.remove_header("Authorization")
        return new


_OPENER = urllib.request.build_opener(_SafeRedirect)


def _request(url, token, accept="application/vnd.github+json"):
    request = urllib.request.Request(url, headers={
        "Authorization": f"Bearer {token}",
        "Accept": accept,
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "follow-ci-fixture",
    })
    return _OPENER.open(request, timeout=60)


def find_artifact(repo, run_id, name, token):
    url = f"{API}/repos/{repo}/actions/runs/{run_id}/artifacts?name={name}&per_page=100"
    with _request(url, token) as response:
        payload = json.loads(response.read(4 * 1024 * 1024))
    matches = [item for item in payload.get("artifacts", []) if item.get("name") == name]
    if len(matches) != 1:
        raise FetchError(f"expected exactly one artifact named {name!r}, found {len(matches)}")
    artifact = matches[0]
    if artifact.get("expired"):
        raise FetchError("the artifact has expired")
    return artifact


def fetch(repo, run_id, name, token, out_path, max_bytes):
    try:
        workflow_guard.require_mandate()
    except workflow_guard.Refused as exc:
        raise FetchError(f"no registered test mandate: {exc}") from None
    artifact = find_artifact(repo, run_id, name, token)
    if artifact["size_in_bytes"] > max_bytes:
        raise FetchError("the artifact is larger than the compressed cap")
    digest = artifact.get("digest", "")
    if not digest.startswith("sha256:"):
        raise FetchError("the API did not provide a sha256 digest")
    expected_prefix = f"https://api.github.com/repos/{repo}/actions/artifacts/{artifact['id']}/zip"
    if artifact["archive_download_url"] != expected_prefix:
        raise FetchError("the artifact download URL is not the expected API URL")
    hasher = hashlib.sha256()
    total = 0
    url = artifact["archive_download_url"]
    with _request(url, token) as response, open(out_path, "wb") as handle:
        while True:
            chunk = response.read(min(65536, max_bytes + 1 - total))
            if not chunk:
                break
            total += len(chunk)
            if total > max_bytes:
                raise FetchError("the artifact stream exceeded the compressed cap")
            hasher.update(chunk)
            handle.write(chunk)
    if total != artifact["size_in_bytes"]:
        raise FetchError("the artifact size differs from the API metadata")
    if hasher.hexdigest() != digest.split(":", 1)[1]:
        raise FetchError("the artifact digest does not match the API metadata")
    return {"name": name, "bytes": total, "sha256": hasher.hexdigest()}


def main(argv=None):
    parser = argparse.ArgumentParser(prog="ci.fetch_artifact")
    parser.add_argument("--repo", default=os.environ.get("GITHUB_REPOSITORY"))
    parser.add_argument("--run-id", default=os.environ.get("GITHUB_RUN_ID"))
    parser.add_argument("--name", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--max-bytes", type=int, default=8 * 1024 * 1024)
    parser.add_argument("--token-env", default="GH_TOKEN")
    args = parser.parse_args(argv)
    token = os.environ.get(args.token_env) or os.environ.get("GITHUB_TOKEN")
    if not token:
        print("ci.fetch_artifact: no token", file=sys.stderr)
        return 2
    try:
        info = fetch(args.repo, args.run_id, args.name, token, args.out, args.max_bytes)
    except (FetchError, urllib.error.URLError, OSError, ValueError) as exc:
        print(f"ci.fetch_artifact: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(info, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
