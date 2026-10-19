# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for the development runner: production is refused and the mock-mode app
answers a GET over the shared origin (task: dev-runner)."""
from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from server import dev
from server.tests.support import call


class DevRunnerTests(unittest.TestCase):
    def test_host_is_loopback(self):
        self.assertEqual(dev.HOST, "127.0.0.1")
        self.assertIn(f"http://{dev.HOST}:", dev.dev_environment(8000, ":memory:")["PUBLIC_ORIGIN"])

    def test_production_is_refused(self):
        with mock.patch.dict(os.environ, {"APP_ENV": "production"}):
            with self.assertRaises(dev.DevRefused):
                dev.main([])

    def test_mock_app_answers_get(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            site = root / "public"
            site.mkdir()
            (site / "index.html").write_text("<!doctype html><title>dev site</title>", encoding="utf-8")
            app, settings = dev.build_dev_app(
                port=8123, db_path=str(root / "accounts.db"), site_root=str(site))

            self.assertEqual(settings.app_env.value, "development")
            self.assertEqual(settings.public_origin, "http://127.0.0.1:8123")

            status, _, body = call(app, "GET", "/follow/api/products")
            self.assertEqual(status, 200)
            self.assertEqual(json.loads(body.decode("utf-8"))["products"], [])

            # The static file is served on the same origin; the test client does
            # not implement the ASGI file-send extension, so assert on the
            # response metadata rather than the streamed bytes.
            status, headers, _ = call(app, "GET", "/follow/")
            self.assertEqual(status, 200)
            self.assertTrue(headers["content-type"].startswith("text/html"))
            self.assertEqual(headers["content-length"], "38")

    def test_content_root_and_manifest_must_be_given_together(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(dev.DevRefused):
                dev.main(["--db", os.path.join(tmp, "a.db"), "--content-root", tmp])


if __name__ == "__main__":
    unittest.main()
