# SPDX-License-Identifier: MIT
"""Fixtures for the 023 scoring tests: synthetic questions, catalogs and an app with scoring on."""
from __future__ import annotations

import hashlib
import json
import tempfile
from dataclasses import dataclass
from pathlib import Path

from server.app import Services, create_app
from server.config import load_config
from server.content_catalog import ContentCatalog
from server.db import Database
from server.models import Product
from server.repositories import Repositories
from server.scoring_catalog import parse_catalog
from server.scoring_service import ManifestQuestionSource, ScoringServices
from server.sessions import SessionService
from server.tests.support import ORIGIN, call, json_body

SCORING_ENV = {"SCORING_ENABLED": "true", "SCORING_DEV_SOURCE": "true", "SCORING_ANONYMOUS_ENABLED": "true", "SCORING_CLAIM_ENABLED": "true",
               "SCORING_PUBLISH_ENABLED": "true"}
SENTINEL = "SECRET-SENTINEL-FACIT"


def make_question(qid: str, *, n_options: int = 5) -> dict:
    options = [{"id": f"{qid}-right", "text": f"Right answer for {qid}", "score": 1,
                "feedback": f"{SENTINEL} right {qid}", "misconception": None},
               {"id": f"{qid}-half", "text": f"Half answer for {qid}", "score": 0.5,
                "feedback": f"{SENTINEL} half {qid}", "misconception": "py.x", "partial_reason": f"{SENTINEL} partial"}]
    for i in range(n_options - 2):
        options.append({"id": f"{qid}-bad{i}", "text": f"Wrong answer {i} for {qid}", "score": 0,
                        "feedback": f"{SENTINEL} wrong {i}", "misconception": "py.y"})
    return {"schema_version": "0.1", "id": qid, "language": "python", "locale": "en", "type": "crashing-input",
            "level": "beginner", "difficulty": 0.5, "concepts": ["py.lists"], "prompt": f"Prompt {qid}",
            "snippets": [{"id": "main", "code": "print(1)\n", "highlight_lines": [1]}], "options": options,
            "explanation": f"{SENTINEL} explanation {qid}", "verification": {"status": "specified", "cases": []},
            "author": {"name": "Anonymous contributor"}, "license": "CC-BY-SA-4.0"}


def _bytes(question: dict) -> bytes:
    return json.dumps(question, sort_keys=True).encode("utf-8")


@dataclass
class ScoringFixture:
    app: object
    settings: object
    path: str
    clock: list
    repos: object
    scoring: ScoringServices
    catalogs: dict
    content_root: str
    cookies: dict      # user_id -> (session cookie, csrf)
    questions: dict    # family -> [ids]
    content_catalog: object

    def headers(self, user="u1", *, csrf=True, origin=True, extra=None):
        h = {}
        if user:
            cookie, token = self.cookies[user]
            h["cookie"] = f"session={cookie}"
            if csrf:
                h["x-csrf-token"] = token
        if origin:
            h["origin"] = ORIGIN
        if extra:
            h.update(extra)
        return h

    def post(self, path, body, user="u1", **kw):
        raw = body if isinstance(body, bytes) else json.dumps(body).encode()
        return call(self.app, "POST", path, headers=self.headers(user, **kw), body=raw)

    def get(self, path, user="u1", query=b"", **kw):
        return call(self.app, "GET", path, headers=self.headers(user, csrf=False, origin=False, **kw), query=query)

    def issue(self, qid, user="u1"):
        response = self.post(f"/scoring/questions/{qid}/issue", {}, user)
        return response, (json_body(response) if response[0] == 200 else None)

    def answer(self, issue, option_text_prefix, user="u1", key="k" * 16):
        """Answer with the option whose text starts with the given marker (e.g. 'Right')."""
        options = issue["question"]["options"]
        option = next(o for o in options if o["text"].startswith(option_text_prefix))
        return self.post("/scoring/answers", {"issue_id": issue["issue_id"], "outcome": "answer",
                                              "option_id": option["option_id"], "idempotency_key": key}, user)

    def anonymous(self):
        """Create an anonymous scoring profile through the real preauth flow; returns (cookie, csrf)."""
        csrf = call(self.app, "GET", "/auth/csrf")
        token = json_body(csrf)["csrf_token"]
        preauth = csrf[1]["set-cookie"].split(";")[0]
        response = call(self.app, "POST", "/scoring/anonymous", headers={"origin": ORIGIN, "cookie": preauth,
                        "x-csrf-token": token}, body=b"{}")
        assert response[0] == 200, response
        cookie = response[1]["set-cookie"].split(";")[0]
        return cookie, json_body(response)["csrf_token"]

    def anon_post(self, path, body, anon, *, csrf=True):
        headers = {"origin": ORIGIN, "cookie": anon[0]}
        if csrf:
            headers["x-csrf-token"] = anon[1]
        return call(self.app, "POST", path, headers=headers, body=json.dumps(body).encode())

    def db(self):
        return Database(self.path, clock=lambda: self.clock[0])


def build_scoring_fixture(*, official=((("py", 10)), ("prod", 4), ("sql", 4)), free=(("py-free", 3),), env=None,
                          users=("u1", "u2")) -> tuple[ScoringFixture, object]:
    tmp = tempfile.TemporaryDirectory()
    root = Path(tmp.name)
    db_path = str(root / "accounts.db")
    clock = [1_700_000_000.0]
    environ = {"APP_ENV": "test", "PUBLIC_ORIGIN": ORIGIN, "DB_PATH": db_path, "CSRF_SECRET_KEY": "a1" * 32,
               "SESSION_SECRET_KEY": "b2" * 32, "SESSION_IDLE_SECONDS": "100000000",
               "SESSION_ABSOLUTE_SECONDS": "100000000", **(SCORING_ENV if env is None else env)}
    settings = load_config(environ)
    db = Database(db_path, clock=lambda: clock[0])
    db.migrate()
    repos = Repositories(db)
    repos.products.upsert(Product("python-lifetime", "Python", "USD", 500, "python", True))
    for user in users:
        repos.users.create(user_id=user)
    purchase = repos.purchases.create("u1", "python-lifetime", "mock", "seed-1")
    repos.entitlements.grant("u1", "python-lifetime", purchase.id)

    content_root = root / "private"
    content_root.mkdir()
    entries, official_courses, official_ids, exposures = [], [], [], []
    for course, count in official:
        items = []
        for i in range(count):
            qid = f"{course}-q{i:02d}"
            data = _bytes(make_question(qid))
            (content_root / f"{qid}.txt").write_bytes(data)
            digest = hashlib.sha256(data).hexdigest()
            entries.append({"kind": "question", "id": qid, "product_id": "python-lifetime", "path": f"{qid}.txt", "sha256": digest})
            items.append({"question_id": qid, "product_id": "python-lifetime", "content_sha256": digest})
            exposures.append({"kind": "question", "id": qid, "content_sha256": digest, "affected_question_ids": [qid]})
            official_ids.append(qid)
        official_courses.append({"course_id": course, "N_c": count, "questions": items})
    # py-q02's feedback also touches py-q03: its solution is withheld until both are locked.
    for row in exposures:
        if row["id"] == "py-q02":
            row["affected_question_ids"] = ["py-q02", "py-q03"]
    # A study page that reveals the answers of the first two pool questions of the first course.
    study = b'{"study":"reveals two answers"}'
    (content_root / "study-a.txt").write_bytes(study)
    study_hash = hashlib.sha256(study).hexdigest()
    entries.append({"kind": "study", "id": "study-a", "product_id": "python-lifetime", "path": "study-a.txt", "sha256": study_hash})
    exposures.append({"kind": "study", "id": "study-a", "content_sha256": study_hash,
                      "affected_question_ids": official_ids[:2]})
    # Reviewed as harmless (explicitly empty), and one never reviewed (no row at all).
    for kind, cid, reviewed in (("exam", "exam-ok", True), ("exam", "exam-unreviewed", False)):
        data = b'{"exam":"x"}'
        (content_root / f"{cid}.txt").write_bytes(data)
        digest = hashlib.sha256(data).hexdigest()
        entries.append({"kind": kind, "id": cid, "product_id": "python-lifetime", "path": f"{cid}.txt", "sha256": digest})
        if reviewed:
            exposures.append({"kind": kind, "id": cid, "content_sha256": digest, "affected_question_ids": []})
    # Another product the user owns, with material that was never reviewed for exposure (default-deny).
    repos.products.upsert(Product("other-product", "Other", "USD", 500, "other", True))
    other = repos.purchases.create("u1", "other-product", "mock", "seed-2")
    repos.entitlements.grant("u1", "other-product", other.id)
    data = b'{"exam":"other product"}'
    (content_root / "other-exam.txt").write_bytes(data)
    entries.append({"kind": "exam", "id": "other-exam", "product_id": "other-product", "path": "other-exam.txt",
                    "sha256": hashlib.sha256(data).hexdigest()})
    manifest = root / "manifest.json"
    manifest.write_text(json.dumps({"entries": entries}), encoding="utf-8")
    content_catalog = ContentCatalog.load(content_root, manifest)

    free_items, free_ids, free_exposures, free_bytes = [], [], [], {}
    for course, count in free:
        items = []
        for i in range(count):
            qid = f"{course}-f{i:02d}"
            data = _bytes(make_question(qid))
            free_bytes[qid] = data
            digest = hashlib.sha256(data).hexdigest()
            items.append({"question_id": qid, "product_id": None, "content_sha256": digest})
            free_exposures.append({"kind": "question", "id": qid, "content_sha256": digest, "affected_question_ids": [qid]})
            free_ids.append(qid)
        free_items.append({"course_id": course, "N_c": count, "questions": items})

    catalogs = {
        "official": parse_catalog({"schema_version": 1, "catalog_version": "2026-10-18.1", "rules_version": "r2",
                                   "family": "official", "courses": official_courses, "exposures": exposures},
                                  expected_family="official", content_catalog=content_catalog),
        "free": parse_catalog({"schema_version": 1, "catalog_version": "2026-10-19.1", "rules_version": "r2",
                               "family": "free", "courses": free_items, "exposures": free_exposures},
                              expected_family="free"),
    }
    scoring = ScoringServices(catalogs, ManifestQuestionSource(content_catalog, free_bytes))
    cookies = {}
    for user in users:
        sdb = Database(db_path, clock=lambda: clock[0])
        credentials = SessionService(Repositories(sdb), settings, clock=lambda: clock[0]).issue(user)
        sdb.close()
        cookies[user] = (credentials.cookie_value, credentials.csrf_token)
    db.close()
    services = Services(open_database=lambda: Database(db_path, clock=lambda: clock[0]), catalog=content_catalog,
                        scoring=scoring, clock=lambda: clock[0])
    app = create_app(settings, services)
    fixture = ScoringFixture(app, settings, db_path, clock, repos, scoring, catalogs, str(content_root), cookies,
                             {"official": official_ids, "free": free_ids}, content_catalog)
    return fixture, tmp
