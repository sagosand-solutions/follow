import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from server.content_access import ContentAccessError, resolve_content
from server.content_catalog import ContentCatalog
from server.db import Database
from server.repositories import Repositories
from server.tests.support import build_fixture, call, issue_for


class ContentAccessTests(unittest.TestCase):
    def make(self):
        fixture, tmp = build_fixture()
        self.addCleanup(tmp.cleanup)
        return fixture

    def session_headers(self, cookie):
        return {"cookie": f"session={cookie}"}

    def test_anonymous_is_401(self):
        fx = self.make()
        self.assertEqual(call(fx.app, "GET", "/content/question/q-1")[0], 401)

    def test_entitled_user_reads_content(self):
        fx = self.make()
        status, headers, body = call(fx.app, "GET", "/content/question/q-1", headers=self.session_headers(fx.cookie))
        self.assertEqual(status, 200)
        self.assertEqual(body, b'{"ok": true}')
        self.assertIn("application/json", headers.get("content-type", ""))
        self.assertEqual(headers.get("cache-control"), "no-store")

    def test_unentitled_and_unknown_and_kind_are_404(self):
        fx = self.make()
        db = Database(fx.path, clock=lambda: fx.clock[0])
        Repositories(db).users.create(user_id="u2")
        db.close()
        cookie = issue_for(fx, "u2")
        headers = self.session_headers(cookie)
        self.assertEqual(call(fx.app, "GET", "/content/question/q-1", headers=headers)[0], 404)
        self.assertEqual(call(fx.app, "GET", "/content/question/missing", headers=self.session_headers(fx.cookie))[0], 404)
        self.assertEqual(call(fx.app, "GET", "/content/video/q-1", headers=self.session_headers(fx.cookie))[0], 404)

    def test_path_attacks_never_leak(self):
        fx = self.make()
        headers = self.session_headers(fx.cookie)
        for bad in ("../secret", "..%2fsecret", "q-1/../x", "Q-1"):
            with self.subTest(bad=bad):
                self.assertEqual(call(fx.app, "GET", f"/content/question/{bad}", headers=headers)[0], 404)

    def test_content_id_length_gate_matches_the_catalog_and_schema_limit(self):
        fx = self.make()
        user = SimpleNamespace(user_id="u1")  # seeded and entitled to python-lifetime
        ok = "q" + "a" * 79  # 80 characters, the longest schema questionId
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "private"
            root.mkdir()
            (root / "long.txt").write_bytes(b'{"ok": 1}')
            manifest = Path(tmp) / "manifest.json"
            manifest.write_text(json.dumps({"entries": [{"kind": "question", "id": ok, "product_id": "python-lifetime", "path": "long.txt",
                                                         "sha256": hashlib.sha256(b'{"ok": 1}').hexdigest()}]}), encoding="utf-8")
            catalog = ContentCatalog.load(root, manifest)
            data, _ = resolve_content(catalog, fx.repos, user, "question", ok)
            self.assertEqual(data, b'{"ok": 1}')
            for bad in ("q" + "a" * 80, "a..b"):
                with self.subTest(length=len(bad)), self.assertRaises(ContentAccessError) as caught:
                    resolve_content(catalog, fx.repos, user, "question", bad)
                self.assertEqual(caught.exception.code, "not-found")
        headers = self.session_headers(fx.cookie)
        self.assertEqual(call(fx.app, "GET", "/content/question/" + "q" + "a" * 80, headers=headers)[0], 404)

    def test_revoked_entitlement_denies_immediately(self):
        fx = self.make()
        db = Database(fx.path, clock=lambda: fx.clock[0])
        repos = Repositories(db)
        for purchase in repos.database.connect().execute("SELECT id FROM purchases WHERE user_id = 'u1'").fetchall():
            repos.entitlements.revoke(purchase[0], "python-lifetime")
        db.close()
        self.assertEqual(call(fx.app, "GET", "/content/question/q-1", headers=self.session_headers(fx.cookie))[0], 404)

    def test_hash_mismatch_fails_closed(self):
        fx = self.make()
        # Tamper with the underlying file after load: read must fail closed.
        (Path(fx.content_root) / "q-1.txt").write_bytes(b"tampered")
        self.assertEqual(call(fx.app, "GET", "/content/question/q-1", headers=self.session_headers(fx.cookie))[0], 404)


if __name__ == "__main__":
    unittest.main()
