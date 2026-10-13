"""``python -m longwatch`` — serve, migrate, patterns export|import, verify-bundle.

The entrypoint sequence (§18): wait for the DB → migrate under the advisory
lock (the runner creates ``schema_migrations`` itself) → seed builtin patterns
idempotently → exec ``serve``.  **No demo seeding happens here.**
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

from . import migrations, patterns as patterns_mod
from .config import get_config


def _wait_for_db(cfg, timeout_s: float = 120.0, interval_s: float = 2.0) -> None:
    import psycopg

    deadline = time.monotonic() + timeout_s
    last: Exception | None = None
    while time.monotonic() < deadline:
        try:
            conn = psycopg.connect(cfg.database_url, connect_timeout=5)
            conn.close()
            return
        except Exception as exc:  # noqa: BLE001 - a boot loop, not a failure
            last = exc
            time.sleep(interval_s)
    raise SystemExit(f"database did not become reachable: {last}")


def _seed_and_migrate(cfg) -> dict:
    import psycopg
    from psycopg.rows import dict_row

    # dict_row, exactly as the Database pool configures it: the migration
    # runner reads rows by column name, and a second migrate (every container
    # restart) must not crash on tuple rows
    conn = psycopg.connect(cfg.database_url, autocommit=False, row_factory=dict_row)
    try:
        applied = migrations.apply_migrations(conn)
        seeded = patterns_mod.seed_builtins(conn, actor="migration")
        from . import ledger
        genesis = ledger.ensure_genesis(conn, actor="system")
    finally:
        conn.close()
    return {"migrations_applied": applied, "patterns_seeded": seeded, "genesis": genesis}


def cmd_serve(args) -> int:
    from . import app as app_mod

    cfg = get_config()
    if not cfg.database_url:
        raise SystemExit("DATABASE_URL is required and is never defaulted")
    _wait_for_db(cfg)
    _seed_and_migrate(cfg)
    if args.host:
        cfg.host = args.host
    if args.port:
        cfg.port = int(args.port)
    app_mod.serve(cfg)
    return 0


def cmd_migrate(args) -> int:
    cfg = get_config()
    if not cfg.database_url:
        raise SystemExit("DATABASE_URL is required and is never defaulted")
    _wait_for_db(cfg)
    outcome = _seed_and_migrate(cfg)
    print(json.dumps(outcome, indent=2))
    return 0


def cmd_patterns(args) -> int:
    import psycopg
    from psycopg.rows import dict_row

    cfg = get_config()
    conn = psycopg.connect(cfg.database_url, row_factory=dict_row, autocommit=False)
    try:
        if args.action == "export":
            rows = patterns_mod.list_patterns(conn)
            for row in rows:
                if isinstance(row["rule"], str):
                    row["rule"] = json.loads(row["rule"])
                if hasattr(row.get("updated_at"), "isoformat"):
                    row["updated_at"] = row["updated_at"].isoformat()
            payload = {"patterns": rows}
            text = json.dumps(payload, indent=2, sort_keys=True)
            if args.out:
                Path(args.out).write_text(text, encoding="utf-8")
                print(f"wrote {len(rows)} pattern rows to {args.out}")
            else:
                print(text)
            return 0
        if args.action == "import":
            payload = json.loads(Path(args.path).read_text(encoding="utf-8"))
            created = []
            for document in payload.get("patterns", []):
                slug = document["slug"]
                existing = {row["version"] for row in patterns_mod.get_pattern(conn, slug)}
                if int(document["version"]) in existing:
                    continue
                patterns_mod.create_version(conn, slug, document, cfg.reviewer_name)
                created.append(f"{slug}@{document['version']}")
            print(json.dumps({"created": created}, indent=2))
            return 0
    finally:
        conn.close()
    raise SystemExit("patterns action must be export or import")


def cmd_verify_bundle(args) -> int:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    import verify_bundle

    if not Path(args.bundle).is_dir():
        print(f"not a directory: {args.bundle}", file=sys.stderr)
        return 2
    report = verify_bundle.verify_bundle(args.bundle)
    report.print()
    return report.exit_code


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="longwatch", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    serve = sub.add_parser("serve", help="run the HTTP server (migrates, then binds)")
    serve.add_argument("--host", default=None)
    serve.add_argument("--port", default=None)
    serve.set_defaults(func=cmd_serve)

    migrate = sub.add_parser("migrate", help="apply migrations and seed builtins, then exit")
    migrate.set_defaults(func=cmd_migrate)

    pat = sub.add_parser("patterns", help="export or import pattern rows")
    pat.add_argument("action", choices=["export", "import"])
    pat.add_argument("--out", default=None)
    pat.add_argument("path", nargs="?", default=None)
    pat.set_defaults(func=cmd_patterns)

    vb = sub.add_parser("verify-bundle", help="verify an export bundle")
    vb.add_argument("bundle")
    vb.set_defaults(func=cmd_verify_bundle)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command == "patterns" and args.action == "import" and not args.path:
        parser.error("patterns import needs a path")
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
