# -*- coding: utf-8 -*-
"""Notification, todo, and todo-SSE route registration."""

import json
import queue
import time

from flask import Response, jsonify, request, stream_with_context

from warehouse_suit.db import now_text
from warehouse_suit.temporary_inventory_visibility import append_notification_visibility
from warehouse_suit.todo_service import (
    cleanup_notifications,
    latest_workflow_event_id,
    notification_rows,
    pending_tasks_for_user,
    todos_payload_for_user_id,
    unread_notification_count,
)


def _todo_notification_item(item):
    item = dict(item)
    item["notification_title"] = "{} {}".format(item["form_no"], item.get("title") or "").strip()
    item["notification_body"] = "待办理：{}，当前状态：{}".format(item["step_code"], item["status"])
    return item


def register_notification_routes(app, *, get_db, current_user_provider, sse_lock, sse_clients):
    """Register notification and todo endpoints."""

    def list_notifications():
        conn = get_db()
        cursor = conn.cursor()
        user = current_user_provider(cursor)
        if not user:
            conn.close()
            return jsonify({"success": False, "error": "请先登录"}), 401
        cleanup_notifications(cursor)
        read = str(request.args.get("read") or "0").lower() in {"1", "true", "yes"}
        rows = notification_rows(cursor, user["id"], read=read)
        unread_count = unread_notification_count(cursor, user["id"])
        conn.commit()
        conn.close()
        return jsonify({"success": True, "items": rows, "unread_count": unread_count, "read": read})

    def mark_notifications_read_all():
        conn = get_db()
        cursor = conn.cursor()
        user = current_user_provider(cursor)
        if not user:
            conn.close()
            return jsonify({"success": False, "error": "请先登录"}), 401
        where = ["user_id = ?", "is_read = 0"]
        params = [user["id"]]
        append_notification_visibility(cursor, where, params, "notifications")
        cursor.execute(
            f"UPDATE notifications SET is_read = 1, read_at = ? WHERE {' AND '.join(where)}",
            [now_text(), *params],
        )
        changed = cursor.rowcount
        conn.commit()
        unread_count = unread_notification_count(cursor, user["id"])
        conn.close()
        return jsonify({"success": True, "updated": changed, "unread_count": unread_count})

    def notifications():
        conn = get_db()
        cursor = conn.cursor()
        user = current_user_provider(cursor)
        if not user:
            conn.close()
            return jsonify({"success": False, "error": "请先登录"}), 401
        rows = [_todo_notification_item(row) for row in pending_tasks_for_user(cursor, user, 20)]
        conn.close()
        return jsonify({"success": True, "items": rows})

    def todos():
        conn = get_db()
        cursor = conn.cursor()
        user = current_user_provider(cursor)
        if not user:
            conn.close()
            return jsonify({"success": False, "error": "请先登录"}), 401
        rows = [_todo_notification_item(item) for item in pending_tasks_for_user(cursor, user, 100)]
        notifications = notification_rows(cursor, user["id"], read=False, limit=50)
        conn.close()
        return jsonify({"success": True, "items": rows, "notifications": notifications})

    def todos_stream():
        conn = get_db()
        cursor = conn.cursor()
        user = current_user_provider(cursor)
        if not user:
            conn.close()
            return jsonify({"success": False, "error": "请先登录"}), 401
        user_id = user["id"]
        conn.close()

        def event_stream():
            client = queue.Queue(maxsize=8)
            with sse_lock:
                sse_clients.add(client)
            last_payload = ""
            last_event_id = latest_workflow_event_id()
            last_ping = time.time()
            try:
                while True:
                    should_send = not last_payload
                    try:
                        client.get(timeout=2)
                        should_send = True
                    except queue.Empty:
                        current_event_id = latest_workflow_event_id()
                        if current_event_id != last_event_id:
                            last_event_id = current_event_id
                            should_send = True
                    if should_send:
                        payload = todos_payload_for_user_id(user_id)
                        payload_text = json.dumps(payload, ensure_ascii=False)
                        if payload_text != last_payload:
                            last_payload = payload_text
                            yield "event: todos\ndata: {}\n\n".format(payload_text)
                            last_ping = time.time()
                    if time.time() - last_ping >= 25:
                        last_ping = time.time()
                        yield ": ping\n\n"
            finally:
                with sse_lock:
                    sse_clients.discard(client)

        return Response(
            stream_with_context(event_stream()),
            mimetype="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    app.add_url_rule("/api/notifications", "list_notifications", list_notifications, methods=["GET"])
    app.add_url_rule("/api/notifications/read-all", "mark_notifications_read_all", mark_notifications_read_all, methods=["POST"])
    app.add_url_rule("/api/todo-notifications", "notifications", notifications, methods=["GET"])
    app.add_url_rule("/api/todos", "todos", todos, methods=["GET"])
    app.add_url_rule("/api/todos/stream", "todos_stream", todos_stream, methods=["GET"])
