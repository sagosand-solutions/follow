# SPDX-License-Identifier: AGPL-3.0-or-later
"""Local development runner for the accounts server (mock mode only).

``python -m server.dev`` starts Uvicorn on 127.0.0.1 with a throwaway SQLite
database and freshly generated development secrets. It refuses to run when
``APP_ENV`` is ``production`` and never reads a real secret from the
environment: the CSRF and session keys are generated at start and live only in
this process.

The static site and the API share one origin, which is what the Origin and CSRF
checks require: the site is served under ``/follow/`` (default ``public/``) and
the API under ``/follow/api``. Provider and payment flows stay mock/disabled
until real adapters exist; nothing here makes an external call.
"""
from __future__ import annotations

import argparse
import os
import pathlib
import secrets
import sys
import tempfile
import time

from starlette.applications import Starlette
from starlette.responses import RedirectResponse
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles

from .app import Services, create_app
from .config import load_config
from .content_catalog import ContentCatalog
from .db import Database

_REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
HOST = "127.0.0.1"
DEFAULT_PORT = 8000
DEFAULT_API_PATH = "/follow/api"
DEFAULT_SITE_PATH = "/follow"


class DevRefused(SystemExit):
    """The dev runner refused to start because the environment is unsafe."""


def refuse_production() -> None:
    """Refuse a production environment. Called first by ``main`` AND by ``build_dev_app``, so a direct call of the builder is
    as closed as the command line: nothing is written, migrated or seeded before this check."""
    if os.environ.get("APP_ENV", "").strip().lower() == "production":
        print("server.dev is a development runner and refuses APP_ENV=production", file=sys.stderr)
        raise DevRefused(2)


def dev_environment(port: int, db_path: str, scoring: dict | None = None) -> dict[str, str]:
    """Development-only settings: random keys, no adapters, loopback origin.

    Only development values are built here. Real secrets are never read from the
    environment, and ``BASE_PATH`` stays at ``/`` because the API is mounted
    under ``/follow/api`` in :func:`build_dev_app` instead of being prefixed
    twice.
    """
    env = {
        "APP_ENV": "development",
        "PUBLIC_ORIGIN": f"http://127.0.0.1:{port}",
        "BASE_PATH": "/",
        "DB_PATH": db_path,
        "AUTH_ADAPTERS": "mock",
        "TRUSTED_PROXIES": "",
        "CSRF_SECRET_KEY": secrets.token_bytes(32).hex(),
        "SESSION_SECRET_KEY": secrets.token_bytes(32).hex(),
    }
    if scoring is not None:        # development only: scoring on, private synthetic free pool (never the in-memory source)
        env.update({"SCORING_ENABLED": "true", "SCORING_ANONYMOUS_ENABLED": "true", "SCORING_CLAIM_ENABLED": "true",
                    "SCORING_PUBLISH_ENABLED": "true", "SCORING_FREE_CONTENT_ROOT": str(scoring["free"]["root"]),
                    "SCORING_FREE_MANIFEST": str(scoring["free"]["manifest"])})
    return env


def build_dev_app(
    *,
    port: int,
    db_path: str,
    content_root: str | None = None,
    manifest: str | None = None,
    site_root: str | None = None,
    api_path: str = DEFAULT_API_PATH,
    site_path: str = DEFAULT_SITE_PATH,
    scoring_dir: str | None = None,
) -> tuple[Starlette, object]:
    """Build the mock-mode ASGI app and its settings (no server started).

    The API app is mounted before the static mount because ``/follow/api`` is a
    sub-path of ``/follow``; Starlette matches routes in order and a mount that
    matches swallows the request, so the more specific API mount must come first.
    """
    refuse_production()                  # before any file, database or session side effect
    if scoring_dir is not None and (content_root is not None or manifest is not None):
        raise DevRefused("--scoring brings its own synthetic content; do not combine it with --content-root/--manifest")
    pools = None
    if scoring_dir is not None:
        from . import dev_scoring
        pools = dev_scoring.write_pools(pathlib.Path(scoring_dir))
    settings = load_config(dev_environment(port, db_path, pools))

    database = Database(db_path, clock=time.time)
    database.migrate()

    catalog = None
    scoring_services = None
    seeded = None
    if pools is not None:
        scoring_services, catalog = dev_scoring.build_services(pools, time.time)
        seeded = dev_scoring.seed_accounts(database, settings, time.time)
    elif content_root is not None and manifest is not None:
        catalog = ContentCatalog.load(content_root, manifest)
    database.close()

    services = Services(open_database=lambda: Database(db_path, clock=time.time), catalog=catalog, scoring=scoring_services)
    api_app = create_app(settings, services)

    routes: list = [Mount(api_path, app=api_app)]
    if site_root is not None and pathlib.Path(site_root).is_dir():
        routes.append(Mount(site_path, app=StaticFiles(directory=str(site_root), html=True)))
        routes.append(Route("/", lambda request: RedirectResponse(site_path + "/")))
    app = Starlette(routes=routes)
    app.state.dev_scoring = seeded
    return app, settings


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m server.dev",
        description="Local mock-mode accounts server (development only).",
    )
    parser.add_argument("--port", type=int, default=DEFAULT_PORT, help="loopback port (default %(default)s)")
    parser.add_argument("--content-root", help="private content root (with --manifest)")
    parser.add_argument("--manifest", help="private content manifest (with --content-root)")
    parser.add_argument("--site-root", default=str(_REPO_ROOT / "public"),
                        help="static site directory (default %(default)s)")
    parser.add_argument("--api-path", default=DEFAULT_API_PATH, help="API mount path (default %(default)s)")
    parser.add_argument("--site-path", default=DEFAULT_SITE_PATH, help="site mount path (default %(default)s)")
    parser.add_argument("--db", help="SQLite path (default: a temporary database)")
    parser.add_argument("--scoring", action="store_true",
                        help="scoring on, with a SYNTHETIC protected pool and seeded test accounts (development only)")
    return parser


def main(argv=None) -> int:
    refuse_production()
    args = _parser().parse_args(argv)
    if (args.content_root is None) != (args.manifest is None):
        print("--content-root and --manifest must be given together", file=sys.stderr)
        raise DevRefused(2)
    if args.scoring and args.content_root is not None:
        print("--scoring brings its own synthetic content; do not combine it with --content-root/--manifest", file=sys.stderr)
        raise DevRefused(2)

    temporary = None
    if args.db:
        db_path = args.db
    else:
        temporary = tempfile.TemporaryDirectory(prefix="follow-dev-")
        db_path = os.path.join(temporary.name, "accounts.db")
    try:
        scoring_dir = None
        if args.scoring:
            if temporary is None:
                temporary = tempfile.TemporaryDirectory(prefix="follow-dev-")
            scoring_dir = os.path.join(temporary.name, "scoring-pools")
        app, settings = build_dev_app(
            port=args.port, db_path=db_path, content_root=args.content_root, manifest=args.manifest,
            site_root=args.site_root, api_path=args.api_path, site_path=args.site_path, scoring_dir=scoring_dir,
        )
        if args.scoring:
            from . import dev_scoring
            print(dev_scoring.banner(app.state.dev_scoring), flush=True)
        import uvicorn

        print(f"dev server: {settings.public_origin}{args.site_path}/  "
              f"(api {settings.public_origin}{args.api_path}, mock mode)", flush=True)
        uvicorn.run(app, host=HOST, port=args.port, log_level="info")
    finally:
        if temporary is not None:
            temporary.cleanup()
    return 0


if __name__ == "__main__":
    sys.exit(main())
