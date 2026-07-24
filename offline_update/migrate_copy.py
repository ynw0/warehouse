#!/usr/bin/env python3
import argparse
import json
from pathlib import Path

from common import load_env_file, migrate_database_copy, setting


def main():
    parser = argparse.ArgumentParser(description="Rehearse migrations on a database copy")
    parser.add_argument("--config-file", required=True)
    parser.add_argument("--report-dir")
    args = parser.parse_args()
    config = load_env_file(args.config_file)
    db_path = Path(setting(config, "DB_PATH", required=True)).resolve()
    report_dir = Path(args.report_dir or setting(config, "REPORT_DIR", required=True)).resolve() / "migration-rehearsal"
    print(json.dumps(migrate_database_copy(db_path, report_dir), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
