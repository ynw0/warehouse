"""Enforce workflow task lifecycle invariants."""

from __future__ import annotations


VERSION = "2026082701"
NAME = "workflow task integrity"


def upgrade(conn):
    conn.execute(
        """
        UPDATE workflow_tasks AS task
        SET status = 'completed',
            decision = CASE
                WHEN TRIM(COALESCE(task.decision, '')) = '' THEN '流程已流转自动关闭'
                ELSE task.decision
            END,
            updated_at = datetime('now', 'localtime')
        WHERE COALESCE(task.status, '') IN ('', 'pending')
          AND EXISTS (
              SELECT 1
              FROM workflow_forms form
              WHERE form.id = task.form_id
                AND (
                    form.status IN ('completed', 'cancelled', 'rejected')
                    OR task.step_code <> form.current_step
                )
          )
        """
    )
    conn.execute(
        """
        UPDATE workflow_tasks
        SET status = 'completed',
            decision = '重复待办自动关闭',
            updated_at = datetime('now', 'localtime')
        WHERE status = 'pending'
          AND id NOT IN (
              SELECT MIN(id)
              FROM workflow_tasks
              WHERE status = 'pending'
              GROUP BY form_id, step_code, COALESCE(assignee_id, 0)
          )
        """
    )
    conn.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS idx_workflow_tasks_unique_pending_assignee
        ON workflow_tasks(form_id, step_code, COALESCE(assignee_id, 0))
        WHERE status = 'pending'
        """
    )
    conn.execute(
        """
        CREATE TRIGGER IF NOT EXISTS trg_workflow_forms_close_stale_tasks
        AFTER UPDATE OF status, current_step ON workflow_forms
        WHEN NEW.current_step <> OLD.current_step
          OR NEW.status IN ('completed', 'cancelled', 'rejected')
        BEGIN
            UPDATE workflow_tasks
            SET status = 'completed',
                decision = CASE
                    WHEN TRIM(COALESCE(decision, '')) = '' THEN '流程已流转自动关闭'
                    ELSE decision
                END,
                updated_at = datetime('now', 'localtime')
            WHERE form_id = NEW.id
              AND status = 'pending'
              AND (
                  NEW.status IN ('completed', 'cancelled', 'rejected')
                  OR step_code <> NEW.current_step
              );
        END
        """
    )


MIGRATION = {
    "version": VERSION,
    "name": NAME,
    "upgrade": upgrade,
}
