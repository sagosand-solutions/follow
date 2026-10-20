# SPDX-License-Identifier: MIT
"""Text artifacts of a 0.3 question: typed display text that is never executed and never traced.

The schema fixes the shape; this module adds what a schema cannot say: the exact hash, the fixture binding, line limits,
the URL policy, and the id rule against snippets. Pure functions, no process, no network, no file reads."""
import hashlib
import ipaddress
import re

MAX_LINES = 80
# `scheme://authority`; the authority ends at whitespace, a path/query/fragment or a quote.
_URL = re.compile(r"[A-Za-z][A-Za-z0-9+.\-]*://([^\s/?#\"'<>]*)")
_RESERVED_SUFFIXES = ("example.com", "example.net", "example.org", "example", "test", "invalid")
_DOC_NETS = tuple(ipaddress.ip_network(n) for n in ("192.0.2.0/24", "198.51.100.0/24", "203.0.113.0/24"))


def artifact_sha256(text):
    """SHA-256 over the UTF-8 bytes of the text exactly as stored. Raises UnicodeEncodeError for a lone surrogate."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def line_count(text):
    return text.count("\n") + (0 if text == "" or text.endswith("\n") else 1)


def _reserved_host(authority):
    """Only names that can never reach a real site: the documentation domains and TLDs, localhost, loopback, TEST-NETs."""
    if "@" in authority or authority.startswith("["):
        return False
    host = authority.rstrip(".,;:)]}")
    if host.count(":") == 1 and host.rsplit(":", 1)[1].isdigit():
        host = host.rsplit(":", 1)[0]
    host = host.lower().rstrip(".")
    if not host:
        return False
    if host == "localhost":
        return True
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return any(host == s or host.endswith("." + s) for s in _RESERVED_SUFFIXES)
    return address == ipaddress.ip_address("127.0.0.1") or any(address in net for net in _DOC_NETS)


def url_findings(text):
    """Authorities in `text` that are not reserved test hosts (empty when the text names no URL or only safe ones)."""
    return [m.group(1) for m in _URL.finditer(text) if not _reserved_host(m.group(1))]


def artifact_issues(question):
    """Semantic findings for the artifacts of a question the schema already accepted."""
    from .core import Issue
    issues = []
    snippet_ids = {s["id"] for s in question.get("snippets", [])}
    seen = set()
    for i, item in enumerate(question.get("artifacts", [])):
        path = f"/artifacts/{i}"
        text = item["text"]
        if item["id"] in snippet_ids:
            issues.append(Issue("artifact-id", path + "/id", f"Artifact id {item['id']!r} is also a snippet id"))
        if item["id"] in seen:
            issues.append(Issue("artifact-id", path + "/id", f"Artifact id {item['id']!r} is used twice"))
        seen.add(item["id"])
        try:
            actual = artifact_sha256(text)
        except UnicodeEncodeError:
            issues.append(Issue("artifact-text", path + "/text", "Text is not valid Unicode (a lone surrogate cannot be encoded as UTF-8)"))
            actual = None
        if actual is not None:
            if item["sha256"] != actual:
                issues.append(Issue("artifact-hash", path + "/sha256", "sha256 is not the hash of the UTF-8 bytes of text"))
            if item["provenance"]["fixture_output_sha256"] != actual:
                issues.append(Issue("artifact-binding", path + "/provenance/fixture_output_sha256", "The fixture output hash is not the hash of the shown text"))
        if line_count(text) > MAX_LINES:
            issues.append(Issue("artifact-limits", path + "/text", f"At most {MAX_LINES} lines"))
        for authority in url_findings(text):
            issues.append(Issue("artifact-url", path + "/text", f"A URL may only name a reserved test host, not {authority!r}"))
    return issues


def verify_artifact_fixture(question, artifact_id, fixture_bytes):
    """Authoring-harness check: do the bytes a fixture produced still match what the question recorded for this artifact?"""
    from .core import Issue
    matches = [(i, item) for i, item in enumerate(question.get("artifacts", [])) if item.get("id") == artifact_id]
    if not matches:
        return [Issue("artifact-id", "/artifacts", f"No artifact {artifact_id!r}")]
    if len(matches) > 1:                                # never verify "the first one" of an ambiguous id
        return [Issue("artifact-id", f"/artifacts/{matches[1][0]}/id", f"Artifact id {artifact_id!r} is used more than once")]
    i, item = matches[0]
    if hashlib.sha256(fixture_bytes).hexdigest() != item["provenance"]["fixture_output_sha256"]:
        return [Issue("artifact-binding", f"/artifacts/{i}/provenance/fixture_output_sha256", "The fixture output changed since the artifact was recorded")]
    return []
