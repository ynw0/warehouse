# -*- coding: utf-8 -*-
"""System audit, log, and coding-rule route registration."""

import csv
import io
import os

from flask import Response, jsonify, request

from warehouse_suit.logging_utils import log_tail
from warehouse_suit.workflow_service import require_permission


def register_system_log_routes(app, *, get_db, require_admin, base_dir, default_log_path, coding_rules_provider):
    """Register audit-log, runtime-log, and coding-rule endpoints."""

    def get_audit_logs():
        conn = get_db()
        cursor = conn.cursor()
        require_admin(cursor, "admin")
        where = []
        params = []
        keyword = request.args.get("keyword", "").strip()
        date_from = request.args.get("date_from", "").strip()
        date_to = request.args.get("date_to", "").strip()
        if date_from:
            where.append("created_at >= ?")
            params.append(date_from + " 00:00:00" if len(date_from) == 10 else date_from)
        if date_to:
            where.append("created_at <= ?")
            params.append(date_to + " 23:59:59" if len(date_to) == 10 else date_to)
        if keyword:
            where.append("(username LIKE ? OR action LIKE ? OR target_type LIKE ? OR summary LIKE ?)")
            params.extend(["%{}%".format(keyword)] * 4)
        sql = "SELECT * FROM audit_logs"
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY id DESC LIMIT ?"
        params.append(min(1000, max(1, int(request.args.get("limit", "200") or 200))))
        cursor.execute(sql, params)
        rows = [dict(row) for row in cursor.fetchall()]
        conn.close()
        return jsonify({"items": rows})

    def export_audit_logs():
        conn = get_db()
        cursor = conn.cursor()
        require_admin(cursor, "admin")
        cursor.execute("SELECT * FROM audit_logs ORDER BY id DESC LIMIT 5000")
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(["id", "time", "user", "action", "target_type", "target_id", "summary", "ip"])
        for row in cursor.fetchall():
            writer.writerow([row["id"], row["created_at"], row["username"], row["action"], row["target_type"], row["target_id"], row["summary"], row["ip_address"]])
        conn.close()
        return Response(
            "\ufeff" + output.getvalue(),
            mimetype="text/csv; charset=utf-8",
            headers={"Content-Disposition": "attachment; filename=audit_logs.csv"},
        )

    def get_system_logs():
        conn = get_db()
        cursor = conn.cursor()
        try:
            require_permission(cursor, "view_logs")
            kind = request.args.get("kind", "runtime").strip().lower()
            limit = min(1000, max(50, int(request.args.get("limit", "300") or 300)))
            configured_log = os.environ.get("LOG_FILE")
            path = configured_log if configured_log else str(default_log_path)
            if not os.path.isabs(path):
                path = os.path.join(base_dir, path)
            lines = log_tail(path, limit, error_only=(kind == "error"))
            conn.close()
            return jsonify({"kind": "error" if kind == "error" else "runtime", "path": os.path.basename(path), "lines": lines})
        except Exception:
            conn.close()
            raise

    def get_coding_rules():
        conn = get_db()
        cursor = conn.cursor()
        try:
            require_permission(cursor, "view_query")
            html = coding_rules_provider(cursor)
            conn.close()
            return jsonify({"html": html})
        except Exception:
            conn.close()
            raise

    app.add_url_rule("/api/system/audit-logs", "get_audit_logs", get_audit_logs, methods=["GET"])
    app.add_url_rule("/api/system/audit-logs/export", "export_audit_logs", export_audit_logs, methods=["GET"])
    app.add_url_rule("/api/system/logs", "get_system_logs", get_system_logs, methods=["GET"])
    app.add_url_rule("/api/coding-rules", "get_coding_rules", get_coding_rules, methods=["GET"])
