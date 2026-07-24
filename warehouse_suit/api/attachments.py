# -*- coding: utf-8 -*-
"""Material attachment API routes."""

from flask import jsonify, request, send_file

from warehouse_suit.attachments import (
    attachment_absolute_path,
    attachment_to_dict,
    list_material_attachments,
    new_upload_token,
    save_uploaded_attachment,
)
from warehouse_suit.inventory_constants import STOCK_SOURCE_FORMAL, STOCK_SOURCE_TEMPORARY
from warehouse_suit.workflow_service import require_permission, user_has_permission


def register_attachment_routes(app, *, get_db, current_user_provider):
    current_user = current_user_provider

    def _form_int(name):
        try:
            return int(request.form.get(name) or 0) or None
        except (TypeError, ValueError):
            return None

    def _can_upload_workflow_attachment(user):
        roles = set(user.get("role_codes") or [])
        return bool({"admin", "warehouse"} & roles)

    def _can_delete_attachment(cursor, user, row):
        return user_has_permission(cursor, user, "delete_material_attachment")

    @app.post("/api/material-attachments")
    def upload_material_attachments():
        conn = get_db()
        cursor = conn.cursor()
        try:
            user = current_user(cursor)
            if not user:
                raise PermissionError("请先登录")
            material_id = _form_int("material_id")
            material_batch_id = _form_int("material_batch_id") or _form_int("batch_id")
            workflow_form_id = _form_int("workflow_form_id")
            workflow_item_id = _form_int("workflow_item_id")
            workflow_item = None
            batch_stock_source = None
            if workflow_form_id or workflow_item_id:
                if not workflow_form_id or not workflow_item_id:
                    raise ValueError("验收附件缺少流程单或物料行")
                cursor.execute(
                    """
                    SELECT wi.id, wi.form_id, wi.material_id, wf.status, wf.form_type
                    FROM workflow_items wi
                    JOIN workflow_forms wf ON wf.id = wi.form_id
                    WHERE wi.id = ? AND wi.form_id = ?
                    """,
                    (workflow_item_id, workflow_form_id),
                )
                workflow_item = cursor.fetchone()
                if not workflow_item:
                    return jsonify({"success": False, "error": "流程物料行不存在"}), 404
                if workflow_item["status"] != "acceptance":
                    raise ValueError("仅验收办理阶段可上传验收附件")
                row_material_id = int(workflow_item["material_id"] or 0)
                if material_id and row_material_id and material_id != row_material_id:
                    raise ValueError("附件物料与流程物料不一致")
                material_id = material_id or row_material_id or None
            if material_batch_id:
                if workflow_item:
                    raise ValueError("验收阶段附件将在入库后自动绑定批次")
                cursor.execute(
                    "SELECT id, material_id, stock_source FROM material_batches WHERE id = ? AND stock_source IN (?, ?)",
                    (material_batch_id, STOCK_SOURCE_FORMAL, STOCK_SOURCE_TEMPORARY),
                )
                batch_row = cursor.fetchone()
                batch_stock_source = batch_row["stock_source"] if batch_row else None
                if not batch_row:
                    return jsonify({"success": False, "error": "批次不存在"}), 404
                if material_id and int(material_id) != int(batch_row["material_id"]):
                    raise ValueError("附件批次与物料不一致")
                material_id = int(batch_row["material_id"])
            if material_id:
                if batch_stock_source == STOCK_SOURCE_TEMPORARY:
                    require_permission(cursor, "manage_temporary_inventory")
                elif workflow_item and _can_upload_workflow_attachment(user):
                    pass
                else:
                    require_permission(cursor, "edit_material")
                cursor.execute("SELECT id FROM materials WHERE id = ?", (material_id,))
                if not cursor.fetchone():
                    return jsonify({"success": False, "error": "物料不存在"}), 404
            else:
                require_permission(cursor, "start_acceptance")
            token = request.form.get("token") or new_upload_token()
            files = request.files.getlist("files") or request.files.getlist("file")
            if not files:
                raise ValueError("请选择要上传的附件")
            attachment_type = request.form.get("attachment_type") or "document"
            remark = request.form.get("remark") or ""
            attachments = [
                save_uploaded_attachment(
                    cursor,
                    file_storage,
                    upload_token=token,
                    user_id=user.get("id"),
                    material_id=material_id,
                    material_batch_id=material_batch_id,
                    workflow_form_id=workflow_form_id,
                    workflow_item_id=workflow_item_id,
                    attachment_type=attachment_type,
                    remark=remark,
                )
                for file_storage in files
            ]
            conn.commit()
        except PermissionError:
            conn.rollback()
            conn.close()
            raise
        except Exception as exc:
            conn.rollback()
            conn.close()
            return jsonify({"success": False, "error": str(exc)}), 400
        conn.close()
        return jsonify({"success": True, "token": token, "attachments": attachments})

    @app.get("/api/materials/<int:material_id>/attachments")
    def material_attachments(material_id):
        conn = get_db()
        cursor = conn.cursor()
        user = current_user(cursor)
        if not user:
            conn.close()
            raise PermissionError("请先登录")
        cursor.execute("SELECT id FROM materials WHERE id = ?", (material_id,))
        if not cursor.fetchone():
            conn.close()
            return jsonify({"success": False, "error": "物料不存在"}), 404
        attachments = list_material_attachments(cursor, material_id)
        conn.close()
        return jsonify({"success": True, "attachments": attachments})

    @app.get("/api/material-attachments/<int:attachment_id>/download")
    def download_material_attachment(attachment_id):
        conn = get_db()
        cursor = conn.cursor()
        user = current_user(cursor)
        if not user:
            conn.close()
            raise PermissionError("请先登录")
        cursor.execute("SELECT * FROM material_attachments WHERE id = ?", (attachment_id,))
        row = cursor.fetchone()
        conn.close()
        if not row:
            return jsonify({"success": False, "error": "附件不存在"}), 404
        path = attachment_absolute_path(row)
        if not path.exists():
            return jsonify({"success": False, "error": "附件文件不存在"}), 404
        return send_file(path, mimetype=row["content_type"] or None, as_attachment=False, download_name=row["original_name"])

    @app.delete("/api/material-attachments/<int:attachment_id>")
    def delete_material_attachment(attachment_id):
        conn = get_db()
        cursor = conn.cursor()
        try:
            user = current_user(cursor)
            if not user:
                raise PermissionError("请先登录")
            cursor.execute("SELECT * FROM material_attachments WHERE id = ?", (attachment_id,))
            row = cursor.fetchone()
            if not row:
                return jsonify({"success": False, "error": "附件不存在"}), 404
            if not _can_delete_attachment(cursor, user, row):
                raise PermissionError("当前账号没有删除附件权限")
            path = attachment_absolute_path(row)
            cursor.execute("DELETE FROM material_attachments WHERE id = ?", (attachment_id,))
            conn.commit()
            try:
                path.unlink(missing_ok=True)
            except OSError:
                pass
        except PermissionError:
            conn.rollback()
            conn.close()
            raise
        except Exception as exc:
            conn.rollback()
            conn.close()
            return jsonify({"success": False, "error": str(exc)}), 400
        conn.close()
        return jsonify({"success": True, "attachment": attachment_to_dict(row)})
