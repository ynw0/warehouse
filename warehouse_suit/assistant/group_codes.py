"""助手查询集团分类，复用集团码表管线且不写入物料。"""

import json
import time

from warehouse_suit.coding_agent import parse_model_json
from warehouse_suit.group_coding import classify_group_material
from warehouse_suit.validation import validate_material_code_value


def query_group_codes(ctx, payload):
    cursor = ctx["conn"].cursor()
    code = str(payload.get("material_code") or "").strip()
    query = str(payload.get("q") or "").strip()
    material = None
    if code:
        code = validate_material_code_value(code)
        cursor.execute(
            """SELECT material_code, name, brand_model, spec, unit,
                      group_category_code, group_service_code, group_standard_description
               FROM materials WHERE material_code=?""", (code,),
        )
        row = cursor.fetchone()
        if not row:
            raise ValueError("物料不存在")
        material = dict(row)
        # 已保存的两个集团码直接返回，不重新赋码或调用模型。
        if material["group_category_code"] and material["group_service_code"]:
            return {
                "source": "material", "status": "success", "material": material,
                "group_category_code": material["group_category_code"],
                "group_service_code": material["group_service_code"],
                "standard_description": material["group_standard_description"],
                "missing": [], "message": "", "model_call_count": 0,
            }
        query = json.dumps({
            "名称": material["name"], "品牌型号": material["brand_model"],
            "技术规格": material["spec"], "单位": material["unit"], "补充信息": query,
        }, ensure_ascii=False)
    if not query:
        raise ValueError("请提供仓库物料编码，或物料名称、品牌型号、规格和单位")

    def complete(system, user):
        # 复用当前会话的模型连接，并遵守本轮超时边界。
        if time.monotonic() >= ctx["deadline"]:
            raise TimeoutError("本轮对话已超过 120 秒")
        response = ctx["complete_quiet"]({
            "model": ctx["model"],
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
        })
        return parse_model_json(response.get("content") or "")

    result = classify_group_material(query, complete)
    return {
        **result, "source": "group_catalog", "material": material,
        "message": result.get("message") or "集团分类查询结果，尚未写入物料主数据。",
    }
