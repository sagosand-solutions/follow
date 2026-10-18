# SPDX-License-Identifier: AGPL-3.0-or-later
"""Synthetic scoring pool and seeded test accounts for ``python -m server.dev --scoring`` (development only).

Everything here is invented on the spot and labelled as such: no real question, no public-bank question and no real account or
key is ever used. The "protected" pools are throwaway files in a temporary directory behind private, hash-checked manifests, so the
dev runner exercises the same private-source path as a real deployment. Session cookies are minted for the throwaway database
only. This module is never imported by the production code path.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from .content_catalog import ContentCatalog
from .models import Product
from .repositories import Repositories
from .scoring_catalog import parse_catalog
from .scoring_service import ManifestQuestionSource, ScoringServices
from .sessions import SessionService

LABEL = "SYNTHETIC TEST QUESTION - not real content"
PRODUCT = "dev-product"
FREE_PRODUCT = "dev-free-pool"
READERS = ("dev-reader-1", "dev-reader-2", "dev-reader-3")
OFFICIAL_COURSES = (("dev-intro", 6), ("dev-flow", 4))
FREE_COURSES = (("dev-free", 6),)
CATALOG_VERSION = "dev-synthetic-1"


def _question(question_id: str, family: str) -> bytes:
    options = [{"id": f"{question_id}-right", "text": "Right answer (synthetic)", "score": 1, "feedback": "Synthetic feedback.", "misconception": None},
               {"id": f"{question_id}-half", "text": "Half answer (synthetic)", "score": 0.5, "feedback": "Synthetic feedback.",
                "misconception": "dev.half", "partial_reason": "Synthetic reason."}]
    options += [{"id": f"{question_id}-wrong{i}", "text": f"Wrong answer {i} (synthetic)", "score": 0, "feedback": "Synthetic feedback.",
                 "misconception": "dev.wrong"} for i in range(3)]
    document = {"schema_version": "0.1", "id": question_id, "language": "python", "locale": "en", "type": "crashing-input", "level": "beginner",
                "difficulty": 0.5, "concepts": ["dev.synthetic"], "prompt": f"[{LABEL}] Which answer would a {family} reader pick?",
                "snippets": [{"id": "main", "code": "print('synthetic')\n", "highlight_lines": [1]}], "options": options,
                "explanation": f"{LABEL}.", "verification": {"status": "specified", "cases": []},
                "author": {"name": "Synthetic author"}, "license": "LicenseRef-Proprietary"}
    return json.dumps(document, sort_keys=True).encode("utf-8")


def _pool(root: Path, family: str, courses, product: str | None) -> tuple[list, list, list, list]:
    """Write the synthetic files and return (manifest entries, catalog courses, exposure rows, ids)."""
    entries, catalog_courses, exposures, ids = [], [], [], []
    for course, count in courses:
        items = []
        for i in range(count):
            question_id = f"dev-{family[0]}-{course[4:]}-{i + 1:02d}"
            raw = _question(question_id, family)
            (root / f"{question_id}.txt").write_bytes(raw)
            digest = hashlib.sha256(raw).hexdigest()
            entries.append({"kind": "question", "id": question_id, "product_id": product or FREE_PRODUCT, "path": f"{question_id}.txt", "sha256": digest})
            items.append({"question_id": question_id, "product_id": product, "content_sha256": digest})
            exposures.append({"kind": "question", "id": question_id, "content_sha256": digest, "affected_question_ids": [question_id]})
            ids.append(question_id)
        catalog_courses.append({"course_id": course, "N_c": count, "questions": items})
    return entries, catalog_courses, exposures, ids


def write_pools(directory: Path) -> dict:
    """Create the two private pools under ``directory`` and return their roots, manifests and ids."""
    paths = {}
    for family, courses, product in (("official", OFFICIAL_COURSES, PRODUCT), ("free", FREE_COURSES, None)):
        root = directory / family
        root.mkdir(parents=True, exist_ok=True)
        entries, catalog_courses, exposures, ids = _pool(root, family, courses, product)
        manifest = root / "manifest.json"
        manifest.write_text(json.dumps({"entries": entries}), encoding="utf-8")
        paths[family] = {"root": root, "manifest": manifest, "courses": catalog_courses, "exposures": exposures, "ids": ids}
    return paths


def build_services(paths: dict, clock):
    """(ScoringServices, official ContentCatalog) over the synthetic pools; the free pool uses its private manifest."""
    official = ContentCatalog.load(paths["official"]["root"], paths["official"]["manifest"])
    free = ContentCatalog.load(paths["free"]["root"], paths["free"]["manifest"])
    catalogs = {family: parse_catalog({"schema_version": 1, "catalog_version": CATALOG_VERSION, "rules_version": "r2", "family": family,
                                       "courses": paths[family]["courses"], "exposures": paths[family]["exposures"]},
                                      expected_family=family, content_catalog=official if family == "official" else None)
                for family in ("official", "free")}
    return ScoringServices(catalogs, ManifestQuestionSource(official, free_catalog=free)), official


def seed_accounts(database, settings, clock) -> list:
    """Create the throwaway readers (idempotent), grant the dev product to each, and mint one session per reader.

    Returns (user id, cookie name, cookie value, csrf token) rows. The sessions work against this database only."""
    from . import http
    from .config import AppEnv
    from .dev import DevRefused
    if settings.app_env is AppEnv.PRODUCTION:       # no test account or session is ever minted with production settings
        raise DevRefused("dev_scoring.seed_accounts refuses production settings")
    repos = Repositories(database)
    repos.products.upsert(Product(PRODUCT, "Synthetic dev product (test only)", "USD", 500, "dev", True))
    sessions = SessionService(repos, settings, clock=clock)
    rows = []
    for user in READERS:
        if repos.users.get(user) is None:
            repos.users.create(user_id=user)
            purchase = repos.purchases.create(user, PRODUCT, "mock", f"seed-{user}")
            repos.entitlements.grant(user, PRODUCT, purchase.id)
        credentials = sessions.issue(user)
        rows.append((user, http.cookie_name(http.COOKIE_SESSION, settings), credentials.cookie_value, credentials.csrf_token))
    return rows


def banner(rows: list) -> str:
    lines = ["", "=" * 78, f"SCORING DEV MODE: {LABEL.upper()}",
             "Every question, pool and account below is invented and exists only in this throwaway database.",
             "Scoring flags are on for this process only; nothing here is a release, a key or real content.",
             "Seeded test accounts (no real sign-in): send the cookie and, for POST, the CSRF header.", ""]
    for user, name, value, csrf in rows:
        lines.append(f"  {user}: Cookie: {name}={value}   X-CSRF-Token: {csrf}")
    lines.append("=" * 78)
    return "\n".join(lines)
