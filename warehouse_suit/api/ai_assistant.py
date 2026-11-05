# -*- coding: utf-8 -*-
"""AI 助手接口：查询、办理，以及需要人工确认的高风险操作。"""

from flask import jsonify, request

from warehouse_suit.assistant.compact import usage_report
from warehouse_suit.assistant.files import save_upload
from warehouse_suit.assistant.memory import get_memory, save_memory
from warehouse_suit.assistant.profile import load_profile, resolve_thinking
from warehouse_suit.assistant.session_store import (
    create_session,
    get_preference,
    get_session,
    latest_session,
    list_messages,
    present_messages,
    set_preference,
    set_thinking,
)
from warehouse_suit.assistant_actions import (
    action_catalog,
    cancel_action,
    confirm_action,
    list_pending_actions,
    perform_action,
)

from warehouse_suit.ai_materials import (
    ai_material_by_code,
    ai_ranked_prefixes,
    ai_similar_materials,
    ai_similar_production_items,
)
from warehouse_suit.category_rules import list_category_rules
from warehouse_suit.material_service import next_material_code
from warehouse_suit.project_service import list_projects
from warehouse_suit.settings import get_setting
from warehouse_suit.validation import validate_material_code_value
from warehouse_suit.workflow_commands import (
    create_workflow_record,
    list_workflow_briefs,
    update_workflow_record,
)
from warehouse_suit.workflow_service import require_form_view, require_login, serialize_form


def register_ai_assistant_routes(app, *, get_db, ai_enabled, optional_active_user_id):
    """注册小助手查询和办理接口。入口只要求已登录，办事仍按该账号原有权限。"""

    def disabled():
        return jsonify({"success": False, "error": "当前版本未启用 AI 功能"}), 404

    def open_cursor():
        conn = get_db()
        return conn, conn.cursor()

    def guard(cursor):
        return require_login(cursor)

    def finish_write(callback):
        if not ai_enabled:
            return disabled()
        conn, cursor = open_cursor()
        try:
            guard(cursor)
            return callback(conn, cursor)
        except PermissionError:
            conn.rollback()
            raise
        except Exception as exc:
            conn.rollback()
            return jsonify({"success": False, "error": str(exc)}), 400
        finally:
            conn.close()

    def question():
        return str(request.args.get("q") or request.args.get("keyword") or "").strip()

    def limit(default, maximum):
        raw = request.args.get("limit")
        if raw is None or str(raw).strip() == "":
            return default
        try:
            value = int(raw)
        except (TypeError, ValueError):
            raise ValueError("条数必须是整数")
        if value < 1 or value > maximum:
            raise ValueError(f"条数必须在 1 到 {maximum} 之间")
        return value

    def arg_code(name, *aliases):
        value = request.args.get(name)
        if value is None:
            for alias in aliases:
                value = request.args.get(alias)
                if value is not None:
                    break
        return str(value or "").strip()

    @app.get("/api/ai/assistant")
    def ai_assistant_index():
        if not ai_enabled:
            return disabled()
        conn, cursor = open_cursor()
        try:
            guard(cursor)
            return jsonify(
                {
                    "success": True,
                    "tools": [
                        {"method": "GET", "path": "/api/ai/assistant/materials", "query": ["q", "limit"], "use": "按名称、编码、品牌或规格搜索物料和库存位置"},
                        {"method": "GET", "path": "/api/ai/assistant/materials/<material_code>", "use": "按完整物料编码取一条物料"},
                        {"method": "GET", "path": "/api/ai/assistant/production", "query": ["q", "limit"], "use": "搜索半成品和成品库存"},
                        {"method": "GET", "path": "/api/ai/assistant/prefixes", "query": ["q", "limit"], "use": "查看已使用的物料编码前缀"},
                        {
                            "method": "GET",
                            "path": "/api/ai/assistant/next-code",
                            "query": ["warehouse_code", "major_code", "middle_code", "small_code", "name", "brand_model", "spec"],
                            "use": "按仓库和大中小类取下一个可用物料编码",
                        },
                        {"method": "GET", "path": "/api/ai/assistant/rules", "use": "读取分类规则"},
                        {"method": "GET", "path": "/api/ai/assistant/projects", "use": "读取在用项目代号"},
                        {"method": "GET", "path": "/api/ai/assistant/workflows", "query": ["q", "form_type", "limit"], "use": "按单号或标题查找可见流程"},
                        {"method": "GET", "path": "/api/ai/assistant/workflows/<form_id>", "use": "读取一条流程"},
                        {"method": "POST", "path": "/api/ai/assistant/workflows", "use": "新建流程，正文带 form_type，其余字段与原发起接口相同"},
                        {"method": "PUT", "path": "/api/ai/assistant/workflows/<form_id>", "use": "修改流程，正文与原修改接口相同"},
                        {"method": "DELETE", "path": "/api/ai/assistant/workflows/<form_id>", "use": "提交删除流程确认，人工确认后才删除"},
                        {"method": "GET", "path": "/api/ai/assistant/actions", "use": "列出小助手可以办理的动作。需要人工确认的动作不会在这一步执行"},
                        {"method": "POST", "path": "/api/ai/assistant/actions", "body": ["action", "payload"], "use": "办理一个动作。高风险动作只返回待确认，不会直接执行"},
                    ],
                }
            )
        except PermissionError:
            raise
        except Exception as exc:
            return jsonify({"success": False, "error": str(exc)}), 400
        finally:
            conn.close()

    @app.get("/api/ai/assistant/materials")
    def ai_assistant_materials():
        if not ai_enabled:
            return disabled()
        conn, cursor = open_cursor()
        try:
            guard(cursor)
            rows = ai_similar_materials(cursor, question(), limit(12, 50))
            return jsonify({"success": True, "items": [_material(item) for item in rows]})
        except PermissionError:
            raise
        except Exception as exc:
            return jsonify({"success": False, "error": str(exc)}), 400
        finally:
            conn.close()

    @app.get("/api/ai/assistant/materials/<material_code>")
    def ai_assistant_material(material_code):
        if not ai_enabled:
            return disabled()
        conn, cursor = open_cursor()
        try:
            guard(cursor)
            code = validate_material_code_value(material_code)
            item = ai_material_by_code(cursor, code)
            if not item:
                return jsonify({"success": False, "error": "物料不存在"}), 404
            return jsonify({"success": True, "item": _material(item)})
        except PermissionError:
            raise
        except Exception as exc:
            return jsonify({"success": False, "error": str(exc)}), 400
        finally:
            conn.close()

    @app.get("/api/ai/assistant/production")
    def ai_assistant_production():
        if not ai_enabled:
            return disabled()
        conn, cursor = open_cursor()
        try:
            guard(cursor)
            rows = ai_similar_production_items(cursor, question(), limit(12, 50))
            return jsonify({"success": True, "items": [_production(item) for item in rows]})
        except PermissionError:
            raise
        except Exception as exc:
            return jsonify({"success": False, "error": str(exc)}), 400
        finally:
            conn.close()

    @app.get("/api/ai/assistant/prefixes")
    def ai_assistant_prefixes():
        if not ai_enabled:
            return disabled()
        conn, cursor = open_cursor()
        try:
            guard(cursor)
            rows = ai_ranked_prefixes(cursor, question())
            size = limit(24, 100)
            return jsonify({"success": True, "items": [_prefix(item) for item in rows[:size]]})
        except PermissionError:
            raise
        except Exception as exc:
            return jsonify({"success": False, "error": str(exc)}), 400
        finally:
            conn.close()

    @app.get("/api/ai/assistant/next-code")
    def ai_assistant_next_code():
        if not ai_enabled:
            return disabled()
        conn, cursor = open_cursor()
        try:
            guard(cursor)
            warehouse = arg_code("warehouse_code", "warehouse")
            major = arg_code("major_code", "major")
            middle = arg_code("middle_code", "middle")
            small = arg_code("small_code", "small")
            if warehouse not in {"10", "20"}:
                raise ValueError("仓库码只能是 10 或 20")
            code, step = next_material_code(
                cursor,
                warehouse,
                major,
                middle,
                small,
                request.args.get("name") or "",
                arg_code("brand_model", "brand"),
                request.args.get("spec") or "",
            )
            return jsonify(
                {
                    "success": True,
                    "material_code": code,
                    "detail_code": code[-4:],
                    "step": step,
                    "warehouse_code": warehouse,
                    "major_code": major,
                    "middle_code": middle,
                    "small_code": small,
                }
            )
        except PermissionError:
            raise
        except Exception as exc:
            return jsonify({"success": False, "error": str(exc)}), 400
        finally:
            conn.close()

    @app.get("/api/ai/assistant/rules")
    def ai_assistant_rules():
        if not ai_enabled:
            return disabled()
        conn, cursor = open_cursor()
        try:
            guard(cursor)
            return jsonify({"success": True, "items": list_category_rules(cursor)})
        except PermissionError:
            raise
        except Exception as exc:
            return jsonify({"success": False, "error": str(exc)}), 400
        finally:
            conn.close()

    @app.get("/api/ai/assistant/projects")
    def ai_assistant_projects():
        if not ai_enabled:
            return disabled()
        conn, cursor = open_cursor()
        try:
            guard(cursor)
            rows = list_projects(cursor, active_only=True)
            conn.commit()
            return jsonify({"success": True, "items": [_project(item) for item in rows]})
        except PermissionError:
            conn.rollback()
            raise
        except Exception as exc:
            conn.rollback()
            return jsonify({"success": False, "error": str(exc)}), 400
        finally:
            conn.close()

    @app.get("/api/ai/assistant/workflows")
    def ai_assistant_workflows():
        if not ai_enabled:
            return disabled()
        conn, cursor = open_cursor()
        try:
            user = guard(cursor)
            rows = list_workflow_briefs(
                cursor,
                user,
                question(),
                request.args.get("form_type") or request.args.get("type") or "",
                limit(20, 50),
            )
            return jsonify({"success": True, "items": rows})
        except PermissionError:
            raise
        except Exception as exc:
            return jsonify({"success": False, "error": str(exc)}), 400
        finally:
            conn.close()

    @app.get("/api/ai/assistant/workflows/<int:form_id>")
    def ai_assistant_workflow(form_id):
        if not ai_enabled:
            return disabled()
        conn, cursor = open_cursor()
        try:
            guard(cursor)
            require_form_view(cursor, form_id)
            form = serialize_form(cursor, form_id)
            if not form:
                return jsonify({"success": False, "error": "流程不存在"}), 404
            return jsonify({"success": True, "form": form})
        except PermissionError:
            raise
        except Exception as exc:
            return jsonify({"success": False, "error": str(exc)}), 400
        finally:
            conn.close()

    @app.post("/api/ai/assistant/workflows")
    def ai_assistant_create_workflow():
        def create(conn, cursor):
            form = create_workflow_record(conn, request.get_json(force=True) or {}, optional_active_user_id)
            return jsonify({"success": True, "form": form})

        return finish_write(create)

    @app.put("/api/ai/assistant/workflows/<int:form_id>")
    def ai_assistant_update_workflow(form_id):
        def update(conn, cursor):
            form = update_workflow_record(conn, form_id, request.get_json(force=True) or {})
            return jsonify({"success": True, "form": form})

        return finish_write(update)

    @app.delete("/api/ai/assistant/workflows/<int:form_id>")
    def ai_assistant_delete_workflow(form_id):
        def remove(conn, cursor):
            user = guard(cursor)
            result = perform_action(
                conn,
                user,
                "delete_workflow",
                {"form_id": form_id},
                _client_ip(),
                confirmed=False,
                optional_active_user_id=optional_active_user_id,
            )
            return jsonify({"success": True, **result})

        return finish_write(remove)

    @app.get("/api/ai/assistant/actions")
    def ai_assistant_actions():
        if not ai_enabled:
            return disabled()
        conn, cursor = open_cursor()
        try:
            guard(cursor)
            return jsonify({"success": True, "actions": action_catalog()})
        except PermissionError:
            raise
        except Exception as exc:
            return jsonify({"success": False, "error": str(exc)}), 400
        finally:
            conn.close()

    @app.post("/api/ai/assistant/actions")
    def ai_assistant_perform_action():
        def perform(conn, cursor):
            user = guard(cursor)
            body = request.get_json(force=True) or {}
            result = perform_action(
                conn,
                user,
                str(body.get("action") or "").strip(),
                body.get("payload") or {},
                _client_ip(),
                confirmed=False,
                optional_active_user_id=optional_active_user_id,
            )
            return jsonify({"success": True, **result})

        return finish_write(perform)

    @app.get("/api/ai/assistant/confirmations")
    def ai_assistant_confirmations():
        if not ai_enabled:
            return disabled()
        conn, cursor = open_cursor()
        try:
            user = guard(cursor)
            return jsonify({"success": True, "items": list_pending_actions(cursor, user)})
        except PermissionError:
            raise
        except Exception as exc:
            return jsonify({"success": False, "error": str(exc)}), 400
        finally:
            conn.close()

    @app.post("/api/ai/assistant/confirmations/<int:confirmation_id>/confirm")
    def ai_assistant_confirm(confirmation_id):
        def confirm(conn, cursor):
            user = guard(cursor)
            result = confirm_action(conn, user, confirmation_id, _client_ip(), optional_active_user_id)
            return jsonify({"success": True, **result})

        return finish_write(confirm)

    @app.post("/api/ai/assistant/confirmations/<int:confirmation_id>/cancel")
    def ai_assistant_cancel(confirmation_id):
        def cancel(conn, cursor):
            user = guard(cursor)
            result = cancel_action(conn, user, confirmation_id)
            return jsonify({"success": True, **result})

        return finish_write(cancel)

    @app.post("/api/ai/assistant/files")
    def ai_assistant_upload():
        """上传一条消息要用的附件。"""
        if not ai_enabled:
            return disabled()
        conn, cursor = open_cursor()
        try:
            user = guard(cursor)
            upload = request.files.get("file")
            if upload is None:
                return jsonify({"success": False, "error": "请选择文件"}), 400
            saved = save_upload(conn, int(user["id"]), upload.filename or "", upload.read())
            return jsonify({"success": True, "file": saved})
        except PermissionError:
            raise
        except Exception as exc:
            conn.rollback()
            return jsonify({"success": False, "error": str(exc)}), 400
        finally:
            conn.close()

    @app.get("/api/ai/assistant/session")
    def ai_assistant_session():
        """打开浮窗时恢复最近一次会话、记忆和用量。"""
        if not ai_enabled:
            return disabled()
        conn, cursor = open_cursor()
        try:
            user = guard(cursor)
            profile = load_profile(cursor)
            memory = get_memory(cursor, int(user["id"]))
            preference = get_preference(cursor, int(user["id"]))
            session = latest_session(cursor, int(user["id"]))
            messages = list_messages(cursor, int(session["id"])) if session else []
            usage = {}
            for row in reversed(messages):
                if row.get("role") == "assistant" and row.get("usage"):
                    usage = row["usage"]
                    break
            level = (session or {}).get("thinking_level") or resolve_thinking(profile, "", preference)
            return jsonify({
                "success": True,
                "session": session,
                "messages": present_messages(messages),
                "usage": usage_report(usage, profile, messages),
                "memory": memory,
                "thinking_level": level,
                "model": get_setting(cursor, "ai_model", ""),
                "profile": profile,
            })
        except PermissionError:
            raise
        finally:
            conn.close()

    @app.post("/api/ai/assistant/sessions")
    def ai_assistant_new_session():
        """清空对话时另开一轮，旧会话仍留在库里。"""
        if not ai_enabled:
            return disabled()
        conn, cursor = open_cursor()
        try:
            user = guard(cursor)
            profile = load_profile(cursor)
            preference = get_preference(cursor, int(user["id"]))
            level = resolve_thinking(profile, "", preference)
            session = create_session(conn, int(user["id"]), level)
            return jsonify({"success": True, "session": session, "thinking_level": level})
        except PermissionError:
            raise
        finally:
            conn.close()

    @app.put("/api/ai/assistant/sessions/<int:session_id>/thinking")
    def ai_assistant_thinking(session_id):
        """记住这一轮和这个用户的思考等级。"""
        if not ai_enabled:
            return disabled()
        conn, cursor = open_cursor()
        try:
            user = guard(cursor)
            profile = load_profile(cursor)
            requested = str((request.get_json(silent=True) or {}).get("thinking_level") or "").strip()
            if requested not in profile["thinking_levels"]:
                raise ValueError("思考等级不可用")
            if not profile["capabilities"]["reasoning"] and requested != "off":
                raise ValueError("当前模型未开启思考")
            if not get_session(cursor, session_id, int(user["id"])):
                raise LookupError("会话不存在")
            set_thinking(conn, session_id, int(user["id"]), requested)
            set_preference(conn, int(user["id"]), requested)
            return jsonify({"success": True, "thinking_level": requested})
        except PermissionError:
            raise
        except Exception as exc:
            conn.rollback()
            status = 404 if isinstance(exc, LookupError) else 400
            return jsonify({"success": False, "error": str(exc)}), status
        finally:
            conn.close()

    @app.get("/api/ai/assistant/memory")
    def ai_assistant_memory_get():
        if not ai_enabled:
            return disabled()
        conn, cursor = open_cursor()
        try:
            user = guard(cursor)
            return jsonify({"success": True, "memory": get_memory(cursor, int(user["id"]))})
        except PermissionError:
            raise
        finally:
            conn.close()

    @app.put("/api/ai/assistant/memory")
    def ai_assistant_memory_put():
        if not ai_enabled:
            return disabled()
        conn, cursor = open_cursor()
        try:
            user = guard(cursor)
            body = (request.get_json(silent=True) or {}).get("body") or ""
            saved = save_memory(conn, int(user["id"]), body)
            return jsonify({"success": True, "memory": saved})
        except PermissionError:
            raise
        except Exception as exc:
            conn.rollback()
            return jsonify({"success": False, "error": str(exc)}), 400
        finally:
            conn.close()


def _client_ip():
    forwarded = request.headers.get("X-Forwarded-For", "")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.remote_addr or ""


def _material(item):
    return {
        "id": item.get("id"),
        "material_code": item.get("material_code") or "",
        "name": item.get("name") or "",
        "brand_model": item.get("brand_model") or "",
        "spec": item.get("spec") or "",
        "unit": item.get("unit") or "",
        "category_name": item.get("category_name") or "",
        "material_type": item.get("material_type") or "",
        "purchase_applicant": item.get("purchase_applicant") or "",
        "quantity": item.get("quantity") or 0,
        "shelf_name": item.get("shelf_name") or "",
        "layer_number": item.get("layer_number") or "",
        "zone_name": item.get("zone_name") or "",
        "score": item.get("_score") or 0,
    }


def _production(item):
    return {
        "item_type": item.get("item_type") or "",
        "id": item.get("id"),
        "item_code": item.get("item_code") or "",
        "item_name": item.get("item_name") or "",
        "spec": item.get("spec") or "",
        "unit": item.get("unit") or "",
        "quantity": item.get("quantity") or 0,
        "available_quantity": item.get("available_quantity") or 0,
    }


def _prefix(item):
    return {
        "prefix": item.get("prefix") or "",
        "warehouse_code": item.get("warehouse") or "",
        "major_code": item.get("major") or "",
        "middle_code": item.get("middle") or "",
        "small_code": item.get("small") or "",
        "count": item.get("count") or 0,
        "max_detail": item.get("max_detail") or 0,
        "names": item.get("names") or [],
        "category_names": item.get("category_names") or [],
        "examples": item.get("full_examples") or [],
        "score": item.get("score") or 0,
    }


def _project(item):
    return {
        "code": item.get("code") or "",
        "owner_name": item.get("owner_name") or "",
        "active": bool(item.get("active")),
    }
