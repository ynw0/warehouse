# -*- coding: utf-8 -*-
import json
import os
import urllib.error
import urllib.request

from flask import jsonify, request

from warehouse_suit.ai_client import (
    ai_base_url,
    http_error_detail,
    openai_chat_completion,
    openai_model_list,
)
from warehouse_suit.ai_materials import (
    ai_conversation_context,
    ai_duplicate_context,
    ai_duplicate_materials,
    ai_duplicate_response,
    ai_is_coding_request,
    ai_material_context,
    ai_material_tokens,
    ai_prefix_context,
    ai_production_context,
    ai_record_conversation,
    ai_similar_materials,
    ai_similar_production_items,
    validate_and_correct_codes,
)
from warehouse_suit.content import read_text_prefix
from warehouse_suit.material_service import next_material_code
from warehouse_suit.settings import get_setting, set_setting


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

    def ai_candidate_codes(cursor, question, limit=8):
        """Pre-compute the next available code for the top-matching prefixes.
        The AI should use these instead of guessing detail numbers."""
        cursor.execute(
            """
            SELECT material_code, name, brand_model, spec, category_name
            FROM materials WHERE LENGTH(material_code) = 14 ORDER BY material_code
            """
        )
        groups = {}
        for row in cursor.fetchall():
            code = str(row["material_code"] or "")
            if len(code) != 14 or not code.isdigit():
                continue
            prefix = code[:10]
            if prefix not in groups:
                groups[prefix] = {"prefix": prefix, "names": [], "category": "", "count": 0, "max_detail": 0}
            grp = groups[prefix]
            grp["count"] += 1
            name = str(row["name"] or "").strip()
            if name and name not in grp["names"] and len(grp["names"]) < 5:
                grp["names"].append(name)
            cat = str(row["category_name"] or "").strip()
            if cat and not grp["category"]:
                grp["category"] = cat
            detail = int(code[-4:]) if code[-4:].isdigit() else 0
            if detail > grp["max_detail"]:
                grp["max_detail"] = detail
        tokens = ai_material_tokens(question)
        scored = []
        for grp in groups.values():
            haystack = " ".join(grp["names"] + [grp["category"]]).lower()
            grp["score"] = sum(1 for t in tokens if t and t in haystack)
            if grp["score"] > 0:
                scored.append(grp)
        scored.sort(key=lambda g: (-g["score"], -g["count"]))
        candidates = []
        for grp in scored[:limit]:
            warehouse = grp["prefix"][2:4]
            major = grp["prefix"][4:6]
            middle = grp["prefix"][6:8]
            small = grp["prefix"][8:10]
            known_name = grp["names"][0] if grp["names"] else ""
            try:
                next_code, step = next_material_code(cursor, warehouse, major, middle, small, known_name)
            except Exception:
                max_d = grp["max_detail"]
                step = 1 if max_d % 10 != 0 or "芯片" in known_name else 10
                next_code = f"{grp['prefix']}{(max_d // step * step + step):04d}" if max_d else f"{grp['prefix']}{1:04d}" if step == 1 else f"{grp['prefix']}{10:04d}"
            candidates.append({
                "prefix": grp["prefix"],
                "code": next_code,
                "step": step,
                "category": grp["category"],
                "names": grp["names"][:3],
                "existing_count": grp["count"],
                "score": grp["score"],
            })
        if not candidates:
            return ""
        lines = ["以下是为该物料预计算的可分配编码（系统已校验无重复），请从中选择最接近的分类，如都不合适则说明理由后提出新的分类前缀：\n"]
        for c in candidates:
            wname = "研发材料库" if c["prefix"][2:4] == "20" else "办公用品库"
            code_parts = f"{c['prefix'][2:4]}/{c['prefix'][4:6]}/{c['prefix'][6:8]}/{c['prefix'][8:10]}"
            step_info = "按 1 递增" if c["step"] == 1 else f"按 {c['step']} 步进"
            lines.append(
                f"- {c['code']} | {wname} 大类{c['prefix'][4:6]} 中类{c['prefix'][6:8]} "
                f"小类{c['prefix'][8:10]} | {c['category']} | {step_info} | "
                f"已有 {c['existing_count']} 件（{'、'.join(c['names'])}…）"
            )
        return "\n".join(lines)


    def ai_skill_context(skill_path):
        skill_path = resolve_skill_path(skill_path)
        skill_dir = os.path.dirname(skill_path) if skill_path else os.path.join(BASE_DIR, "wuliao_skill")
        parts = []
        for label, rel_path, limit in [
            ("物料编码 SKILL（工作流、仓库码定义、Guardrails）", "SKILL.md", 6000),
            ("编码规则库（含完整中类/小类分类表）", os.path.join("references", "coding-rules.md"), 24000),
            ("当前数据库已使用前缀地图", os.path.join("references", "existing-category-map.md"), 10000),
            ("用户确认过的新分类规则", os.path.join("references", "learned-category-rules.md"), 4000),
        ]:
            path = skill_path if rel_path == "SKILL.md" else os.path.join(skill_dir, rel_path)
            text = read_text_prefix(path, limit)
            if text:
                parts.append(f"### {label}\n{text}")
        return "\n\n".join(parts)

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
        if not AI_ENABLED:
            return jsonify({"success": False, "error": "当前版本未启用 AI 功能"}), 404
        data = request.get_json(force=True)
        question = str(data.get("question") or "").strip()
        if not question:
            return jsonify({"success": False, "error": "请输入问题"}), 400
        session_id = str(data.get("session_id") or "default").strip()
        conn = get_db()
        cursor = conn.cursor()
        base_url = ai_base_url(get_setting(cursor, "ai_base_url", DEFAULT_AI_BASE_URL))
        model = get_setting(cursor, "ai_model", DEFAULT_AI_MODEL)
        api_key = get_setting(cursor, "ai_api_key", DEFAULT_AI_API_KEY)
        skill_path = resolve_skill_path(get_setting(cursor, "ai_skill_path", DEFAULT_SKILL_PATH))
        skill_text = ai_skill_context(skill_path)
        matches = ai_similar_materials(cursor, question, 12)
        duplicate_hits = ai_duplicate_materials(question, matches)
        if duplicate_hits and ai_is_coding_request(question):
            answer = ai_duplicate_response(duplicate_hits)
            ai_record_conversation(session_id, question, answer)
            conn.close()
            return jsonify({"success": True, "answer": answer})
        duplicate_context = ai_duplicate_context(duplicate_hits)
        material_context = ai_material_context(matches)
        production_context = ai_production_context(ai_similar_production_items(cursor, question, 12))
        prefix_context = ai_prefix_context(cursor, question)
        candidate_context = "" if duplicate_hits else ai_candidate_codes(cursor, question)
        conv_context = ai_conversation_context(session_id)
        user_name = ""
        try:
            user = current_user(cursor)
            if user:
                user_name = user.get("display_name") or ""
        except Exception:
            pass
        if base_url:
            try:
                if not model:
                    model_error = ""
                    for suffix in ("/model", "/models"):
                        try:
                            model_req = urllib.request.Request(
                                ai_base_url(base_url).rstrip("/") + suffix,
                                headers={"Authorization": f"Bearer {api_key}"} if api_key else {},
                                method="GET",
                            )
                            with urllib.request.urlopen(model_req, timeout=8) as response:
                                model_body = json.loads(response.read().decode("utf-8"))
                            if isinstance(model_body, dict):
                                models = model_body.get("data") or model_body.get("models") or []
                                if isinstance(model_body.get("id"), str):
                                    model = model_body["id"]
                                elif isinstance(models, list) and models:
                                    non_embedding = [item for item in models if "embed" not in str(item).lower()]
                                    first = (non_embedding or models)[0]
                                    model = first.get("id") if isinstance(first, dict) else str(first)
                            elif isinstance(model_body, list) and model_body:
                                first = model_body[0]
                                model = first.get("id") if isinstance(first, dict) else str(first)
                            if model:
                                break
                        except Exception as exc:
                            model_error = str(exc)
                if not model:
                    raise ValueError("AI 模型名为空，且未能从 /model 或 /models 自动获取" + (f"：{model_error}" if model_error else ""))

                system_prompt = f"""你是仓库物料管理系统的智能助手，运行在生产环境。严格遵守物料编码规则是你的第一优先级。

    ## ⚠️ 铁律（违反任何一条即视为错误输出）

    1. **编码 14 位结构不可变**：10 + 仓库码(2位) + 大类(2) + 中类(2) + 小类(2) + 明细(4位)。如 10200101010020
    2. **仓库码只有 10 和 20**：10=办公用品库，20=研发材料库
    3. **禁止使用 00 分类**：除非编码规则中明确标注的个别情况
    4. **大类码必须来自规则表**：01=电子元件、02=结构件、03=核心模块、04=连接线缆、05=安全防护、06=整机设备、10=食品、17=布制品、22=纸制品、23=记录本、24=办公收纳、26=卫生园艺、29=日用品、30=塑料制品、34=工具五金、37=搬运设备、39=信息化配件、40=计算机硬件、41=办公设备、42=清洁用具、59=家具、90=药品
    5. **仓库与分类一致性**：研发材料的大类不能出现在办公用品库，反之亦然（以数据库已有记录为准）
    6. **同大类同中类同小类不可变更**：同一物料不同规格/型号，前10位必须相同，仅改明细号
    7. **明细号规则**：常规 0010→0020→0030（步进 10），紧密规格 +1，芯片类 0001→0002（步进 1）
    8. **禁止重复编码**：输出前必须确认编码未被使用
    9. **存疑先问**：缺信息或不确定分类时，先向用户确认，不要猜测
    10. **禁止降级分类**：已有的细分小类不得合并为更粗的分类

    ## 编码工作流程（按顺序，缺信息停）

    ### 第1步：理解物料
    提取：名称、品牌/型号、规格、用途、目标仓库

    ### 第2步：查重复
    查【物料匹配结果】和【编码参考表】→ 如有相似物料列出来问用户

    ### 第3步：定仓库和大类（对照铁律第2、4、5条）
    ### 第4步：定中类和小类（参照【编码规则资料】中的中类/小类表）
    ### 第5步：定编码 — **优先从【推荐可用编码】中选择**（系统已按规则预计算）
    ### 第6步：输出前自查铁律 10 条，全部满足才输出

    {conv_context}

    {duplicate_context}

    【物料匹配结果】
    {material_context}

    【半成品/成品匹配结果】
    {production_context}

    {candidate_context}

    【编码参考表】
    {prefix_context}

    【物料编码规则资料（含完整分类表）】
    {skill_text}""".strip()

                payload = {
                    "model": model,
                    "temperature": 0.3,
                    "max_tokens": 4096,
                    "messages": [
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": question},
                    ],
                }
                try:
                    body = openai_chat_completion(base_url, api_key, payload)
                except urllib.error.HTTPError as exc:
                    detail = http_error_detail(exc)
                    lower_detail = detail.lower()
                    retry_payload = dict(payload)
                    should_retry = False
                    if "max_tokens" in lower_detail and "max_completion_tokens" in lower_detail:
                        retry_payload["max_completion_tokens"] = retry_payload.pop("max_tokens", 4096)
                        should_retry = True
                    if "temperature" in lower_detail:
                        retry_payload.pop("temperature", None)
                        should_retry = True
                    if not should_retry:
                        raise RuntimeError(detail)
                    try:
                        body = openai_chat_completion(base_url, api_key, retry_payload)
                    except urllib.error.HTTPError as retry_exc:
                        raise RuntimeError(http_error_detail(retry_exc))
                message = body.get("choices", [{}])[0].get("message", {})
                answer = message.get("content") or message.get("reasoning_content") or ""

                # Validate and optionally self-correct (max 2 refinement rounds)
                for _round in range(2):
                    validated = validate_and_correct_codes(
                        cursor,
                        answer,
                        allowed_existing_codes=[item.get("material_code") for item in duplicate_hits],
                    )
                    if validated == answer:
                        answer = validated
                        break
                    # Extraction: separate the answer body from the appended validation notes
                    parts = validated.split("\n\n---\n## 编码自动校验\n")
                    if len(parts) == 2:
                        clean_answer = parts[0].strip()
                        issues = parts[1].strip()
                        # Send issues back to AI for correction
                        correction_payload = {
                            "model": model,
                            "temperature": 0.2,
                            "max_tokens": 2048,
                            "messages": [
                                {"role": "system", "content": system_prompt},
                                {"role": "user", "content": question},
                                {"role": "assistant", "content": clean_answer},
                                {"role": "user", "content": f"你上面的回复中编码有问题：\n{issues}\n\n请修正这些编码问题，直接给出修正后的完整回复。"},
                            ],
                        }
                        try:
                            correction_body = openai_chat_completion(base_url, api_key, correction_payload)
                            corrected_answer = correction_body.get("choices", [{}])[0].get("message", {}).get("content") or ""
                            if corrected_answer:
                                answer = corrected_answer
                                continue
                        except Exception:
                            pass
                    answer = validated
                    break

                ai_record_conversation(session_id, question, answer)

                conn.close()
                return jsonify({"success": True, "answer": answer or "AI 未返回内容"})
            except Exception as exc:
                fallback_error = http_error_detail(exc)
        else:
            fallback_error = ""

        answer_lines = []
        if fallback_error:
            answer_lines.append(f"\u26a0\ufe0f AI 模型暂不可用：{fallback_error}")
        if matches:
            answer_lines.append("## 本地物料匹配结果（离线模式）\n")
            for idx, item in enumerate(matches, 1):
                score = int(item.get("_score") or 0)
                indicator = "\u2605\u2605\u2605" if score >= 25 else "\u2605\u2605" if score >= 12 else "\u2605"
                answer_lines.append(
                    f"{idx}. {indicator} **{item.get('material_code') or '-'}** {item.get('name') or '-'}\n"
                    f"   品牌型号：{item.get('brand_model') or '-'} | 规格：{item.get('spec') or '-'}\n"
                    f"   库存：{item.get('quantity') or 0:g}{item.get('unit') or ''} | "
                    f"位置：{item.get('shelf_name') or '-'} {item.get('layer_number') or ''}{item.get('zone_name') or ''}"
                )
        else:
            answer_lines.append("未在物料库中匹配到相关物料。可以尝试：\n- 使用更精确的物料名称或编号\n- 在验收页面新建物料\n- 提供仓库码/大类/中类让我辅助生成编码")
        conn.close()
        return jsonify({"success": True, "answer": "\n".join(answer_lines)})
