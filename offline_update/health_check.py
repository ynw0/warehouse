#!/usr/bin/env python3
"""Read-only database and HTTP health checks."""

import argparse
import json
from urllib.request import Request, urlopen

from common import database_checks, database_metrics, load_env_file, setting


def main():
    parser = argparse.ArgumentParser(description="Read-only warehouse health check")
    parser.add_argument("--config-file", required=True)
    args = parser.parse_args()
    config = load_env_file(args.config_file)
    db_path = setting(config, "DB_PATH", required=True)
    url = setting(config, "HEALTH_URL", required=True)
    with urlopen(Request(url, headers={"User-Agent": "warehouse-offline-health"}), timeout=15) as response:
        prefix = response.read(2048).decode("utf-8", errors="replace")
        status = response.status
    if status >= 400:
        raise SystemExit(f"HTTP health failed: {status}")
    checks = database_checks(db_path)
    if checks["integrity_check"] != "ok" or checks["foreign_key_check"]:
        raise SystemExit("database health failed")
    print(json.dumps({"http": {"url": url, "status": status, "body_prefix": prefix[:300]}, "database": checks, "metrics": database_metrics(db_path)}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
