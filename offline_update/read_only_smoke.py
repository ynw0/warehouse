#!/usr/bin/env python3
"""Safe production smoke checks: no business writes are performed."""
import argparse
import json
import sqlite3
from urllib.request import Request, urlopen

def table_exists(conn, table):
    return conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone() is not None

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--db-path", required=True)
    parser.add_argument("--base-url", required=True)
    args = parser.parse_args()
    conn = sqlite3.connect(f"file:{args.db_path}?mode=ro", uri=True)
    try:
        results = {
            "integrity": conn.execute("PRAGMA integrity_check").fetchone()[0],
            "foreign_keys": conn.execute("PRAGMA foreign_key_check").fetchall(),
            "materials": conn.execute("SELECT COUNT(*) FROM materials").fetchone()[0],
            "formal_batches": conn.execute("SELECT COUNT(*) FROM material_batches WHERE stock_source='formal'").fetchone()[0] if table_exists(conn, "material_batches") else 0,
            "temporary_batches": conn.execute("SELECT COUNT(*) FROM material_batches WHERE stock_source='temporary'").fetchone()[0] if table_exists(conn, "material_batches") else 0,
            "workflow_forms": conn.execute("SELECT COUNT(*) FROM workflow_forms").fetchone()[0] if table_exists(conn, "workflow_forms") else 0,
            "notifications": conn.execute("SELECT COUNT(*) FROM notifications").fetchone()[0] if table_exists(conn, "notifications") else 0,
        }
    finally:
        conn.close()
    with urlopen(Request(args.base_url.rstrip("/") + "/", headers={"User-Agent": "warehouse-readonly-smoke"}), timeout=15) as response:
        results["http_status"] = response.status
        results["html_prefix"] = response.read(512).decode("utf-8", errors="replace")[:200]
    if results["integrity"] != "ok" or results["foreign_keys"] or results["http_status"] >= 400:
        raise SystemExit(json.dumps(results, ensure_ascii=False))
    print(json.dumps(results, ensure_ascii=False, indent=2))

if __name__ == "__main__":
    main()
