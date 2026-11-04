"""集团分类码表、选中条件和 Q/CNG 候选。只读，不回写。"""

from __future__ import annotations

import json
import math
from collections import Counter
from pathlib import Path

from warehouse_suit.group_coding.search_terms import terms


def _load(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _zip_equal(left, right):
    if len(left) != len(right):
        raise ValueError("分类检索数据长度不一致")
    return zip(left, right)


class CategoryCatalog:
    """第 1 部分六位小类。"""

    def __init__(self, records):
        if not records:
            raise ValueError("分类表不能为空")
        self.records = records
        self.by_code = {}
        for record in records:
            code = record["所属分类编码"]
            if code in self.by_code:
                raise ValueError("分类表中存在重复的所属分类编码")
            self.by_code[code] = record
        self._terms = [
            terms(" ".join((
                record.get("分类名称") or "",
                record.get("大类名称") or "",
                record.get("中类名称") or "",
                record.get("说明") or "",
                record.get("第1部分Q_CNG参考名称") or "",
            )))
            for record in records
        ]
        counts = Counter(term for record_terms in self._terms for term in record_terms)
        total = len(records)
        self._idf = {
            term: math.log((total + 1) / (count + 1)) + 1
            for term, count in counts.items()
        }

    @classmethod
    def from_json(cls, path):
        records = []
        for item in _load(path):
            records.append({
                "所属分类编码": str(item.get("所属分类编码") or ""),
                "分类名称": str(item.get("分类名称") or ""),
                "大类编码": str(item.get("大类编码") or ""),
                "大类名称": str(item.get("大类名称") or ""),
                "中类编码": str(item.get("中类编码") or ""),
                "中类名称": str(item.get("中类名称") or ""),
                "计量单位": str(item.get("计量单位") or ""),
                "说明": str(item.get("说明") or ""),
                "第1部分Q_CNG参考代码": str(item.get("物品和服务分类代码") or item.get("第1部分Q_CNG参考代码") or ""),
                "第1部分Q_CNG参考名称": str(item.get("物品和服务分类名称") or item.get("第1部分Q_CNG参考名称") or ""),
            })
        return cls(records)

    def large_categories(self):
        values = sorted({(record["大类编码"], record["大类名称"]) for record in self.records})
        return [{"code": code, "name": name} for code, name in values]

    def middle_categories(self, large_codes):
        allowed = set(large_codes)
        values = sorted({
            (record["中类编码"], record["中类名称"])
            for record in self.records
            if record["大类编码"] in allowed
        })
        return [{"code": code, "name": name} for code, name in values]

    def leaf_categories(self, middle_codes):
        allowed = set(middle_codes)
        return [_candidate(record, 0) for record in self.records if record["中类编码"] in allowed]

    def retrieve_leaf_categories(self, middle_codes, material):
        """已选中类的全部小类，按相关度排序，不截断。"""
        allowed = set(middle_codes)
        if not allowed:
            raise ValueError("没有可供检索的中类")
        query = " ".join(filter(None, [
            material.get("通用名称"),
            material.get("品牌"),
            material.get("型号"),
            material.get("材质"),
            material.get("用途"),
            *(material.get("规格") or []),
            *(material.get("其他可核实属性") or {}).values(),
        ]))
        query_terms = terms(query)
        scored = []
        for record, record_terms in _zip_equal(self.records, self._terms):
            if record["中类编码"] not in allowed:
                continue
            score = sum(self._idf.get(term, 1) for term in query_terms & record_terms)
            if record.get("分类名称") and record["分类名称"] in query:
                score += 20
            scored.append((score, record))
        if not scored:
            raise ValueError("中类没有小类：%s" % sorted(allowed))
        scored.sort(key=lambda item: (-item[0], item[1]["所属分类编码"]))
        return [_candidate(record, score) for score, record in scored]

    def require(self, code):
        try:
            return self.by_code[code]
        except KeyError as exc:
            raise ValueError("分类主数据中不存在所属分类编码 %s" % code) from exc


def _candidate(record, score):
    return {
        "所属分类编码": record["所属分类编码"],
        "分类名称": record["分类名称"],
        "大类名称": record["大类名称"],
        "中类名称": record["中类名称"],
        "计量单位": record.get("计量单位") or "",
        "说明": record.get("说明") or "",
        "选中": "",
        "不选中": "",
        "第1部分Q_CNG参考代码": record.get("第1部分Q_CNG参考代码") or "",
        "第1部分Q_CNG参考名称": record.get("第1部分Q_CNG参考名称") or "",
        "检索分数": round(float(score), 6),
    }


class SelectionCatalog:
    """未写入选中条件的分类不进入候选。"""

    def __init__(self, payload, records):
        self.payload = payload
        self._records = records

    @classmethod
    def from_json(cls, path, catalog):
        payload = _load(path)
        errors = validate_category_selection(payload, catalog.records)
        if errors:
            raise ValueError("分类选中条件校验失败：\n" + "\n".join(errors[:20]))
        return cls(payload, catalog.records)

    def large_options(self):
        options = []
        values = sorted({(record["大类编码"], record["大类名称"]) for record in self._records})
        for code, _name in values:
            entry = (self.payload.get("large") or {}).get(code)
            if not entry:
                continue
            options.append(_level_option(code, entry))
        return options

    def middle_options(self, large_codes):
        allowed = set(large_codes)
        options = []
        values = sorted({
            (record["中类编码"], record["中类名称"])
            for record in self._records
            if record["大类编码"] in allowed
        })
        for code, _name in values:
            entry = (self.payload.get("middle") or {}).get(code)
            if not entry:
                continue
            options.append(_level_option(code, entry))
        return options

    def annotate_leaves(self, candidates):
        annotated = []
        leaf = self.payload.get("leaf") or {}
        for candidate in candidates:
            entry = leaf.get(candidate["所属分类编码"])
            if entry is None:
                continue
            copied = dict(candidate)
            copied["分类名称"] = entry.get("名称") or copied["分类名称"]
            copied["说明"] = entry.get("说明") or ""
            copied["选中"] = entry.get("选中") or ""
            copied["不选中"] = entry.get("不选中") or ""
            annotated.append(copied)
        return annotated


def _level_option(code, entry):
    return {
        "code": code,
        "name": entry.get("名称") or "",
        "description": entry.get("说明") or "",
        "选中": entry.get("选中") or "",
        "不选中": entry.get("不选中") or "",
    }


def validate_category_selection(payload, records):
    errors = []
    if payload.get("review_status") != "approved":
        errors.append("选中条件未审核，不能用于编码管线")
    large = payload.get("large") or {}
    middle = payload.get("middle") or {}
    leaf = payload.get("leaf") or {}
    if not large:
        errors.append("至少需要一个已写明条件的大类")
    by_large = {}
    by_middle = {}
    large_names = {}
    middle_names = {}
    leaf_names = {}
    for record in records:
        by_large.setdefault(record["大类编码"], []).append(record)
        by_middle.setdefault(record["中类编码"], []).append(record)
        large_names[record["大类编码"]] = record["大类名称"]
        middle_names[record["中类编码"]] = record["中类名称"]
        leaf_names[record["所属分类编码"]] = record["分类名称"]
    for code, entry in large.items():
        if code not in large_names:
            errors.append("大类 %s 不在码表中" % code)
            continue
        if entry.get("名称") != large_names[code]:
            errors.append("大类 %s 名称与码表不一致" % code)
        expected_middles = {record["中类编码"] for record in by_large[code]}
        missing = sorted(expected_middles - set(middle))
        if missing:
            errors.append("大类 %s 缺少中类条件" % code)
        expected_leaves = {record["所属分类编码"] for record in by_large[code]}
        if expected_leaves - set(leaf):
            errors.append("大类 %s 缺少小类条件" % code)
    for code, entry in middle.items():
        if code not in middle_names:
            errors.append("中类 %s 不在码表中" % code)
            continue
        if entry.get("名称") != middle_names[code]:
            errors.append("中类 %s 名称与码表不一致" % code)
    for code, entry in leaf.items():
        if code not in leaf_names:
            errors.append("小类 %s 不在码表中" % code)
            continue
        if entry.get("名称") != leaf_names[code]:
            errors.append("小类 %s 名称与码表不一致" % code)
        for field in ("说明", "选中", "不选中"):
            if not str(entry.get(field) or "").strip():
                errors.append("小类 %s %s为空" % (code, field))
    return errors


class TemplateRepository:
    def __init__(self, templates):
        self._by_code = {}
        for template in templates:
            code = template.get("所属分类编码")
            if code in self._by_code:
                raise ValueError("描述模板库存在重复的所属分类编码")
            self._by_code[code] = template

    @classmethod
    def from_json(cls, path):
        return cls(_load(path))

    def get(self, category_code):
        try:
            return self._by_code[category_code]
        except KeyError as exc:
            raise ValueError("所属分类编码 %s 没有对应的描述模板" % category_code) from exc

    def contains(self, category_code):
        return category_code in self._by_code


class ServiceCatalog:
    """领域分册 Q/CNG 候选。"""

    def __init__(self, records):
        if not records:
            raise ValueError("Q/CNG精细分类表不能为空")
        self.records = records
        self._terms = [
            terms(" ".join((
                record.get("物品和服务分类名称") or "",
                record.get("描述对象") or "",
                record.get("模板位置名称") or "",
            )))
            for record in records
        ]
        counts = Counter(term for record_terms in self._terms for term in record_terms)
        total = len(records)
        self._idf = {term: math.log((total + 1) / (count + 1)) + 1 for term, count in counts.items()}

    @classmethod
    def from_json(cls, path):
        return cls(_load(path))

    def retrieve(self, part, category_code, material, normalized):
        query = " ".join(filter(None, [
            material.get("通用名称"),
            material.get("品牌"),
            material.get("型号"),
            material.get("材质"),
            material.get("用途"),
            *(material.get("规格") or []),
            *(material.get("其他可核实属性") or {}).values(),
            *list((normalized or {}).values()),
        ]))
        query_terms = terms(query)
        applicable = _applicable_records(self.records, part, category_code)
        if not applicable:
            return []
        applicable_ids = {id(record) for record in applicable}
        scored = []
        for record, record_terms in _zip_equal(self.records, self._terms):
            if id(record) not in applicable_ids:
                continue
            score = sum(self._idf.get(term, 1) for term in query_terms & record_terms)
            name = record.get("物品和服务分类名称") or ""
            if name and name in query:
                score += 20
            scored.append((score, -len(str(record.get("物品和服务分类代码") or "")), record))
        scored.sort(key=lambda item: (-item[0], item[1], item[2].get("物品和服务分类代码") or ""))
        results = []
        for score, _length, record in scored:
            copied = dict(record)
            copied["检索分数"] = round(score, 6)
            results.append(copied)
        return results


def _applicable_records(records, part, category_code):
    scoped = [record for record in records if int(record.get("标准部分") or 0) == int(part)]
    matched = [record for record in scoped if str(category_code).startswith(str(record.get("模板位置编码") or ""))]
    matched = [record for record in matched if record.get("模板位置编码")]
    if matched:
        longest = max(len(str(record.get("模板位置编码") or "")) for record in matched)
        return [record for record in matched if len(str(record.get("模板位置编码") or "")) == longest]
    other = "%s9999" % str(category_code)[:2]
    return [record for record in scoped if str(record.get("模板位置编码") or "") == other]
