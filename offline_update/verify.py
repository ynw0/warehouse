#!/usr/bin/env python3
import argparse
import json

from common import database_checks, database_metrics, load_env_file, setting, verify_package_checksums


def main():
    parser = argparse.ArgumentParser(description="Verify package and database without writes")
    parser.add_argument("--config-file", required=True)
    args = parser.parse_args()
    config = load_env_file(args.config_file)
    db_path = setting(config, "DB_PATH", required=True)
    print(json.dumps({
        "checked_package_files": verify_package_checksums(),
        "database_checks": database_checks(db_path),
        "database_metrics": database_metrics(db_path),
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
