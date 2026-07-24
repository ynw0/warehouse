"""Material-assistant matching and context helpers."""

from __future__ import annotations

import re


AI_STOP_WORDS = {
    "帮我查", "帮查", "查询", "查一下", "查", "库存", "余量", "物料", "请问",
    "帮我", "一下", "编码", "名称", "还有", "吗", "能不能", "能否", "多少",
    "有吗", "看看", "看一下", "看看还有没有", "我需要", "想找", "找个", "找一个",
    "有没有", "是否", "是什么", "什么意思", "怎样", "如何", "怎么",
}

AI_SYNONYMS = {
    "贴片": ["SMD", "SMT", "片式"],
    "电容": ["电容器", "CAP", "MLCC", "瓷片电容", "电解电容", "钽电容"],
    "电阻": ["电阻器", "RES", "贴片电阻", "插件电阻", "排阻"],
    "电感": ["电感器", "IND", "磁珠", "线圈", "bead"],
    "二极管": ["DIODE", "整流管", "稳压管", "LED"],
    "三极管": ["晶体三极管", "BJT", "晶体管"],
    "MOS": ["场效应管", "MOSFET", "MOS管"],
    "芯片": ["IC", "集成电路", "MCU", "SOC", "CPU", "FPGA", "驱动芯片"],
    "连接器": ["接插件", "排针", "排母", "端子", "插座", "connector", "FPC连接器", "USB", "HDMI"],
    "线缆": ["线", "线束", "排线", "电源线", "数据线", "cable", "FPC"],
    "开发板": ["DEMO板", "评估板", "核心板", "主板"],
    "传感器": ["sensor", "温度传感器", "惯性传感器", "IMU", "陀螺仪", "加速度计"],
    "电机": ["马达", "motor", "步进电机", "舵机"],
    "电池": ["蓄电池", "锂电池", "battery", "纽扣电池"],
    "螺丝": ["螺钉", "螺栓", "螺母", "紧固件", "screw"],
    "工具": ["扳手", "钳子", "螺丝刀", "剪刀", "电钻", "焊台"],
    "办公用品": ["文具", "笔", "文件夹", "胶带", "订书机", "打印纸"],
    "清洁": ["清洁剂", "拖把", "扫帚", "抹布", "洗洁精"],
}

AI_DOMAIN_TERMS = [
    "电阻", "电容", "电感", "磁珠", "晶振", "芯片", "二极管", "三极管", "场效应管", "MOS",
    "传感器", "连接器", "接插件", "线缆", "线束", "排线", "FPC", "开发板", "镜头", "电机",
    "螺丝", "螺钉", "螺母", "垫片", "结构件", "手板", "导热", "硅胶", "胶水", "针头",
    "电池", "转换器", "读卡器", "U盘", "硬盘", "内存", "插座", "开关", "按钮", "蜂鸣器",
    "纸", "标签", "文件夹", "胶带", "笔", "清洁", "工具", "药", "桌", "椅", "垃圾袋",
]

AI_CONVERSATIONS = {}

def ai_search_terms(question):
    text = str(question or "").strip()
    # Multi-strategy: keep original, cleaned, expanded by synonyms
    cleaned = text
    for token in AI_STOP_WORDS:
        cleaned = cleaned.replace(token, " ")
    terms = []
    # Strategy 1: alphanumeric tokens (brand names, part numbers)
    for match in re.findall(r"[A-Za-z0-9]+(?:[-_/\.][A-Za-z0-9]+)*", text):
        if len(match) >= 2 and match.lower() not in terms:
            terms.append(match.lower())
    # Strategy 2: Chinese meaningful chunks (2+ chars)
    for part in cleaned.replace("，", " ").replace(",", " ").replace("。", " ").split():
        part = part.strip()
        if len(part) >= 2 and part not in terms:
            terms.append(part)
    # Strategy 3: synonym expansion (top 3 synonyms max)
    expanded = list(terms)
    for term in terms[:8]:
        for base, syns in AI_SYNONYMS.items():
            if term in [base] + syns:
                for s in syns:
                    if s not in expanded:
                        expanded.append(s)
                break
            if base in term or any(s in term for s in syns):
                for s in syns[:3]:
                    if s not in expanded:
                        expanded.append(s)
                if base not in expanded:
                    expanded.append(base)
    deduped = []
    for t in expanded + (terms if len(terms) <= 3 else []):
        if t and t not in deduped:
            deduped.append(t)
    return deduped[:20] or [text]


def ai_material_tokens(text):
    raw = str(text or "").lower()
    tokens = []
    for token in re.findall(r"[a-z0-9]+(?:[-_/\.][a-z0-9]+)*|[\u4e00-\u9fff]{2,}", raw, flags=re.I):
        token = token.strip().lower()
        if len(token) >= 2 and token not in tokens:
            tokens.append(token)
    for term in AI_DOMAIN_TERMS:
        if term.lower() in raw and term.lower() not in tokens:
            tokens.append(term.lower())
    return tokens[:80]


def ai_normalize_compare_text(value):
    raw = str(value or "").lower()
    raw = raw.replace("×", "x").replace("*", "x").replace("＊", "x")
    raw = raw.replace("（", "(").replace("）", ")")
    return re.sub(r"[^a-z0-9\u4e00-\u9fff]+", "", raw)


def ai_material_duplicate_score(question, material):
    q_norm = ai_normalize_compare_text(question)
    if not q_norm:
        return 0, []
    reasons = []
    code = ai_normalize_compare_text(material.get("material_code"))
    name = ai_normalize_compare_text(material.get("name"))
    brand = ai_normalize_compare_text(material.get("brand_model"))
    spec = ai_normalize_compare_text(material.get("spec"))
    score = 0
    if code and code in q_norm:
        return 100, ["用户文本包含已有物料编号"]
    name_match = bool(name and len(name) >= 2 and name in q_norm)
    brand_match = bool(brand and len(brand) >= 2 and brand in q_norm)
    spec_match = bool(spec and len(spec) >= 2 and spec in q_norm)
    if not spec_match and spec:
        spec_tokens = [ai_normalize_compare_text(token) for token in ai_material_tokens(material.get("spec"))]
        spec_tokens = [token for token in spec_tokens if len(token) >= 2]
        if spec_tokens:
            hit_count = sum(1 for token in spec_tokens if token in q_norm)
            spec_match = hit_count == len(spec_tokens) or (len(spec_tokens) >= 3 and hit_count / len(spec_tokens) >= 0.8)
    if name_match:
        score += 35
        reasons.append("名称一致")
    if brand:
        if brand_match:
            score += 25
            reasons.append("品牌型号一致")
        else:
            score -= 8
    if spec:
        if spec_match:
            score += 40
            reasons.append("技术规格/尺寸一致")
        else:
            score -= 12
    if name_match and spec_match and (not brand or brand_match):
        score = max(score, 92)
        reasons.append("名称和规格已可判定为同一物料")
    elif brand_match and spec_match:
        score = max(score, 84)
        reasons.append("品牌型号和规格完全匹配")
    elif not spec_match and not code:
        score = min(score, 60)
    return max(0, score), list(dict.fromkeys(reasons))


def ai_duplicate_materials(question, materials, limit=3):
    hits = []
    for material in materials or []:
        score, reasons = ai_material_duplicate_score(question, material)
        if score >= 80:
            item = dict(material)
            item["_duplicate_score"] = score
            item["_duplicate_reasons"] = reasons
            hits.append(item)
    hits.sort(key=lambda item: (-item.get("_duplicate_score", 0), -(item.get("_score") or 0)))
    return hits[:limit]


def ai_is_coding_request(question):
    text = str(question or "")
    markers = ["编码", "编号", "料号", "物料号", "新到", "新增", "新物料", "录入", "入库", "分类", "生成", "保存", "申请"]
    return any(marker in text for marker in markers)


def ai_duplicate_context(duplicates):
    if not duplicates:
        return ""
    lines = [
        "【必须优先处理的重复物料】",
        "系统检测到用户描述与已有物料高度一致。若用户是在为新到/新增/待录入物料要编号，必须先提示沿用原编号，禁止继续生成新物料编号。只有用户明确说明它不是同一物料并给出差异后，才可以进入新编码流程。",
    ]
    for idx, item in enumerate(duplicates, 1):
        reasons = "、".join(item.get("_duplicate_reasons") or ["高置信匹配"])
        lines.append(
            f"{idx}. 原编号 {item.get('material_code') or '-'} | {item.get('name') or '-'} | "
            f"品牌型号：{item.get('brand_model') or '-'} | 技术规格：{item.get('spec') or '-'} | "
            f"库存：{item.get('quantity') or 0:g}{item.get('unit') or ''} | "
            f"位置：{item.get('shelf_name') or '-'} {item.get('layer_number') or ''}{item.get('zone_name') or ''} | "
            f"依据：{reasons}"
        )
    return "\n".join(lines)


def ai_duplicate_response(duplicates):
    lines = [
        "我先做了查重：这个物料和系统已有物料高度一致，应该沿用原编号，不要再新编一个编号。",
        "",
    ]
    for idx, item in enumerate(duplicates, 1):
        reasons = "、".join(item.get("_duplicate_reasons") or ["高置信匹配"])
        lines.append(
            f"{idx}. 原编号：**{item.get('material_code') or '-'}**\n"
            f"   名称：{item.get('name') or '-'}\n"
            f"   品牌型号：{item.get('brand_model') or '-'}\n"
            f"   技术规格：{item.get('spec') or '-'}\n"
            f"   库存/位置：{item.get('quantity') or 0:g}{item.get('unit') or ''}，"
            f"{item.get('shelf_name') or '-'} {item.get('layer_number') or ''}{item.get('zone_name') or ''}\n"
            f"   判断依据：{reasons}"
        )
    lines.append("")
    lines.append("如果它实际不是同一物料，请补充差异点，比如品牌型号、技术规格、尺寸、封装、材质或用途，我再按新物料重新分类编号。")
    return "\n".join(lines)


def ai_record_conversation(session_id, question, answer):
    history = AI_CONVERSATIONS.setdefault(session_id, [])
    history.append({"role": "user", "content": question})
    history.append({"role": "assistant", "content": answer})
    if len(history) > 20:
        AI_CONVERSATIONS[session_id] = history[-20:]


def ai_material_similarity(question, material):
    text = str(question or "").lower()
    haystack = " ".join(
        str(material.get(key) or "").lower()
        for key in ["material_code", "name", "brand_model", "spec", "purchase_applicant", "category_name", "material_type"]
    )
    score = 0
    tokens = ai_material_tokens(question)
    for token in tokens:
        if token and token in haystack:
            score += 2 if len(token) <= 3 else 4
    # Code prefix match (e.g. "102001" matches material_code prefix)
    code_prefixes = re.findall(r"\d{4,12}", text)
    mat_code = str(material.get("material_code") or "").lower()
    for prefix in code_prefixes:
        if mat_code.startswith(prefix):
            score += len(prefix)
    for key, weight in [("name", 10), ("brand_model", 8), ("spec", 8), ("material_code", 12)]:
        value = str(material.get(key) or "").strip().lower()
        if value and value in text:
            score += weight
    # Partial match bonus: name contains query substring
    name = str(material.get("name") or "").lower()
    for term in tokens:
        if len(term) >= 3 and term in name:
            score += 6
    return score


def ai_similar_materials(cursor, question, limit=12):
    # Multi-pass search: exact first, then fuzzy
    tokens = ai_search_terms(question)
    all_matches = {}
    for i, term in enumerate(tokens[:8]):
        like = f"%{term}%"
        cursor.execute(
            """
            SELECT m.id, m.material_code, m.name, m.brand_model, m.spec, m.unit,
                   m.purchase_applicant, m.category_name, m.material_type,
                   COALESCE(i.quantity, 0) AS quantity,
                   s.name AS shelf_name, mp.layer_number, mp.zone_name
            FROM materials m
            LEFT JOIN inventory i ON i.material_id = m.id
            LEFT JOIN material_positions mp ON mp.material_id = m.id
            LEFT JOIN shelves s ON s.id = mp.shelf_id
            WHERE m.material_code LIKE ? OR m.name LIKE ? OR m.brand_model LIKE ?
               OR m.spec LIKE ? OR m.purchase_applicant LIKE ?
            ORDER BY m.id DESC
            LIMIT 30
            """,
            (like, like, like, like, like),
        )
        for row in cursor.fetchall():
            item = dict(row)
            if item["id"] not in all_matches:
                item["_match_rank"] = i  # earlier terms = higher priority
                all_matches[item["id"]] = item
    scored = []
    for item in all_matches.values():
        base_score = ai_material_similarity(question, item)
        rank_bonus = max(0, 8 - item.get("_match_rank", 0)) * 2
        item["_score"] = base_score + rank_bonus
        if item["_score"] > 0:
            scored.append(item)
    scored.sort(key=lambda item: (-item["_score"], item.get("material_code") or ""))
    return scored[:limit]


def ai_material_context(materials):
    if not materials:
        return "未在物料库中找到高度匹配的结果。"
    lines = [
        "【疑似已有物料 — 请逐条判断是否与用户录入的物料重复】"
    ]
    for idx, item in enumerate(materials, 1):
        extra = ""
        if int(item.get("_score") or 0) >= 25:
            extra = " ⚠️ 高度相似"
        lines.append(
            f"{idx}. {item.get('material_code') or '-'} | {item.get('name') or '-'} | "
            f"品牌型号：{item.get('brand_model') or '-'} | 规格：{item.get('spec') or '-'} | "
            f"库存：{item.get('quantity') or 0:g}{item.get('unit') or ''} | "
            f"位置：{item.get('shelf_name') or '-'} {item.get('layer_number') or ''}{item.get('zone_name') or ''}"
            f"{extra}"
        )
    return "\n".join(lines)


def ai_similar_production_items(cursor, question, limit=12):
    tokens = ai_search_terms(question)
    if not tokens:
        tokens = [question.strip()] if question.strip() else []
    matches = {}
    for rank, term in enumerate(tokens[:8]):
        like = f"%{term}%"
        cursor.execute(
            """
            SELECT '半成品' AS item_type, id, serial_no AS item_code, name AS item_name,
                   spec, unit, quantity,
                   MAX(0, COALESCE(quantity, 0) - COALESCE(used_quantity, 0) - COALESCE(borrowed_quantity, 0)) AS available_quantity
            FROM semifinished_inventory
            WHERE serial_no LIKE ? OR name LIKE ? OR spec LIKE ?
            ORDER BY id DESC
            LIMIT 20
            """,
            (like, like, like),
        )
        for row in cursor.fetchall():
            item = dict(row)
            matches.setdefault(("semi", item["id"]), {**item, "_rank": rank})
        cursor.execute(
            """
            SELECT '成品' AS item_type, id, serial_no AS item_code, product_name AS item_name,
                   spec, unit, quantity,
                   MAX(0, COALESCE(quantity, 0) - COALESCE(borrowed_quantity, 0)) AS available_quantity
            FROM finished_good_inventory
            WHERE serial_no LIKE ? OR product_name LIKE ? OR spec LIKE ?
            ORDER BY id DESC
            LIMIT 20
            """,
            (like, like, like),
        )
        for row in cursor.fetchall():
            item = dict(row)
            matches.setdefault(("finished", item["id"]), {**item, "_rank": rank})
    rows = list(matches.values())
    rows.sort(key=lambda item: (item.get("_rank", 99), item.get("item_code") or ""))
    return rows[:limit]


def ai_production_context(items):
    if not items:
        return "未在半成品/成品库中找到高度匹配的结果。"
    lines = ["【半成品/成品库存匹配结果】"]
    for idx, item in enumerate(items, 1):
        lines.append(
            f"{idx}. {item.get('item_type')} | {item.get('item_code') or '-'} | {item.get('item_name') or '-'} | "
            f"规格：{item.get('spec') or '-'} | 可用：{item.get('available_quantity') or 0:g}{item.get('unit') or ''}"
        )
    return "\n".join(lines)


def ai_prefix_context(cursor, question, limit=24):
    cursor.execute(
        """
        SELECT material_code, name, brand_model, spec, category_name, material_type
        FROM materials
        WHERE LENGTH(material_code) = 14
        ORDER BY material_code
        """
    )
    groups = {}
    for row in cursor.fetchall():
        item = dict(row)
        code = str(item.get("material_code") or "")
        if len(code) != 14 or not code.isdigit():
            continue
        prefix = code[:10]
        detail = int(code[-4:])
        group = groups.setdefault(
            prefix,
            {
                "prefix": prefix,
                "warehouse": code[2:4],
                "major": code[4:6],
                "middle": code[6:8],
                "small": code[8:10],
                "count": 0,
                "details": [],
                "max_detail": 0,
                "names": [],
                "full_examples": [],
                "category_names": [],
                "score": 0,
            },
        )
        group["count"] += 1
        group["details"].append(detail)
        if detail > group["max_detail"]:
            group["max_detail"] = detail
        for key, target in [("name", "names"), ("category_name", "category_names")]:
            value = str(item.get(key) or "").strip()
            if value and value not in group[target] and len(group[target]) < 8:
                group[target].append(value)
        if len(group["full_examples"]) < 4:
            example = f"{code} | {item.get('name') or ''} | {item.get('brand_model') or ''} | {item.get('spec') or ''}".strip(" |")
            if example not in group["full_examples"]:
                group["full_examples"].append(example)
    tokens = ai_material_tokens(question)
    for group in groups.values():
        group["details"].sort()
        haystack = " ".join(group["names"] + group["category_names"] + group["full_examples"]).lower()
        group["score"] = sum(1 for token in tokens if token and token in haystack)
    ranked = sorted(groups.values(), key=lambda item: (-item["score"], -item["count"], item["prefix"]))
    if not ranked:
        return "（数据库暂无已编码物料）"
    warehouse_names = {"10": "办公用品库", "20": "研发材料库"}
    lines = []
    for group in ranked[:limit]:
        wname = warehouse_names.get(group["warehouse"], f"仓库{group['warehouse']}")
        # Determine increment pattern
        details = group["details"]
        name_text = " ".join(group["names"]).lower()
        chip_like = "芯片" in name_text or "ic" in name_text or "mcu" in name_text or (
            group["major"] == "01" and group["middle"] in {"06", "09", "14", "17", "19", "20", "21", "40"}
        )
        if chip_like:
            step_hint = "芯片类，明细号从 0001 起按 1 递增"
            next_detail = max(details) + 1 if details else 1
            step_size = 1
        elif max(details) % 10 == 0:
            step_hint = "普通物料，按 10 步进"
            next_detail = (max(details) // 10 + 1) * 10 if details else 10
            step_size = 10
        else:
            step_hint = "紧密规格，按 1 递增"
            next_detail = max(details) + 1 if details else 1
            step_size = 1
        # Show detail range
        detail_info = f"明细号：{'~'.join(map(str, [min(details), max(details)]))}（共{len(details)}个），下一个可用 ≈ {next_detail:04d}"
        lines.append(
            f"## {wname} / {group['prefix']} ({group['major']}{group['middle']}{group['small']}) | {step_hint}"
        )
        lines.append(f"  已用 {group['count']} 件 | {detail_info}")
        for ex in group["full_examples"][:3]:
            lines.append(f"  • {ex}")
    return "\n".join(lines)


def ai_conversation_context(session_id, max_turns=6):
    history = AI_CONVERSATIONS.get(session_id, [])
    if not history:
        return ""
    recent = history[-max_turns:]
    lines = ["【对话历史】"]
    for msg in recent:
        role_label = "用户" if msg["role"] == "user" else "助手"
        content = str(msg.get("content") or "")
        if len(content) > 600:
            content = content[:600] + "..."
        lines.append(f"{role_label}：{content}")
    return "\n".join(lines)

def validate_and_correct_codes(cursor, answer, allowed_existing_codes=None):
    """Post-process AI response: find all 14-digit codes, validate structure,
    check for duplicates, verify classification against rules/DB, correct issues, append fix notes."""
    if not answer:
        return answer
    allowed_existing_codes = {str(code) for code in (allowed_existing_codes or []) if code}
    codes_found = list(dict.fromkeys(re.findall(r"\b10\d{12}\b", answer)))
    if not codes_found:
        return answer
    # Load DB prefix map and major categories for classification validation
    cursor.execute("SELECT DISTINCT SUBSTR(material_code,1,10) AS px, SUBSTR(material_code,3,2) AS wh, SUBSTR(material_code,5,2) AS mj, COUNT(*) AS cnt, GROUP_CONCAT(DISTINCT name) AS names FROM materials WHERE LENGTH(material_code)=14 GROUP BY px ORDER BY px")
    db_prefixes = {row["px"]: {"warehouse": row["wh"], "major": row["mj"], "count": row["cnt"], "names": str(row["names"] or "")} for row in cursor.fetchall()}
    # Known valid major codes from coding rules
    known_majors = {"01","02","03","04","05","06","10","17","22","23","24","26","29","30","34","37","39","40","41","42","59","90"}
    corrections = []
    for code in codes_found:
        warehouse = code[2:4]
        major = code[4:6]
        middle = code[6:8]
        small = code[8:10]
        prefix = code[:10]
        notes = []
        try:
            # --- Structure & warehouse check ---
            code_valid = code.isdigit() and len(code) == 14 and code.startswith("10")
            valid_warehouse = warehouse in {"10", "20"}
            if not code_valid:
                notes.append("编码结构不符合 14 位规则")
            if not valid_warehouse:
                notes.append(f"仓库码 {warehouse} 无效（只能 10=办公用品库 或 20=研发材料库）")
            # --- Classification check ---
            if code_valid and valid_warehouse:
                if major == "00" or middle == "00":
                    notes.append(f"不允许使用 00 分类（除非规则明确要求）")
                if major in known_majors:
                    # Check consistency with DB: same major should have same warehouse across existing codes
                    for db_px, db_info in db_prefixes.items():
                        if db_info["major"] == major and db_info["warehouse"] != warehouse:
                            notes.append(
                                f"仓库码与数据库不一致：大类 {major} 在数据库中属于"
                                f"{'研发材料库' if db_info['warehouse']=='20' else '办公用品库'}（仓库{db_info['warehouse']}），"
                                f"但你使用了仓库{warehouse}"
                            )
                            break
                else:
                    notes.append(f"大类 {major} 不在已知分类表中，请确认是否为新增分类")
                # Check prefix consistency
                if prefix in db_prefixes:
                    db_info = db_prefixes[prefix]
                    if db_info["warehouse"] != warehouse:
                        notes.append(
                            f"仓库码与已有前缀冲突：{prefix} 在数据库中属于"
                            f"{'研发材料库' if db_info['warehouse']=='20' else '办公用品库'}，但你用了仓库{warehouse}"
                        )
                    if db_info["major"] != major:
                        notes.append(f"大类码与已有前缀 {prefix} 不一致（DB 记录为 {db_info['major']}）")
            if notes:
                corrections.append(f"\u26a0\ufe0f 编码 {code} 分类问题：{'；'.join(notes)}")
                continue
            # --- Duplicate check ---
            cursor.execute("SELECT material_code, name, brand_model, spec FROM materials WHERE material_code = ?", (code,))
            existing = cursor.fetchone()
            if existing:
                if code in allowed_existing_codes:
                    continue
                ex_name = existing["name"] or ""
                ex_brand = existing["brand_model"] or ""
                ex_spec = existing["spec"] or ""
                cursor.execute(
                    "SELECT material_code FROM materials WHERE material_code LIKE ? ORDER BY material_code DESC LIMIT 1",
                    (f"{prefix}____",),
                )
                last_row = cursor.fetchone()
                if last_row and last_row["material_code"][-4:].isdigit():
                    last_detail = int(last_row["material_code"][-4:])
                    name_text = str(ex_name or "").lower()
                    chip_like = "芯片" in name_text or "ic" in name_text or "ddr" in name_text
                    if chip_like:
                        next_detail = last_detail + 1
                    elif last_detail % 10 == 0:
                        next_detail = (last_detail // 10 + 1) * 10
                    else:
                        next_detail = last_detail + 1
                else:
                    next_detail = 10
                corrected = f"{prefix}{next_detail:04d}"
                cursor.execute("SELECT 1 FROM materials WHERE material_code = ?", (corrected,))
                if cursor.fetchone():
                    for offset in range(1, 10000):
                        alt = f"{prefix}{(next_detail + offset):04d}"
                        cursor.execute("SELECT 1 FROM materials WHERE material_code = ?", (alt,))
                        if not cursor.fetchone():
                            corrected = alt
                            break
                corrections.append(
                    f"\u26a0\ufe0f 编码 {code} 已存在（{ex_name} / {ex_brand} / {ex_spec}），已修正为 {corrected}"
                )
                answer = answer.replace(code, corrected)
            elif prefix not in db_prefixes:
                corrections.append(
                    f"\u2139\ufe0f 编码 {code} 为全新分类前缀，请确认大类/中类/小类定义正确"
                )
        except Exception as exc:
            corrections.append(f"\u26a0\ufe0f 编码 {code} 校验失败：{exc}")
    if corrections:
        answer += "\n\n---\n## \u7f16\u7801\u81ea\u52a8\u6821\u9a8c\n" + "\n".join(corrections)
    return answer


