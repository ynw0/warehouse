"""赋码编排：本库编码由模型判断，集团两个码只复制分类工具的返回值。"""

from __future__ import annotations

import json
import threading

from warehouse_suit.ai_client import http_error_detail, openai_chat_completion
from warehouse_suit.ai_materials import ai_prefix_context, ai_similar_materials
from warehouse_suit.category_rules import rules_text, save_confirmed_rule
from warehouse_suit.db import now_text
from warehouse_suit.group_coding import classify_group_material, supplement_group_material
from warehouse_suit.group_coding.pipeline import parse_json_object
from warehouse_suit.material_service import next_material_code
from warehouse_suit.validation import validate_material_code_value


WAREHOUSE_CODE_PROMPT = """你是仓库物料编码助手。只判断本库物料编码。
仓库、大类、中类、小类和明细，只以用户消息中的【分类规则】为准。不要用记忆里的分类表，也不要执行技能里的脚本或读文件。
相近物料、前缀地图和程序建议只供对照，不要直接抄成结论。末四位自己判断，不要交给分配程序。集团两个码由其他工具给出，不要在这里编造。
参考里已有同一物料时，same_as_existing 填 true，material_code 填那条已有编码。
否则给出新的 14 位编码，说明参考了哪些物料，以及末四位为什么这样取。
现有大中小类都不合适时，new_category 填 true，并在 proposed_rule 里写出建议。
只输出一个 JSON 对象，不要输出其他文字：
{"same_as_existing": false, "material_code": "", "rationale": "", "new_category": false, "proposed_rule": {"warehouse_code": "", "major_code": "", "middle_code": "", "small_code": "", "major_name": "", "middle_name": "", "small_name": "", "reason": ""}}
"""

_FILLABLE = {"success", "master_data_missing"}
_SUPPLEMENT = {"information_missing", "service_category_no_match"}


def parse_model_json(text):
    """从模型回复里取出 JSON 对象。夹杂说明或不是对象都算失败。"""
    return parse_json_object(text)


def make_model_complete(base_url, api_key, model):
    """走仓库现有的模型接口。"""

    def complete(system, user):
        if not base_url or not model:
            raise ValueError("请先在系统设置中填写模型地址和模型名")
        payload = {
            "model": model,
            "temperature": 0,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        }
        try:
            body = openai_chat_completion(base_url, api_key, payload)
        except Exception as exc:
            detail = http_error_detail(exc)
            if "temperature" not in detail.lower():
                raise ValueError(detail)
            payload.pop("temperature", None)
            try:
                body = openai_chat_completion(base_url, api_key, payload)
            except Exception as retry_exc:
                raise ValueError(http_error_detail(retry_exc))
        message = (body.get("choices") or [{}])[0].get("message") or {}
        content = message.get("content") or ""
        if not content:
            raise ValueError("模型没有返回内容")
        return parse_model_json(content)

    return complete


def structured_smoke(complete):
    """确认模型能返回约定 JSON。失败时调用方不得填码。"""
    payload = complete("只输出 JSON 对象，不要输出其他文字。", '请返回 {"ok": true}')
    if not isinstance(payload, dict) or payload.get("ok") is not True:
        raise ValueError("结构化冒烟失败：模型没有返回 {\"ok\": true}")
    return payload


def fail_stale_coding_jobs(conn):
    """进程重启后，进行中的赋码任务改为失败，避免页面一直等待。"""
    tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    if "coding_jobs" not in tables:
        return 0
    cursor = conn.execute(
        """
        UPDATE coding_jobs
        SET status = 'failed', error = '进程重启，赋码任务已中断', updated_at = ?
        WHERE status IN ('queued', 'running')
        """,
        (now_text(),),
    )
    return cursor.rowcount


def verify_warehouse_code(cursor, code, line_name):
    """只检查格式、仓库和占用。不把编码改成程序算出的另一个号。"""
    checked = validate_material_code_value(code)
    if not checked.startswith("10") or checked[2:4] not in {"10", "20"}:
        raise ValueError("仓库码只能是 10 或 20")
    cursor.execute("SELECT name FROM materials WHERE material_code = ?", (checked,))
    row = cursor.fetchone()
    if row and str(row["name"] or "").strip() and str(row["name"] or "").strip() != str(line_name or "").strip():
        raise ValueError("该物料编码已属于另一条物料")
    return checked


def reference_packet(cursor, name, brand, spec):
    """分类规则以数据库为准；相近物料与程序建议只供对照。"""
    question = " ".join(part for part in (name, brand, spec) if part)
    similar = ai_similar_materials(cursor, question, 8)
    lines = ["【分类规则】\n" + rules_text(cursor)]
    lines.append("【只供对照，不能直接抄成结论】")
    if similar:
        lines.append("相近物料：")
        for item in similar:
            lines.append(
                "- %s | %s | %s | %s"
                % (item.get("material_code") or "", item.get("name") or "", item.get("brand_model") or "", item.get("spec") or "")
            )
    else:
        lines.append("没有找到相近物料。")
    lines.append(ai_prefix_context(cursor, question, 12))
    suggestion = _program_suggestion(cursor, similar, name, brand, spec)
    if suggestion:
        lines.append("程序建议（仅供参考，不得直接采用）：%s" % suggestion)
    return "\n".join(lines), similar


def _program_suggestion(cursor, similar, name, brand, spec):
    codes = [str(item.get("material_code") or "") for item in similar if len(str(item.get("material_code") or "")) == 14]
    if not codes:
        return ""
    prefix = codes[0][:10]
    try:
        suggested, _step = next_material_code(cursor, prefix[2:4], prefix[4:6], prefix[6:8], prefix[8:10], name, brand, spec)
    except Exception:
        return ""
    return suggested


def judge_warehouse_code(cursor, line_name, decision):
    if not isinstance(decision, dict):
        raise ValueError("本库编码判断不是 JSON 对象")
    code = verify_warehouse_code(cursor, decision.get("material_code"), line_name)
    if decision.get("same_as_existing"):
        cursor.execute("SELECT 1 FROM materials WHERE material_code = ?", (code,))
        if not cursor.fetchone():
            raise ValueError("判断为已有物料，但该编码不存在")
    return code


def group_codes_from_tool(group_result):
    """集团两个码只取工具返回值。"""
    return {
        "group_category_code": str(group_result.get("group_category_code") or ""),
        "group_service_code": str(group_result.get("group_service_code") or ""),
        "standard_description": str(group_result.get("standard_description") or ""),
    }


def public_job(row):
    result = _json_load(row["result_json"])
    return {
        "id": row["id"],
        "form_id": row["form_id"],
        "item_id": row["item_id"],
        "status": row["status"],
        "error": row["error"] or "",
        "result": {
            "material_code": result.get("material_code") or "",
            "group_category_code": result.get("group_category_code") or "",
            "group_service_code": result.get("group_service_code") or "",
            "standard_description": result.get("standard_description") or "",
            "rationale": result.get("rationale") or "",
            "similar": result.get("similar") or [],
            "missing": result.get("missing") or [],
            "message": result.get("message") or "",
            "new_category": bool(result.get("new_category")),
            "proposed_rule": result.get("proposed_rule") or {},
            "rule_saved": bool(result.get("rule_saved")),
        },
    }


def latest_succeeded_job(cursor, item_id):
    cursor.execute(
        """
        SELECT * FROM coding_jobs
        WHERE item_id = ? AND status = 'succeeded'
        ORDER BY id DESC LIMIT 1
        """,
        (int(item_id),),
    )
    row = cursor.fetchone()
    return dict(row) if row else None


def assert_group_codes_match_job(cursor, item_id, category_code, service_code):
    category_code = str(category_code or "").strip()
    service_code = str(service_code or "").strip()
    if not category_code and not service_code:
        return "", ""
    job = latest_succeeded_job(cursor, item_id)
    if not job:
        raise ValueError("集团编码只能来自成功的赋码任务")
    result = _json_load(job["result_json"])
    expected_category = str(result.get("group_category_code") or "")
    expected_service = str(result.get("group_service_code") or "")
    if category_code != expected_category or service_code != expected_service:
        raise ValueError("集团编码必须与该行最近一次成功赋码的结果一致")
    return category_code, service_code


def standard_description_from_job(cursor, item_id):
    job = latest_succeeded_job(cursor, item_id)
    if not job:
        return ""
    return str(_json_load(job["result_json"]).get("standard_description") or "")


def start_coding_job(db_opener, job_id, ai_config):
    """本进程后台执行。请求本身不等待模型。"""

    def run():
        conn = db_opener()
        try:
            _set_status(conn, job_id, "running", "")
            conn.commit()
            _execute(conn, job_id, ai_config, supplement_text="")
            conn.commit()
        except Exception as exc:
            conn.rollback()
            _set_status(conn, job_id, "failed", str(exc))
            conn.commit()
        finally:
            conn.close()

    threading.Thread(target=run, name="coding-job-%s" % job_id, daemon=True).start()


def run_coding_job(conn, job_id, ai_config, supplement_text=""):
    """同步执行，供测试和补充信息使用。"""
    _set_status(conn, job_id, "running", "")
    _execute(conn, job_id, ai_config, supplement_text=supplement_text)


def confirm_category_rule(cursor, job_id, operator_name):
    """确认后把新的大中小类写回分类规则表。"""
    cursor.execute("SELECT * FROM coding_jobs WHERE id = ?", (int(job_id),))
    row = cursor.fetchone()
    if not row:
        raise ValueError("赋码任务不存在")
    if row["status"] != "succeeded":
        raise ValueError("只有成功的赋码任务可以保存分类参考")
    result = _json_load(row["result_json"])
    if not result.get("new_category"):
        raise ValueError("这次判断没有新的大中小类")
    save_confirmed_rule(cursor, result.get("proposed_rule") or {}, result.get("material_code") or "", operator_name)
    result["rule_saved"] = True
    cursor.execute(
        "UPDATE coding_jobs SET result_json = ?, updated_at = ? WHERE id = ?",
        (_dumps(result), now_text(), int(job_id)),
    )


def _execute(conn, job_id, ai_config, supplement_text):
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM coding_jobs WHERE id = ?", (int(job_id),))
    job = cursor.fetchone()
    if not job:
        raise ValueError("赋码任务不存在")
    cursor.execute("SELECT * FROM workflow_items WHERE id = ? AND form_id = ?", (job["item_id"], job["form_id"]))
    item = cursor.fetchone()
    if not item:
        raise ValueError("赋码行不存在")
    name = str(item["material_name"] or "").strip()
    if not name:
        raise ValueError("这一行没有物料名称")
    previous = _json_load(job["result_json"])
    complete = ai_config["complete"]
    query = " ".join(part for part in (name, item["brand_model"], item["spec"], item["unit"]) if part)
    if supplement_text:
        group = supplement_group_material(previous.get("group") or previous, supplement_text, complete)
    else:
        group = classify_group_material(query, complete)
    if group.get("status") in _SUPPLEMENT:
        _store(cursor, job_id, "needs_input", _group_payload(group, item), "")
        return
    if group.get("status") not in _FILLABLE or not group.get("group_category_code"):
        raise ValueError(group.get("message") or "集团分类失败")
    references, similar = reference_packet(cursor, name, item["brand_model"], item["spec"])
    decision = complete(
        WAREHOUSE_CODE_PROMPT,
        "物料：%s\n品牌型号：%s\n技术规格：%s\n单位：%s\n\n%s" % (
            name, item["brand_model"] or "", item["spec"] or "", item["unit"] or "", references,
        ),
    )
    code = judge_warehouse_code(cursor, name, decision)
    codes = group_codes_from_tool(group)
    payload = {
        "material_code": code,
        "rationale": str(decision.get("rationale") or ""),
        "similar": [
            {
                "material_code": row.get("material_code") or "",
                "name": row.get("name") or "",
                "brand_model": row.get("brand_model") or "",
                "spec": row.get("spec") or "",
            }
            for row in similar
        ],
        "new_category": bool(decision.get("new_category")),
        "proposed_rule": decision.get("proposed_rule") or {},
        "missing": group.get("missing") or [],
        "message": group.get("message") or "",
        "group": group,
    }
    payload.update(codes)
    _store(cursor, job_id, "succeeded", payload, "")


def _group_payload(group, item):
    payload = {
        "material_code": "",
        "rationale": "",
        "similar": [],
        "missing": group.get("missing") or [],
        "message": group.get("message") or "请补充集团分类所需属性",
        "group": group,
        "new_category": False,
        "proposed_rule": {},
    }
    payload.update(group_codes_from_tool(group))
    payload["line_name"] = item["material_name"]
    return payload


def _set_status(conn, job_id, status, error):
    conn.execute(
        "UPDATE coding_jobs SET status = ?, error = ?, updated_at = ? WHERE id = ?",
        (status, error or "", now_text(), int(job_id)),
    )


def _store(cursor, job_id, status, payload, error):
    cursor.execute(
        "UPDATE coding_jobs SET status = ?, result_json = ?, error = ?, updated_at = ? WHERE id = ?",
        (status, _dumps(payload), error or "", now_text(), int(job_id)),
    )


def _json_load(text):
    try:
        value = json.loads(text or "{}")
    except json.JSONDecodeError:
        return {}
    return value if isinstance(value, dict) else {}


def _dumps(value):
    return json.dumps(value or {}, ensure_ascii=False)
