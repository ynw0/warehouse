"""Command line entry point for the warehouse suite."""

from __future__ import annotations

import argparse
import os

from . import __version__
from .runtime import DATA_DIR, ensure_runtime_dirs, migrate_legacy_database


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="warehouse_suit", description="Run the warehouse suite web application.")
    parser.add_argument("--version", action="store_true", help="print the application version and exit")
    parser.add_argument("--host", default=os.environ.get("HOST", "127.0.0.1"), help="bind host")
    parser.add_argument("--port", type=int, default=int(os.environ.get("PORT", "5000")), help="bind port")
    parser.add_argument("--debug", action="store_true", default=os.environ.get("FLASK_DEBUG", "0") == "1", help="enable Flask debug mode")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.version:
        print(__version__)
        return 0

    ensure_runtime_dirs()
    migrate_legacy_database()
    os.environ.setdefault("WAREHOUSE_DATA_DIR", str(DATA_DIR))
    os.environ.setdefault("WAREHOUSE_DB", str(DATA_DIR / "warehouse.db"))
    os.environ.setdefault("LOG_FILE", str(DATA_DIR / "warehouse.log"))

    from app import app

    app.run(debug=args.debug, host=args.host, port=args.port)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
