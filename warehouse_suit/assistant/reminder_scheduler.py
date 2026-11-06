"""定期检查因流程状态变化而释放的可申领库存。"""

import logging
import threading
import time

from warehouse_suit.assistant.notes import evaluate_material_notes
from warehouse_suit.maintenance import maintenance_enabled


LOGGER = logging.getLogger(__name__)
_thread = None
_lock = threading.Lock()


def _run(get_db):
    while True:
        time.sleep(30)
        if maintenance_enabled():
            continue
        conn = None
        try:
            conn = get_db()
            cursor = conn.cursor()
            cursor.execute("SELECT DISTINCT material_id FROM assistant_notes WHERE status = 'watching' AND material_id IS NOT NULL")
            material_ids = [int(row[0]) for row in cursor.fetchall()]
            if not material_ids:
                continue
            # 多个服务进程通过 SQLite 写锁串行检查，状态更新保证只提醒一次。
            conn.execute("BEGIN IMMEDIATE")
            for material_id in material_ids:
                evaluate_material_notes(cursor, material_id)
            conn.commit()
        except Exception:
            if conn:
                conn.rollback()
            LOGGER.exception("助手提醒检查失败")
        finally:
            if conn:
                conn.close()


def start_reminder_scheduler(get_db):
    global _thread
    with _lock:
        if _thread and _thread.is_alive():
            return
        _thread = threading.Thread(target=_run, args=(get_db,), name="assistant-reminders", daemon=True)
        _thread.start()
