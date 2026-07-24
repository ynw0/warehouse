"""File-based maintenance mode shared by the application and update tools."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

from warehouse_suit.runtime import DATA_DIR


def maintenance_flag_path() -> Optional[Path]:
    """Return the configured flag or the standard data-directory location."""

    value = os.environ.get("WAREHOUSE_MAINTENANCE_FLAG", "").strip()
    if value:
        path = Path(value).expanduser()
        return path if path.is_absolute() else None
    return DATA_DIR / "warehouse-maintenance.flag"


def maintenance_enabled() -> bool:
    """Read the flag on every request so enable/disable needs no restart."""

    flag = maintenance_flag_path()
    return bool(flag and flag.is_file())
