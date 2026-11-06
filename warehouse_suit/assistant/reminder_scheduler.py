"""定期检查因流程状态变化而释放的可申领库存。"""

import logging
import threading
import time

from warehouse_suit.assistant.notes import evaluate_material_notes, deliver_due_notes
from warehouse_suit.db import now_text
from warehouse_suit.maintenance import maintenance_enabled


LOGGER = logging.getLogger(__name__)
_thread = None
_lock = threading.Lock()


def _run(get_db, should_run, stop):
    next_material_check = 0.0
    while not stop.wait(1):
        if not should_run() or maintenance_enabled():
            continue
        conn = None
        try:
            conn = get_db()
            cursor = conn.cursor()
            material_ids = []
            if time.monotonic() >= next_material_check:
                cursor.execute("SELECT DISTINCT material_id FROM assistant_notes WHERE status='watching' AND reminder_mode='material' AND material_id IS NOT NULL")
                material_ids = [int(row[0]) for row in cursor.fetchall()]
                next_material_check = time.monotonic() + 30
            cursor.execute("SELECT 1 FROM assistant_notes WHERE status='watching' AND reminder_mode='at_time' AND remind_at<=? LIMIT 1", (now_text(),))
            due = cursor.fetchone() is not None
            if not material_ids and not due:
                continue
            conn.execute("BEGIN IMMEDIATE")
            deliver_due_notes(cursor)
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


def start_reminder_scheduler(get_db, should_run=lambda: True):
    global _thread
    with _lock:
        if _thread and _thread.is_alive():
            return
        stop = threading.Event()
        _thread = threading.Thread(target=_run, args=(get_db, should_run, stop), name="assistant-reminders", daemon=True)
        _thread.start()
