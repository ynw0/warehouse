# -*- coding: utf-8 -*-
import json

from flask import Response, jsonify, request, stream_with_context

from warehouse_suit.ai_client import (
    ai_base_url,
    http_error_detail,
    openai_chat_events,
    openai_chat_message,
    openai_model_list,
    resolve_model_name,
)
from warehouse_suit.assistant.loop import iter_chat
from warehouse_suit.assistant.profile import load_profile, save_profile
from warehouse_suit.coding_agent import (
    confirm_category_rule,
    make_model_complete,
    public_job,
    start_coding_job,
)
from warehouse_suit.db import now_text
from warehouse_suit.settings import get_setting, set_setting
from warehouse_suit.api.ai_assistant import register_ai_assistant_routes
from warehouse_suit.workflow_service import require_login, require_permission


def register_ai_routes(
    app,
    *,
    get_db,
    current_user_provider,
    require_role_provider,
    ai_enabled,
    default_ai_base_url,
    default_ai_model,
    default_ai_api_key,
    default_skill_path,
    resolve_skill_path_provider,
    base_dir,
    optional_active_user_id_provider,
):
    current_user = current_user_provider
    require_any_role = require_role_provider
    AI_ENABLED = ai_enabled
    DEFAULT_AI_BASE_URL = default_ai_base_url
    DEFAULT_AI_MODEL = default_ai_model
    DEFAULT_AI_API_KEY = default_ai_api_key
    DEFAULT_SKILL_PATH = default_skill_path
    BASE_DIR = base_dir
    resolve_skill_path = resolve_skill_path_provider

    @app.get("/api/ai/config")
    def ai_config():
        if not AI_ENABLED:
            return jsonify({"enabled": False})
        conn = get_db()
        cursor = conn.cursor()
        stored_base_url = get_setting(cursor, "ai_base_url", DEFAULT_AI_BASE_URL)
        config = {
            "enabled": True,
            "base_url": ai_base_url(stored_base_url),
            "model": get_setting(cursor, "ai_model", DEFAULT_AI_MODEL),
            "skill_path": resolve_skill_path(get_setting(cursor, "ai_skill_path", DEFAULT_SKILL_PATH)),
            "database_api": get_setting(cursor, "database_api", request.host_url.rstrip("/") + "/api"),
            "has_api_key": bool(get_setting(cursor, "ai_api_key", DEFAULT_AI_API_KEY)),
            "profile": load_profile(cursor),
        }
        conn.close()
        return jsonify(config)


    @app.post("/api/ai/config")
    def save_ai_config():
        if not AI_ENABLED:
            return jsonify({"success": False, "error": "当前版本未启用 AI 功能"}), 404
        data = request.get_json(force=True)
        conn = get_db()
        cursor = conn.cursor()
        try:
            require_any_role(cursor, "admin")
            for key in ["ai_base_url", "ai_model", "ai_api_key", "ai_skill_path", "database_api"]:
                if key in data:
                    value = str(data.get(key) or "")
                    if key == "ai_base_url":
                        value = ai_base_url(value)
                    if key == "ai_api_key" and not value and get_setting(cursor, "ai_api_key", ""):
                        continue
                    set_setting(cursor, key, value)
            if "ai_model_profile" in data:
                save_profile(cursor, data.get("ai_model_profile") or {})
            conn.commit()
        except Exception as exc:
            conn.rollback()
            conn.close()
            return jsonify({"success": False, "error": str(exc)}), 400
        conn.close()
        return jsonify({"success": True})


    @app.post("/api/ai/models")
    def ai_models():
        if not AI_ENABLED:
            return jsonify({"success": False, "error": "当前版本未启用 AI 功能"}), 404
        data = request.get_json(force=True)
        conn = get_db()
        cursor = conn.cursor()
        try:
            require_any_role(cursor, "admin")
            base_url = ai_base_url(data.get("ai_base_url") or get_setting(cursor, "ai_base_url", DEFAULT_AI_BASE_URL))
            api_key = str(data.get("ai_api_key") or "")
            if not api_key:
                api_key = get_setting(cursor, "ai_api_key", DEFAULT_AI_API_KEY)
            if not base_url:
                raise ValueError("请先填写模型地址")
            models = openai_model_list(base_url, api_key)
            if not models:
                raise ValueError("连接成功，但未从 /models 返回可用模型")
        except Exception as exc:
            conn.close()
            return jsonify({"success": False, "error": http_error_detail(exc)}), 400
        conn.close()
        return jsonify({"success": True, "base_url": base_url, "models": models})


    @app.post("/api/ai/chat")
    def ai_chat():
        """流式对话。每一行是一个 JSON 事件。"""
        if not AI_ENABLED:
            return jsonify({"success": False, "error": "当前版本未启用 AI 功能"}), 404
        data = request.get_json(silent=True) or {}
        question = str(data.get("question") or "").strip()
        file_ids = data.get("file_ids") or []
        if not isinstance(file_ids, list):
            return jsonify({"success": False, "error": "附件参数不正确"}), 400
        if not question and not file_ids:
            return jsonify({"success": False, "error": "请输入问题"}), 400
        if not question:
            question = "请结合附件回答。"
        conn = get_db()
        cursor = conn.cursor()
        try:
            user = require_login(cursor)
        except PermissionError:
            conn.close()
            raise
        base_url = ai_base_url(get_setting(cursor, "ai_base_url", DEFAULT_AI_BASE_URL))
        model = str(data.get("model") or "").strip() or get_setting(cursor, "ai_model", DEFAULT_AI_MODEL)
        api_key = get_setting(cursor, "ai_api_key", DEFAULT_AI_API_KEY)
        if not base_url:
            conn.close()
            return jsonify({"success": False, "error": "请先在系统设置中配置 AI 地址"}), 400
        try:
            model = resolve_model_name(base_url, api_key, model)
        except Exception as exc:
            conn.close()
            return jsonify({"success": False, "error": http_error_detail(exc)}), 400
        if not model:
            conn.close()
            return jsonify({"success": False, "error": "未找到可用模型，请先在设置中选择模型"}), 400
        session_text = str(data.get("session_id") or "").strip()
        session_id = int(session_text) if session_text.isdigit() else None
        try:
            from warehouse_suit.assistant.files import load_files
            from warehouse_suit.assistant.session_store import get_session

            load_files(cursor, int(user["id"]), file_ids)
            if session_id is not None and not get_session(cursor, session_id, user["id"]):
                raise ValueError("会话不存在")
        except (TypeError, ValueError) as exc:
            conn.close()
            return jsonify({"success": False, "error": str(exc)}), 400
        thinking_level = str(data.get("thinking_level") or "")
        forwarded = request.headers.get("X-Forwarded-For", "")
        client_ip = forwarded.split(",")[0].strip() if forwarded else (request.remote_addr or "")

        def generate():
            try:
                for event in iter_chat(
                    conn,
                    user,
                    question=question,
                    session_id=session_id,
                    file_ids=file_ids,
                    thinking_level=thinking_level,
                    model=model,
                    client_ip=client_ip,
                    optional_active_user_id=optional_active_user_id_provider,
                    complete_events=lambda payload: openai_chat_events(base_url, api_key, payload),
                    complete_quiet=lambda payload: openai_chat_message(base_url, api_key, payload),
                ):
                    yield json.dumps(event, ensure_ascii=False) + "\n"
            except Exception as exc:
                yield json.dumps({"type": "error", "error": http_error_detail(exc)}, ensure_ascii=False) + "\n"
            finally:
                yield json.dumps({"type": "done"}, ensure_ascii=False) + "\n"
                conn.close()

        return Response(stream_with_context(generate()), mimetype="application/x-ndjson; charset=utf-8")

    def coding_runtime(cursor):
        base_url = ai_base_url(get_setting(cursor, "ai_base_url", DEFAULT_AI_BASE_URL))
        model = get_setting(cursor, "ai_model", DEFAULT_AI_MODEL)
        api_key = get_setting(cursor, "ai_api_key", DEFAULT_AI_API_KEY)
        return {"complete": make_model_complete(base_url, api_key, model)}

    def coding_job_row(cursor, job_id):
        cursor.execute("SELECT * FROM coding_jobs WHERE id = ?", (int(job_id),))
        row = cursor.fetchone()
        if not row:
            raise ValueError("赋码任务不存在")
        return row

    @app.post("/api/purchase-summaries/<int:form_id>/items/<int:item_id>/ai-code")
    def start_purchase_ai_code(form_id, item_id):
        if not AI_ENABLED:
            return jsonify({"success": False, "error": "当前版本未启用 AI 功能"}), 404
        conn = get_db()
        cursor = conn.cursor()
        try:
            user = require_permission(cursor, "manage_purchase_summary")
            cursor.execute(
                "SELECT status FROM workflow_forms WHERE id = ? AND form_type = 'purchase_summary'",
                (form_id,),
            )
            form = cursor.fetchone()
            if not form:
                raise ValueError("汇总不存在")
            if form["status"] == "registered":
                raise ValueError("汇总已经登记")
            cursor.execute(
                "SELECT id, material_name FROM workflow_items WHERE id = ? AND form_id = ?",
                (item_id, form_id),
            )
            item = cursor.fetchone()
            if not item:
                raise ValueError("赋码行不存在")
            if not str(item["material_name"] or "").strip():
                raise ValueError("这一行没有物料名称")
            timestamp = now_text()
            cursor.execute(
                """
                INSERT INTO coding_jobs
                    (form_id, item_id, status, input_json, result_json, error, operator_name, created_at, updated_at)
                VALUES (?, ?, 'queued', ?, '{}', '', ?, ?, ?)
                """,
                (
                    form_id,
                    item_id,
                    json.dumps({"item_id": item_id}, ensure_ascii=False),
                    user.get("display_name") or user.get("username") or "",
                    timestamp,
                    timestamp,
                ),
            )
            job_id = int(cursor.lastrowid)
            config = coding_runtime(cursor)
            conn.commit()
            start_coding_job(get_db, job_id, config)
            return jsonify({"success": True, "job_id": job_id})
        except Exception as exc:
            conn.rollback()
            return jsonify({"success": False, "error": str(exc)}), 400
        finally:
            conn.close()

    @app.get("/api/coding-jobs/<int:job_id>")
    def get_coding_job(job_id):
        if not AI_ENABLED:
            return jsonify({"success": False, "error": "当前版本未启用 AI 功能"}), 404
        conn = get_db()
        cursor = conn.cursor()
        try:
            require_permission(cursor, "manage_purchase_summary")
            return jsonify({"success": True, "job": public_job(coding_job_row(cursor, job_id))})
        except Exception as exc:
            return jsonify({"success": False, "error": str(exc)}), 400
        finally:
            conn.close()

    @app.post("/api/coding-jobs/<int:job_id>/supplement")
    def supplement_coding_job(job_id):
        if not AI_ENABLED:
            return jsonify({"success": False, "error": "当前版本未启用 AI 功能"}), 404
        conn = get_db()
        cursor = conn.cursor()
        try:
            require_permission(cursor, "manage_purchase_summary")
            row = coding_job_row(cursor, job_id)
            if row["status"] != "needs_input":
                raise ValueError("当前任务不需要补充信息")
            text = str((request.get_json(force=True) or {}).get("text") or "").strip()
            if not text:
                raise ValueError("请填写补充内容")
            config = coding_runtime(cursor)
            cursor.execute(
                "UPDATE coding_jobs SET status = 'queued', error = '', updated_at = ? WHERE id = ?",
                (now_text(), job_id),
            )
            conn.commit()

            def opener():
                return get_db()

            def run():
                supplement_conn = opener()
                try:
                    from warehouse_suit.coding_agent import run_coding_job
                    run_coding_job(supplement_conn, job_id, config, supplement_text=text)
                    supplement_conn.commit()
                except Exception as exc:
                    supplement_conn.rollback()
                    supplement_conn.execute(
                        "UPDATE coding_jobs SET status = 'failed', error = ?, updated_at = ? WHERE id = ?",
                        (str(exc), now_text(), job_id),
                    )
                    supplement_conn.commit()
                finally:
                    supplement_conn.close()

            import threading
            threading.Thread(target=run, name="coding-supplement-%s" % job_id, daemon=True).start()
            return jsonify({"success": True, "job_id": job_id})
        except Exception as exc:
            conn.rollback()
            return jsonify({"success": False, "error": str(exc)}), 400
        finally:
            conn.close()

    @app.post("/api/coding-jobs/<int:job_id>/confirm-rule")
    def confirm_coding_rule(job_id):
        if not AI_ENABLED:
            return jsonify({"success": False, "error": "当前版本未启用 AI 功能"}), 404
        conn = get_db()
        cursor = conn.cursor()
        try:
            user = require_permission(cursor, "manage_purchase_summary")
            confirm_category_rule(cursor, job_id, user.get("display_name") or user.get("username") or "")
            conn.commit()
            return jsonify({"success": True, "job": public_job(coding_job_row(cursor, job_id))})
        except Exception as exc:
            conn.rollback()
            return jsonify({"success": False, "error": str(exc)}), 400
        finally:
            conn.close()

    register_ai_assistant_routes(
        app,
        get_db=get_db,
        ai_enabled=AI_ENABLED,
        optional_active_user_id=optional_active_user_id_provider,
    )
