"""HTML export helpers."""

from __future__ import annotations

from html import escape


def material_cards_html(materials, material_code=""):
    title = f"物料料卡导出 - {escape(material_code)}" if material_code else "物料料卡导出 - 全部"
    cards = []
    for material in materials:
        rows = []
        if material["records"]:
            for record in material["records"]:
                rows.append(
                    "<tr>"
                    f"<td>{escape(record['operation_date'])}</td>"
                    f"<td>{'入库' if record['operation_type'] == 'in' else '领用'}</td>"
                    f"<td>{record['quantity']:g}</td>"
                    f"<td>{record['balance_after']:g}</td>"
                    f"<td>{escape(record.get('remark') or '')}</td>"
                    "</tr>"
                )
        else:
            rows.append("<tr><td colspan='5'>暂无出入库记录</td></tr>")
        cards.append(
            f"""
            <section class="card">
              <div class="meta">
                <h2>{escape(material['name'])}</h2>
                <p><b>物料编号</b>{escape(material['material_code'])}</p>
                <p><b>品牌型号</b>{escape(material.get('brand_model') or '')}</p>
                <p><b>技术规格</b>{escape(material.get('spec') or '')}</p>
                <p><b>存放位置</b>{escape(material.get('shelf_name') or '')} / {material.get('layer_number') or '-'} 层 / {escape(material.get('zone_name') or '')} 区</p>
                <p><b>当前余量</b>{material.get('quantity') or 0:g} {escape(material.get('unit') or '')}</p>
              </div>
              <table>
                <thead><tr><th>日期</th><th>类型</th><th>数量</th><th>余量</th><th>备注</th></tr></thead>
                <tbody>{''.join(rows)}</tbody>
              </table>
            </section>
            """
        )
    if not cards:
        cards.append("<section class='card'><h2>未找到物料</h2></section>")
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <title>{title}</title>
  <style>
    body {{ margin: 0; padding: 28px; font-family: Arial, 'Microsoft YaHei', sans-serif; color: #172033; background: #f5f7fb; }}
    h1 {{ margin: 0 0 20px; font-size: 26px; }}
    .card {{ display: grid; grid-template-columns: 280px 1fr; gap: 22px; margin: 0 0 18px; padding: 18px; background: white; border: 1px solid #dfe5ef; border-radius: 8px; page-break-inside: avoid; }}
    h2 {{ margin: 0 0 12px; font-size: 20px; }}
    p {{ margin: 8px 0; }}
    b {{ display: inline-block; width: 76px; color: #5c667a; }}
    table {{ width: 100%; border-collapse: collapse; font-size: 14px; }}
    th, td {{ border: 1px solid #dfe5ef; padding: 9px 10px; text-align: left; }}
    th {{ background: #eef3fa; }}
    @media print {{ body {{ background: white; }} .card {{ border-color: #999; }} }}
  </style>
</head>
<body>
  <h1>{title}</h1>
  {''.join(cards)}
</body>
</html>"""
