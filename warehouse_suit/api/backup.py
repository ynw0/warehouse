# -*- coding: utf-8 -*-
"""System backup route registration."""

import os
import tempfile

from flask import jsonify, request, send_file

from warehouse_suit.backup_service import (
    backup_path_from_name,
    backup_settings,
    create_database_backup,
    list_database_backups,
    restore_database_from_backup,
    save_backup_settings,
)
from warehouse_suit.backup_utils import ensure_backup_dir


def register_backup_routes(app, *, get_db, require_admin, init_database, notify_changed):
    """Register system backup settings, download, run, and restore endpoints."""

    def get_backup_settings():
        conn = get_db()
        cursor = conn.cursor()
        require_admin(cursor, "admin")
        settings = backup_settings(cursor)
        backups = list_database_backups(cursor)
        conn.close()
        return jsonify({"settings": settings, "backups": backups})

    def save_system_backup_settings():
        data = request.get_json(force=True)
        conn = get_db()
        cursor = conn.cursor()
        try:
            require_admin(cursor, "admin")
            settings = backup_settings(cursor)
            if "enabled" in data:
                settings["enabled"] = bool(data.get("enabled"))
            if "backup_dir" in data:
                settings["backup_dir"] = ensure_backup_dir(data.get("backup_dir"))
            if "frequency_hours" in data:
                settings["frequency_hours"] = max(1, int(float(data.get("frequency_hours") or 24)))
            if "retention_count" in data:
                settings["retention_count"] = max(1, int(float(data.get("retention_count") or 30)))
            save_backup_settings(cursor, settings)
            conn.commit()
        except Exception as exc:
            conn.rollback()
            conn.close()
            return jsonify({"success": False, "error": str(exc)}), 400
        backups = list_database_backups(cursor)
        conn.close()
        return jsonify({"success": True, "settings": settings, "backups": backups})

    def get_system_backups():
        conn = get_db()
        cursor = conn.cursor()
        require_admin(cursor, "admin")
        settings = backup_settings(cursor)
        backups = list_database_backups(cursor)
        conn.close()
        return jsonify({"settings": settings, "backups": backups})

    def run_system_backup():
        conn = get_db()
        cursor = conn.cursor()
        try:
            require_admin(cursor, "admin")
            backup = create_database_backup(cursor, reason="manual")
            conn.commit()
        except Exception as exc:
            conn.rollback()
            conn.close()
            return jsonify({"success": False, "error": str(exc)}), 400
        backups = list_database_backups(cursor)
        settings = backup_settings(cursor)
        conn.close()
        return jsonify({"success": True, "backup": backup, "settings": settings, "backups": backups})

    def download_system_backup():
        filename = request.args.get("filename", "")
        conn = get_db()
        cursor = conn.cursor()
        require_admin(cursor, "admin")
        path = backup_path_from_name(cursor, filename)
        conn.close()
        return send_file(path, as_attachment=True, download_name=os.path.basename(path))

    def restore_system_backup():
        conn = get_db()
        cursor = conn.cursor()
        temp_path = ""
        try:
            require_admin(cursor, "admin")
            if request.files.get("file"):
                uploaded = request.files["file"]
                suffix = os.path.splitext(uploaded.filename or "backup.db")[1] or ".db"
                fd, temp_path = tempfile.mkstemp(prefix="warehouse_restore_", suffix=suffix)
                os.close(fd)
                uploaded.save(temp_path)
                source_path = temp_path
            else:
                data = request.get_json(force=True)
                if not data.get("confirm"):
                    raise ValueError("restore requires confirmation")
                source_path = backup_path_from_name(cursor, data.get("filename"))
            conn.close()
            guard_path = restore_database_from_backup(source_path)
            if temp_path:
                try:
                    os.remove(temp_path)
                except OSError:
                    pass
            init_database()
        except Exception as exc:
            try:
                conn.close()
            except Exception:
                pass
            return jsonify({"success": False, "error": str(exc)}), 400
        notify_changed()
        return jsonify({"success": True, "before_restore_backup": guard_path})

    app.add_url_rule("/api/system/backup-settings", "get_backup_settings", get_backup_settings, methods=["GET"])
    app.add_url_rule("/api/system/backup-settings", "save_system_backup_settings", save_system_backup_settings, methods=["POST"])
    app.add_url_rule("/api/system/backups", "get_system_backups", get_system_backups, methods=["GET"])
    app.add_url_rule("/api/system/backups/run", "run_system_backup", run_system_backup, methods=["POST"])
    app.add_url_rule("/api/system/backups/download", "download_system_backup", download_system_backup, methods=["GET"])
    app.add_url_rule("/api/system/backups/restore", "restore_system_backup", restore_system_backup, methods=["POST"])
