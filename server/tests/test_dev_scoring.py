# SPDX-License-Identifier: MIT
"""server.dev --scoring: a SYNTHETIC protected pool and seeded test accounts, development only.

Negative first: production and a mixed content source are refused, nothing real is used, and the pool is private and labelled.
"""
from __future__ import annotations

import contextlib
import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from server import dev, dev_scoring
from server.tests.support import call, json_body

REPO = Path(__file__).resolve().parents[2]
ORIGIN = "http://127.0.0.1:8123"


class DevScoringBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        site = self.root / "public"
        site.mkdir()
        (site / "index.html").write_text("<!doctype html><title>dev</title>", encoding="utf-8")
        self.db = str(self.root / "accounts.db")
        self.site = str(site)

    def build(self, **extra):
        return dev.build_dev_app(port=8123, db_path=self.db, site_root=self.site, scoring_dir=str(self.root / "pools"), **extra)

    def headers(self, row, *, post=False):
        _, name, value, csrf = row
        headers = {"cookie": f"{name}={value}"}
        if post:
            headers.update({"x-csrf-token": csrf, "origin": ORIGIN})
        return headers


class RefusalTests(DevScoringBase):
    def test_production_is_refused_with_or_without_scoring(self):
        with mock.patch.dict(os.environ, {"APP_ENV": "production"}), mock.patch("uvicorn.run") as run:
            for argv in (["--scoring"], []):
                with self.assertRaises(dev.DevRefused):
                    dev.main(argv)
            run.assert_not_called()

    def test_scoring_does_not_mix_with_a_content_source(self):
        with self.assertRaises(dev.DevRefused):
            dev.main(["--db", self.db, "--scoring", "--content-root", str(self.root), "--manifest", str(self.root / "m.json")])
        with self.assertRaises(dev.DevRefused):
            self.build(content_root=str(self.root), manifest=str(self.root / "m.json"))

    def test_the_scoring_environment_never_reaches_production(self):
        from server.config import load_config
        from server.models import SetupError
        env = dev.dev_environment(8123, self.db, dev_scoring.write_pools(self.root / "pools"))
        self.assertEqual(load_config(env).app_env.value, "development")
        with self.assertRaises(SetupError):
            load_config({**env, "APP_ENV": "production"})


class ProductionIsolationTests(DevScoringBase):
    """The builder and the seeding helper are closed in production too, not only the command line: nothing is written first."""

    def snapshot(self):
        return sorted((str(p.relative_to(self.root)), p.stat().st_size) for p in self.root.rglob("*") if p.is_file())

    def test_a_direct_builder_call_under_production_is_refused_before_any_side_effect(self):
        Path(self.db).write_bytes(b"not a real database, must stay untouched")
        before = self.snapshot()
        for value in ("production", " Production ", "PRODUCTION\n", "\tproduction"):
            for scoring in (True, False):
                with self.subTest(value=value, scoring=scoring), mock.patch.dict(os.environ, {"APP_ENV": value}), \
                        mock.patch.object(dev_scoring, "write_pools") as write, mock.patch.object(dev_scoring, "seed_accounts") as seed, \
                        mock.patch.object(dev, "Database") as database, mock.patch.object(dev, "create_app") as create:
                    with self.assertRaises(dev.DevRefused):
                        dev.build_dev_app(port=8123, db_path=self.db, site_root=self.site,
                                          scoring_dir=str(self.root / "pools") if scoring else None)
                    for call_ in (write, seed, database, create):
                        call_.assert_not_called()
        self.assertEqual(self.snapshot(), before, "no pool file, no database change, nothing created")
        self.assertFalse((self.root / "pools").exists())
        self.assertEqual(Path(self.db).read_bytes(), b"not a real database, must stay untouched")

    def test_seeding_with_production_settings_writes_nothing(self):
        import dataclasses
        from server.config import AppEnv
        from server.db import Database
        app, settings = self.build()
        database = Database(self.db, clock=lambda: 1.0)
        self.addCleanup(database.close)
        count = lambda: database.connect().execute("SELECT (SELECT COUNT(*) FROM users) + (SELECT COUNT(*) FROM sessions) "
                                                    "+ (SELECT COUNT(*) FROM entitlements) + (SELECT COUNT(*) FROM products)").fetchone()[0]
        before = count()
        production = dataclasses.replace(settings, app_env=AppEnv.PRODUCTION)
        with self.assertRaises(dev.DevRefused):
            dev_scoring.seed_accounts(database, production, lambda: 1.0)
        self.assertEqual(count(), before)

    def test_the_command_line_and_the_normal_flow_are_unchanged(self):
        with mock.patch.dict(os.environ, {"APP_ENV": " production "}), mock.patch("uvicorn.run") as run, \
                mock.patch.object(dev.tempfile, "TemporaryDirectory", wraps=dev.tempfile.TemporaryDirectory) as temporary:
            with self.assertRaises(dev.DevRefused):
                dev.main(["--scoring"])
            temporary.assert_not_called()                  # main refuses first, before it makes even a temporary directory
            run.assert_not_called()
        with mock.patch.dict(os.environ, {"APP_ENV": "development"}):
            app, settings = self.build()                  # development in the environment is fine
        self.assertEqual(len(app.state.dev_scoring), 3)


class SyntheticOnlyTests(DevScoringBase):
    def test_the_pool_is_invented_labelled_private_and_never_in_the_public_tree(self):
        app, settings = self.build()
        pools = self.root / "pools"
        entries = [e for family in ("official", "free") for e in json.loads((pools / family / "manifest.json").read_text())["entries"]]
        self.assertGreater(len(entries), 10)
        files = {p.name: p for p in pools.rglob("*.txt")}
        for entry in entries:
            self.assertTrue(entry["id"].startswith("dev-"), entry["id"])
            self.assertIn(dev_scoring.LABEL, files[entry["path"]].read_text(encoding="utf-8"), "every question says it is synthetic")
        public = "".join(p.read_text(encoding="utf-8", errors="ignore") for p in (REPO / "public").rglob("*")
                         if p.is_file() and p.suffix in (".json", ".js", ".html"))
        self.assertTrue(all(entry["id"] not in public for entry in entries), "no synthetic id exists in, or was taken from, the public bank")
        self.assertTrue(settings.scoring.free_manifest and not settings.scoring.dev_source, "the private manifest, not the in-memory source")
        self.assertNotIn(str(REPO / "public"), settings.scoring.free_manifest)
        self.assertFalse(settings.paddle_endpoint_secret)

    def test_no_real_key_account_or_payment_is_configured(self):
        _, settings = self.build()
        self.assertEqual(settings.app_env.value, "development")
        self.assertEqual(settings.payments_provider, "mock")
        self.assertEqual(settings.public_origin, ORIGIN)


class FlowTests(DevScoringBase):
    def test_a_seeded_reader_can_play_publish_and_be_ranked_end_to_end(self):
        app, _ = self.build()
        rows = app.state.dev_scoring
        self.assertEqual([r[0] for r in rows], list(dev_scoring.READERS))
        one, two = rows[0], rows[1]
        for row, outcome in ((one, "Right"), (two, "Half")):
            issue = json_body(call(app, "POST", "/follow/api/scoring/questions/dev-f-free-01/issue", headers=self.headers(row, post=True), body=b"{}"))
            self.assertIn(dev_scoring.LABEL, issue["question"]["prompt"])
            option = next(o for o in issue["question"]["options"] if o["text"].startswith(outcome))
            body = json.dumps({"issue_id": issue["issue_id"], "outcome": "answer", "option_id": option["option_id"], "idempotency_key": row[0].ljust(16, "k")}).encode()
            self.assertEqual(call(app, "POST", "/follow/api/scoring/answers", headers=self.headers(row, post=True), body=body)[0], 200)
            pref = json.dumps({"family": "free", "opt_in": True, "nickname": "Dev " + row[0][-1]}).encode()
            self.assertEqual(call(app, "POST", "/follow/api/scoring/preferences", headers=self.headers(row, post=True), body=pref)[0], 200)
        board = json_body(call(app, "GET", "/follow/api/leaderboards", query=b"family=free&scope=total&window=all"))
        self.assertEqual([e["alias"] for e in board["entries"]], ["Dev 1", "Dev 2"])
        me = json_body(call(app, "GET", "/follow/api/scoring/me", headers=self.headers(two), query=b"family=free"))
        self.assertEqual((me["rank"]["kind"], me["rank"]["value"]), ("actual", 2))

    def test_the_official_pool_needs_the_seeded_entitlement_and_nobody_else_gets_it(self):
        app, _ = self.build()
        reader = app.state.dev_scoring[0]
        ok = call(app, "POST", "/follow/api/scoring/questions/dev-o-intro-01/issue", headers=self.headers(reader, post=True), body=b"{}")
        self.assertEqual(ok[0], 200)
        anonymous = call(app, "POST", "/follow/api/scoring/questions/dev-o-intro-01/issue", headers={"origin": ORIGIN}, body=b"{}")
        self.assertEqual(anonymous[0], 401)

    def test_a_persistent_database_can_be_started_again(self):
        first, _ = self.build()
        second, _ = self.build()                      # same database, same deterministic pool, new sessions
        self.assertEqual({r[0] for r in second.state.dev_scoring}, set(dev_scoring.READERS))
        self.assertNotEqual({r[2] for r in first.state.dev_scoring}, {r[2] for r in second.state.dev_scoring})


class MainTests(DevScoringBase):
    def test_main_prints_a_loud_label_the_seeded_accounts_and_binds_loopback(self):
        out = io.StringIO()
        with mock.patch("uvicorn.run") as run, contextlib.redirect_stdout(out):
            self.assertEqual(dev.main(["--scoring", "--port", "8123", "--site-root", self.site]), 0)
        text = out.getvalue()
        self.assertIn(dev_scoring.LABEL.upper(), text)
        self.assertIn("no real sign-in", text)
        for user in dev_scoring.READERS:
            self.assertIn(user, text)
        self.assertEqual(run.call_args.kwargs["host"], "127.0.0.1")

    def test_without_the_flag_nothing_about_scoring_is_switched_on(self):
        app, settings = dev.build_dev_app(port=8123, db_path=self.db, site_root=self.site)
        self.assertFalse(settings.scoring.any())
        self.assertIsNone(app.state.dev_scoring)
        self.assertEqual(call(app, "GET", "/follow/api/leaderboards", query=b"family=free")[0], 503)


if __name__ == "__main__":
    unittest.main()
