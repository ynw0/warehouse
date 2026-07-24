#!/usr/bin/env python3
import argparse
import json
from pathlib import Path

from common import checkpoint_database, load_env_file, setting, sqlite_backup, timestamp


def main():
    parser = argparse.ArgumentParser(description="Create a verified SQLite backup")
    parser.add_argument("--config-file", required=True)
    args = parser.parse_args()
    config = load_env_file(args.config_file)
    db_path = Path(setting(config, "DB_PATH", required=True)).resolve()
    backup_dir = Path(setting(config, "BACKUP_DIR", required=True)).resolve()
    backup_dir.mkdir(parents=True, exist_ok=True)
    checkpoint = checkpoint_database(db_path)
    result = sqlite_backup(db_path, backup_dir / f"warehouse_manual_backup_{timestamp()}.db")
    print(json.dumps({"checkpoint": checkpoint, "backup": result}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
