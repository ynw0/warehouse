"""集团分类管线。模型只能复制候选里的编码，候选外直接失败。"""

from __future__ import annotations

import json
import re

from warehouse_suit.group_coding.object_semantics import ObjectSemanticsError, validate_extracted_object
from warehouse_suit.group_coding.prompts import (
    CATEGORY_CHOICE_SYSTEM_PROMPT,
    CATEGORY_LEVEL_RANKING_SYSTEM_PROMPT,
    MATERIAL_INFORMATION_SYSTEM_PROMPT,
    SERVICE_CATEGORY_CHOICE_SYSTEM_PROMPT,
    TEMPLATE_NORMALIZATION_SYSTEM_PROMPT,
)


def parse_json_object(text):
    """取出模型回复中的 JSON 对象。不是对象就失败，不猜测编码。"""
    raw = str(text or "").strip()
    if raw.startswith("```"):
        raw = re.sub(r"^```(?:json)?", "", raw, count=1).strip()
        if raw.endswith("```"):
            raw = raw[:-3].strip()
    start = raw.find("{")
    end = raw.rfind("}")
    if start < 0 or end < start:
        raise ValueError("模型没有返回 JSON")
    try:
        data = json.loads(raw[start:end + 1])
    except json.JSONDecodeError as exc:
        raise ValueError("模型返回的 JSON 不合法") from exc
    if not isinstance(data, dict):
        raise ValueError("模型返回的不是 JSON 对象")
    return data


def _dumps(value):
    return json.dumps(value, ensure_ascii=False, indent=2)


def _material_from_llm(raw_input, payload):
    return {
        "原始输入": raw_input,
        "通用名称": str(payload.get("general_name") or "").strip(),
        "对象本体": str(payload.get("object_head") or "").strip(),
        "对象形态": str(payload.get("object_form") or "").strip(),
        "对象修饰": list(payload.get("object_qualifiers") or []),
        "品牌": payload.get("brand"),
        "型号": payload.get("model"),
        "材质": payload.get("material"),
        "规格": list(payload.get("specifications") or []),
        "用途": payload.get("usage"),
        "其他可核实属性": dict(payload.get("other_attributes") or {}),
    }


class GroupPipeline:
    """单条物料的集团分类。complete(system, user) 必须返回 JSON 对象。"""

    def __init__(self, catalog, selection, templates, services):
        self.catalog = catalog
        self.selection = selection
        self.templates = templates
        self.services = services

    def process(self, query, complete):
        calls = 0
        try:
            calls += 1
            material = self._extract(query, complete)
            calls += 1
            large_options = self.selection.large_options()
            if not large_options:
                return _failed("category_no_match", "没有已写明选中条件的大类", material, calls)
            large, _rationale = self._select_level("一级大类", material, large_options, complete)
            if not large:
                return _failed("category_no_match", "一级大类没有匹配分类", material, calls)
            calls += 1
            middle_options = self.selection.middle_options(large)
            if not middle_options:
                return _failed("category_no_match", "已选大类没有已写明选中条件的中类", material, calls)
            middle, middle_rationale = self._select_level("二级中类", material, middle_options, complete)
            if not middle:
                return _failed("category_no_match", "二级中类没有匹配分类", material, calls)
            calls += 1
            choice, candidates, leaf_status, leaf_rationale = self._select_leaf(
                material, middle_options, middle, middle_rationale, complete
            )
            if choice is None and leaf_status == "parent_conflict":
                rejected = set(middle)
                remaining = [option for option in middle_options if option["code"] not in rejected]
                if not remaining:
                    return _failed("category_no_match", "六位分类确认上级中类对象冲突，且没有剩余中类可重选", material, calls)
                calls += 1
                middle, middle_rationale = self._select_level(
                    "二级中类（对象冲突后重选）", material, remaining, complete,
                    exclusions="已排除中类 %s：%s" % (sorted(rejected), leaf_rationale),
                )
                if not middle:
                    return _failed("category_no_match", "对象冲突后没有匹配的二级中类", material, calls)
                calls += 1
                choice, candidates, leaf_status, leaf_rationale = self._select_leaf(
                    material, remaining, middle, middle_rationale, complete
                )
            if choice is None:
                message = "重选中类后六位分类仍存在上级对象冲突" if leaf_status == "parent_conflict" else "六位所属分类候选全部不匹配"
                return _failed("category_no_match", message, material, calls)
            record = self.catalog.require(choice)
            if not self.templates.contains(record["所属分类编码"]):
                return self._unpublished(record, material, calls)
            template = self.templates.get(record["所属分类编码"])
            calls += 1
            normalized, unsupported = self._normalize(template, query, complete)
            service_candidates = self.services.retrieve(
                template.get("标准部分"), record["所属分类编码"], material, normalized
            )
            if not service_candidates:
                return _result(
                    "master_data_missing", material, record, normalized, "", [],
                    "第%s部分没有可用Q/CNG候选" % template.get("标准部分"), calls, template,
                )
            calls += 1
            service_code = self._select_service(material, record, normalized, service_candidates, complete)
            if not service_code:
                return _result(
                    "service_category_no_match", material, record, normalized, "", [],
                    "Q/CNG候选存在，但全部与物料不匹配。可继续补充属性后重试", calls, template,
                )
            description, missing = _finalize(template, normalized, service_code)
            status = "success" if not missing else "information_missing"
            return _result(status, material, record, normalized, service_code, missing, "", calls, template, description)
        except (ValueError, ObjectSemanticsError) as exc:
            return _failed("model_error", str(exc), None, calls)

    def supplement(self, previous, user_input, complete):
        if previous.get("status") not in {"information_missing", "service_category_no_match"}:
            raise ValueError("只有信息不足或Q/CNG待重试的结果可以接收补充信息")
        state = previous.get("pipeline_state") or {}
        category_code = state.get("category_code") or previous.get("group_category_code")
        if not category_code or not self.templates.contains(category_code):
            raise ValueError("现有结果缺少可补充的六位分类")
        record = self.catalog.require(category_code)
        template = self.templates.get(category_code)
        material = dict(state.get("material") or {})
        query = "%s\n%s" % (material.get("原始输入") or "", user_input)
        calls = int(previous.get("model_call_count") or 0) + 1
        try:
            normalized, _unsupported = self._normalize(template, query, complete, existing=state.get("normalized") or {})
            calls += 1
            service_candidates = self.services.retrieve(template.get("标准部分"), category_code, material, normalized)
            if not service_candidates:
                return _result(
                    "master_data_missing", material, record, normalized, "", [],
                    "第%s部分没有可用Q/CNG候选" % template.get("标准部分"), calls, template,
                )
            service_code = self._select_service(material, record, normalized, service_candidates, complete)
            if not service_code:
                return _result(
                    "service_category_no_match", material, record, normalized, "", [],
                    "Q/CNG候选存在，但全部与物料不匹配。可继续补充属性后重试", calls, template,
                )
            description, missing = _finalize(template, normalized, service_code)
            status = "success" if not missing else "information_missing"
            return _result(status, material, record, normalized, service_code, missing, "", calls, template, description)
        except (ValueError, ObjectSemanticsError) as exc:
            return _failed("model_error", str(exc), material, calls)

    def _extract(self, query, complete):
        payload = complete(MATERIAL_INFORMATION_SYSTEM_PROMPT, "采购物料信息：\n%s" % query)
        material = _material_from_llm(query, payload)
        try:
            validate_extracted_object(material["对象本体"], material["对象形态"])
        except ObjectSemanticsError as exc:
            payload = complete(
                MATERIAL_INFORMATION_SYSTEM_PROMPT,
                "采购物料信息：\n%s\n\n对象语义校验失败，必须改正：%s" % (query, exc),
            )
            material = _material_from_llm(query, payload)
            validate_extracted_object(material["对象本体"], material["对象形态"])
        return material

    def _select_level(self, level, material, options, complete, exclusions="无"):
        payload = complete(
            CATEGORY_LEVEL_RANKING_SYSTEM_PROMPT,
            "当前层级：%s\n\n物料信息：\n%s\n\n当前层排除信息：\n%s\n\n候选：\n%s" % (
                level, _dumps(material), exclusions, _dumps(options),
            ),
        )
        status = payload.get("status")
        rationale = str(payload.get("rationale") or "")
        if status == "no_match":
            if payload.get("ranked_categories"):
                raise ValueError("no_match不得返回分类")
            return [], rationale
        if status != "matched":
            raise ValueError("分类导航状态不合法")
        ranked = payload.get("ranked_categories") or []
        if not ranked:
            raise ValueError("matched必须返回至少一个分类")
        if len(ranked) > 15:
            raise ValueError("分类导航返回超过15个编码")
        codes = []
        allowed = {option["code"] for option in options}
        for item in ranked:
            code = str((item or {}).get("code") or "")
            if code in codes:
                raise ValueError("LLM返回了重复的%s编码" % level)
            codes.append(code)
        unknown = sorted(set(codes) - allowed)
        if unknown:
            raise ValueError("LLM返回了候选范围外的%s编码：%s" % (level, unknown))
        return codes, rationale

    def _select_leaf(self, material, middle_options, middle, rationale, complete):
        candidates = self.selection.annotate_leaves(self.catalog.retrieve_leaf_categories(middle, material))
        if not candidates:
            return None, [], "no_match", ""
        selected = [option for option in middle_options if option["code"] in set(middle)]
        payload = complete(
            CATEGORY_CHOICE_SYSTEM_PROMPT,
            "物料信息：\n%s\n\n已选中类及判定依据：\n%s\n\n候选分类（唯一允许选择的范围）：\n%s" % (
                _dumps(material),
                "已选中类：%s\n中类判定依据：%s" % (_dumps(selected), rationale),
                _dumps([_compact_leaf(item) for item in candidates]),
            ),
        )
        status = payload.get("status")
        leaf_rationale = str(payload.get("rationale") or "")
        if status in {"no_match", "parent_conflict"}:
            if payload.get("selected_category_code"):
                raise ValueError("%s不得返回所属分类编码" % status)
            return None, candidates, status, leaf_rationale
        if status != "matched":
            raise ValueError("小类选择状态不合法")
        code = str(payload.get("selected_category_code") or "")
        allowed = {item["所属分类编码"] for item in candidates}
        if code not in allowed:
            raise ValueError("LLM 返回了候选范围外的编码 %s" % code)
        return code, candidates, status, leaf_rationale

    def _unpublished(self, record, material, calls):
        part1 = record.get("第1部分Q_CNG参考代码") or ""
        if part1:
            message = "当前物料分类“%s”（%s）在第4～41部分标准中未覆盖，转而使用第1部分的Q/CNG 130-2017分类代码" % (
                record.get("分类名称") or "", record.get("所属分类编码") or "",
            )
        else:
            message = "当前物料分类“%s”（%s）在第4～41部分标准中未覆盖，且第1部分没有可用的Q/CNG 130-2017分类代码" % (
                record.get("分类名称") or "", record.get("所属分类编码") or "",
            )
        return _result(
            "master_data_missing", material, record, {}, part1, [], message, calls, None,
        )

    def _normalize(self, template, query, complete, existing=None):
        attributes = template.get("属性") or []
        payload = complete(
            TEMPLATE_NORMALIZATION_SYSTEM_PROMPT,
            "采购信息：\n%s\n\n已有属性：\n%s\n\n模板属性：\n%s" % (
                query,
                _dumps(existing or {}),
                _dumps([
                    {
                        "属性名称": item.get("属性名称"),
                        "是否赋码项": item.get("是否赋码项"),
                        "是否必填": item.get("是否必填"),
                        "填写规则": item.get("填写规则"),
                    }
                    for item in attributes
                ]),
            ),
        )
        allowed = {item.get("属性名称") for item in attributes}
        normalized = dict(existing or {})
        seen = set()
        for item in payload.get("attribute_values") or []:
            name = str(item.get("attribute_name") or "")
            if name not in allowed:
                raise ValueError("模板外字段 %s" % name)
            if name in seen:
                raise ValueError("重复字段 %s" % name)
            seen.add(name)
            evidence = str(item.get("evidence") or "")
            if evidence and evidence not in query:
                continue
            if item.get("evidence_status") == "unsupported":
                continue
            value = str(item.get("normalized_value") or item.get("raw_value") or "").strip()
            if not value or value == "无" and "无" not in query:
                continue
            normalized[name] = value
        return normalized, []

    def _select_service(self, material, record, normalized, candidates, complete):
        payload = complete(
            SERVICE_CATEGORY_CHOICE_SYSTEM_PROMPT,
            "物料信息：\n%s\n\n所属分类：\n%s\n\n规范属性：\n%s\n\n候选：\n%s" % (
                _dumps(material),
                _dumps({
                    "所属分类编码": record.get("所属分类编码"),
                    "分类名称": record.get("分类名称"),
                }),
                _dumps(normalized),
                _dumps([
                    {
                        "物品和服务分类代码": item.get("物品和服务分类代码"),
                        "物品和服务分类名称": item.get("物品和服务分类名称"),
                        "模板位置编码": item.get("模板位置编码"),
                        "模板位置名称": item.get("模板位置名称"),
                    }
                    for item in candidates
                ]),
            ),
        )
        status = payload.get("status")
        if status == "no_match":
            if payload.get("selected_service_category_code"):
                raise ValueError("no_match不得返回物品和服务分类代码")
            return ""
        if status != "matched":
            raise ValueError("Q/CNG选择状态不合法")
        code = str(payload.get("selected_service_category_code") or "")
        allowed = {str(item.get("物品和服务分类代码") or "") for item in candidates}
        if code not in allowed:
            raise ValueError("LLM 返回了候选范围外的物品和服务分类代码 %s" % code)
        return code


def _compact_leaf(item):
    return {
        "所属分类编码": item.get("所属分类编码"),
        "分类名称": item.get("分类名称"),
        "说明": item.get("说明"),
        "选中": item.get("选中"),
        "不选中": item.get("不选中"),
        "计量单位": item.get("计量单位"),
    }


def _finalize(template, normalized, service_code):
    missing = []
    coding = []
    for item in template.get("属性") or []:
        name = item.get("属性名称")
        if item.get("是否必填") and not normalized.get(name):
            missing.append(name)
        if item.get("是否赋码项"):
            coding.append(item)
    coding.sort(key=lambda item: item.get("组合顺序") or 0)
    description = ""
    if not missing:
        description = "丨".join(normalized.get(item.get("属性名称")) or "" for item in coding)
    if service_code and "物品和服务分类代码" not in normalized:
        normalized = dict(normalized)
        normalized["物品和服务分类代码"] = service_code
    return description, missing


def _result(status, material, record, normalized, service_code, missing, message, calls, template, description=""):
    category_code = (record or {}).get("所属分类编码") or ""
    return {
        "status": status,
        "group_category_code": category_code,
        "group_service_code": service_code or "",
        "standard_description": description or "",
        "missing": list(missing or []),
        "message": message or "",
        "model_call_count": calls,
        "pipeline_state": {
            "material": material or {},
            "category_code": category_code,
            "normalized": normalized or {},
            "template_code": (template or {}).get("所属分类编码") or "",
        },
    }


def _failed(status, message, material, calls):
    return _result(status, material, {}, {}, "", [], message, calls, None)
