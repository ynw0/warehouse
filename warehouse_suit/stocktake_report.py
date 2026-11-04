"""月度盘点报表：样式对齐纸质盘点表，跨月时每个月一张表。"""

from __future__ import annotations

import calendar
from datetime import datetime
from io import BytesIO
from xml.sax.saxutils import escape
from zipfile import ZIP_DEFLATED, ZipFile

from warehouse_suit.inventory_constants import STOCK_SOURCE_FORMAL


def _parse_day(value):
    text = str(value or "").strip()[:10]
    if not text:
        raise ValueError("盘点单缺少日期")
    try:
        return datetime.strptime(text, "%Y-%m-%d").date()
    except ValueError as exc:
        raise ValueError("盘点日期不正确") from exc


def _month_span(date_from, date_to):
    start = _parse_day(date_from)
    end = _parse_day(date_to)
    if end < start:
        start, end = end, start
    cursor = start.replace(day=1)
    months = []
    while cursor <= end:
        last_day = calendar.monthrange(cursor.year, cursor.month)[1]
        month_start = cursor
        month_end = cursor.replace(day=last_day)
        clipped_start = max(month_start, start)
        clipped_end = min(month_end, end)
        months.append(
            {
                "year": cursor.year,
                "month": cursor.month,
                "start": clipped_start.isoformat(),
                "end": clipped_end.isoformat(),
                "ytd_end": clipped_end.isoformat(),
                "prior_year_end": f"{cursor.year - 1}-12-31",
                "year_start": f"{cursor.year}-01-01",
            }
        )
        if cursor.month == 12:
            cursor = cursor.replace(year=cursor.year + 1, month=1)
        else:
            cursor = cursor.replace(month=cursor.month + 1)
    return months


def _blank(value):
    number = float(value or 0)
    if abs(number) < 1e-9:
        return None
    return round(number, 4)


def _money(value):
    number = float(value or 0)
    if abs(number) < 1e-9:
        return None
    return round(number, 2)


def _sum_range(rows, operation_type, start, end):
    quantity = 0.0
    amount = 0.0
    for row in rows:
        if row["operation_type"] != operation_type:
            continue
        day = str(row["operation_date"] or "")[:10]
        if start and day < start:
            continue
        if end and day > end:
            continue
        quantity += float(row["quantity"] or 0)
        amount += float(row["amount"] or 0)
    return quantity, amount


def _net_until(rows, end):
    inbound_qty, inbound_amount = _sum_range(rows, "in", "", end)
    outbound_qty, outbound_amount = _sum_range(rows, "out", "", end)
    return inbound_qty - outbound_qty, inbound_amount - outbound_amount


def build_stocktake_sheets(cursor, stocktake_id):
    cursor.execute("SELECT * FROM stocktake_forms WHERE id = ?", (int(stocktake_id),))
    form = cursor.fetchone()
    if not form:
        return None
    form = dict(form)
    cursor.execute(
        """
        SELECT si.material_id, si.location_text, m.name AS material_name, m.brand_model, m.spec, m.unit
        FROM stocktake_items si
        JOIN materials m ON m.id = si.material_id
        WHERE si.stocktake_id = ?
        ORDER BY m.material_code, si.id
        """,
        (int(stocktake_id),),
    )
    materials = []
    seen = set()
    for row in cursor.fetchall():
        material_id = int(row["material_id"])
        if material_id in seen:
            continue
        seen.add(material_id)
        materials.append(dict(row))
    material_ids = [item["material_id"] for item in materials]
    records = {material_id: [] for material_id in material_ids}
    projects = {material_id: [] for material_id in material_ids}
    first_in = {}
    if material_ids:
        placeholders = ",".join("?" for _ in material_ids)
        cursor.execute(
            f"""
            SELECT material_id, operation_type, operation_date, quantity, amount
            FROM stock_records
            WHERE stock_source = ? AND material_id IN ({placeholders})
            ORDER BY operation_date, id
            """,
            [STOCK_SOURCE_FORMAL, *material_ids],
        )
        for row in cursor.fetchall():
            item = dict(row)
            material_id = int(item["material_id"])
            records.setdefault(material_id, []).append(item)
            if item["operation_type"] == "in" and material_id not in first_in:
                first_in[material_id] = str(item["operation_date"] or "")[:10]
        cursor.execute(
            f"""
            SELECT material_id, purchase_project
            FROM material_batches
            WHERE material_id IN ({placeholders})
            """,
            material_ids,
        )
        for row in cursor.fetchall():
            project = str(row["purchase_project"] or "").strip()
            bucket = projects.setdefault(int(row["material_id"]), [])
            if project and project not in bucket:
                bucket.append(project)
    months = _month_span(form.get("date_from") or form.get("date_to"), form.get("date_to") or form.get("date_from"))
    year_count = len({item["year"] for item in months})
    sheets = []
    for month in months:
        sheet_name = f"{month['month']}月" if year_count == 1 else f"{month['year']}年{month['month']}月"
        rows = []
        for index, material in enumerate(materials, start=1):
            material_id = int(material["material_id"])
            ledger = records.get(material_id, [])
            opening_qty, opening_amount = _net_until(ledger, month["prior_year_end"])
            month_in_qty, month_in_amount = _sum_range(ledger, "in", month["start"], month["end"])
            month_out_qty, month_out_amount = _sum_range(ledger, "out", month["start"], month["end"])
            ytd_in_qty, ytd_in_amount = _sum_range(ledger, "in", month["year_start"], month["ytd_end"])
            ytd_out_qty, ytd_out_amount = _sum_range(ledger, "out", month["year_start"], month["ytd_end"])
            close_qty = opening_qty + ytd_in_qty - ytd_out_qty
            close_amount = opening_amount + ytd_in_amount - ytd_out_amount
            age = None
            first_day = first_in.get(material_id) or ""
            if first_day:
                started = _parse_day(first_day)
                ended = _parse_day(month["ytd_end"])
                age = max(0, (ended.year - started.year) * 12 + (ended.month - started.month))
            avg_price = close_amount / close_qty if abs(close_qty) > 1e-9 else None
            in_price = month_in_amount / month_in_qty if abs(month_in_qty) > 1e-9 else None
            rows.append(
                {
                    "seq": index,
                    "name": material.get("material_name") or "",
                    "brand_model": material.get("brand_model") or "",
                    "spec": material.get("spec") or "",
                    "unit": material.get("unit") or "",
                    "avg_price": _money(avg_price),
                    "opening_qty": _blank(opening_qty),
                    "opening_amount": _money(opening_amount),
                    "month_in_qty": _blank(month_in_qty),
                    "month_in_price": _money(in_price),
                    "month_in_amount": _money(month_in_amount),
                    "ytd_in_qty": _blank(ytd_in_qty),
                    "ytd_in_amount": _money(ytd_in_amount),
                    "month_out_qty": _blank(month_out_qty),
                    "month_out_amount": _money(month_out_amount),
                    "ytd_out_qty": _blank(ytd_out_qty),
                    "ytd_out_amount": _money(ytd_out_amount),
                    "close_qty": _blank(close_qty),
                    "close_amount": _money(close_amount),
                    "age_months": age,
                    "project": "、".join(projects.get(material_id) or []),
                }
            )
        sheets.append(
            {
                "name": sheet_name,
                "title": f"中光学杭州库房{month['year']}年{month['month']}月盘点报表",
                "org": "填报单位：中光学杭州",
                "rows": rows,
            }
        )
    return {
        "id": int(form["id"]),
        "form_no": form.get("form_no") or "",
        "date_from": form.get("date_from") or "",
        "date_to": form.get("date_to") or "",
        "checker_signature": form.get("checker_signature") or "",
        "checker_date": form.get("checker_date") or "",
        "supervisor_signature": form.get("supervisor_signature") or "",
        "supervisor_date": form.get("supervisor_date") or "",
        "sheets": sheets,
    }


def _xml(text):
    return escape(str(text or ""), {'"': "&quot;"})


def _cell(ref, value, style, kind="text"):
    if kind == "number":
        return f'<c r="{ref}" s="{style}"><v>{value}</v></c>'
    if kind == "formula":
        return f'<c r="{ref}" s="{style}"><f>{value}</f></c>'
    return f'<c r="{ref}" s="{style}" t="inlineStr"><is><t>{_xml(value)}</t></is></c>'


def _column_name(index):
    name = ""
    while index:
        index, remainder = divmod(index - 1, 26)
        name = chr(65 + remainder) + name
    return name


def _sheet_xml(sheet):
    rows = sheet["rows"]
    last = 6 + len(rows)
    total_row = last + 1
    lines = [
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>',
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">',
        "<sheetViews><sheetView workbookViewId=\"0\"><pane ySplit=\"6\" topLeftCell=\"A7\" activePane=\"bottomLeft\" state=\"frozen\"/></sheetView></sheetViews>",
        "<cols>"
        '<col min="1" max="1" width="8" customWidth="1"/>'
        '<col min="2" max="2" width="24" customWidth="1"/>'
        '<col min="3" max="3" width="18" customWidth="1"/>'
        '<col min="4" max="4" width="32" customWidth="1"/>'
        '<col min="5" max="5" width="8" customWidth="1"/>'
        '<col min="6" max="6" width="14" customWidth="1"/>'
        '<col min="7" max="21" width="12" customWidth="1"/>'
        "</cols>",
        "<sheetData>",
    ]
    lines.append(f'<row r="1" ht="30" customHeight="1">{_cell("A1", sheet["title"], 1)}</row>')
    lines.append(f'<row r="2" ht="22" customHeight="1">{_cell("A2", sheet["org"], 2)}</row>')
    lines.append('<row r="3" ht="18" customHeight="1"></row>')
    header_4 = [
        ("A4", "序号"), ("B4", "产品名称"), ("C4", "品牌型号"), ("D4", "技术规格"),
        ("E4", "单位"), ("F4", "库存平均单价"), ("G4", "上年结存"), ("I4", "收入"),
        ("N4", "发出"), ("R4", "月末库存"), ("T4", "库龄（月）"), ("U4", "入账项目"),
    ]
    header_5 = [("I5", "本月"), ("L5", "当年累计"), ("N5", "本月"), ("P5", "当年累计")]
    header_6 = [
        ("G6", "数量"), ("H6", "金额"), ("I6", "数量"), ("J6", "单价"), ("K6", "金额"),
        ("L6", "数量"), ("M6", "金额"), ("N6", "数量"), ("O6", "金额"), ("P6", "数量"),
        ("Q6", "金额"), ("R6", "数量"), ("S6", "金额"),
    ]
    lines.append('<row r="4" ht="22" customHeight="1">' + "".join(_cell(ref, text, 2) for ref, text in header_4) + "</row>")
    lines.append('<row r="5" ht="22" customHeight="1">' + "".join(_cell(ref, text, 2) for ref, text in header_5) + "</row>")
    lines.append('<row r="6" ht="22" customHeight="1">' + "".join(_cell(ref, text, 2) for ref, text in header_6) + "</row>")
    numeric_keys = [
        "avg_price", "opening_qty", "opening_amount", "month_in_qty", "month_in_price", "month_in_amount",
        "ytd_in_qty", "ytd_in_amount", "month_out_qty", "month_out_amount", "ytd_out_qty", "ytd_out_amount",
        "close_qty", "close_amount", "age_months",
    ]
    for offset, item in enumerate(rows):
        excel_row = 7 + offset
        values = [
            ("text", item["seq"]),
            ("text", item["name"]),
            ("text", item["brand_model"]),
            ("text", item["spec"]),
            ("text", item["unit"]),
        ]
        for key in numeric_keys:
            values.append(("number", item.get(key)))
        values.append(("text", item["project"]))
        cells = []
        for index, (kind, value) in enumerate(values, start=1):
            ref = f"{_column_name(index)}{excel_row}"
            if kind == "number" and value is None:
                cells.append(f'<c r="{ref}" s="3"/>')
            elif kind == "number":
                cells.append(_cell(ref, value, 3, "number"))
            else:
                cells.append(_cell(ref, value, 3))
        lines.append(f'<row r="{excel_row}">' + "".join(cells) + "</row>")
    total_cells = [_cell(f"A{total_row}", "合计", 2)]
    for column in ("K", "M", "O", "Q", "R", "S"):
        formula = f"SUM({column}7:{column}{last})" if rows else "0"
        total_cells.append(_cell(f"{column}{total_row}", formula, 3, "formula"))
    lines.append(f'<row r="{total_row}" ht="22" customHeight="1">' + "".join(total_cells) + "</row>")
    lines.append("</sheetData>")
    lines.append(
        "<mergeCells count=\"19\">"
        '<mergeCell ref="A1:U1"/>'
        '<mergeCell ref="A2:D2"/>'
        '<mergeCell ref="A4:A6"/><mergeCell ref="B4:B6"/><mergeCell ref="C4:C6"/><mergeCell ref="D4:D6"/>'
        '<mergeCell ref="E4:E6"/><mergeCell ref="F4:F6"/><mergeCell ref="G4:H5"/>'
        '<mergeCell ref="I4:M4"/><mergeCell ref="I5:K5"/><mergeCell ref="L5:M5"/>'
        '<mergeCell ref="N4:Q4"/><mergeCell ref="N5:O5"/><mergeCell ref="P5:Q5"/>'
        '<mergeCell ref="R4:S5"/><mergeCell ref="T4:T6"/><mergeCell ref="U4:U6"/>'
        f'<mergeCell ref="A{total_row}:D{total_row}"/>'
        "</mergeCells>"
    )
    lines.append("</worksheet>")
    return "".join(lines)


def stocktake_workbook(report):
    sheets = report.get("sheets") or []
    if not sheets:
        sheets = [{"name": "盘点", "title": "中光学杭州库房盘点报表", "org": "填报单位：中光学杭州", "rows": []}]
    buffer = BytesIO()
    with ZipFile(buffer, "w", ZIP_DEFLATED) as archive:
        archive.writestr(
            "[Content_Types].xml",
            """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
<Default Extension="xml" ContentType="application/xml"/>
<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>
<Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>
"""
            + "".join(
                f'<Override PartName="/xl/worksheets/sheet{index}.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
                for index in range(1, len(sheets) + 1)
            )
            + "</Types>",
        )
        archive.writestr(
            "_rels/.rels",
            """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>
</Relationships>""",
        )
        archive.writestr(
            "xl/workbook.xml",
            """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">
<sheets>"""
            + "".join(
                f'<sheet name="{_xml(sheet["name"])}" sheetId="{index}" r:id="rId{index}"/>'
                for index, sheet in enumerate(sheets, start=1)
            )
            + "</sheets></workbook>",
        )
        archive.writestr(
            "xl/_rels/workbook.xml.rels",
            """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">"""
            + "".join(
                f'<Relationship Id="rId{index}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet{index}.xml"/>'
                for index in range(1, len(sheets) + 1)
            )
            + f'<Relationship Id="rId{len(sheets) + 1}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>'
            + "</Relationships>",
        )
        archive.writestr(
            "xl/styles.xml",
            """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">
<fonts count="3">
<font><sz val="11"/><name val="宋体"/></font>
<font><b/><sz val="20"/><name val="宋体"/></font>
<font><sz val="14"/><name val="宋体"/></font>
</fonts>
<fills count="2"><fill><patternFill patternType="none"/></fill><fill><patternFill patternType="gray125"/></fill></fills>
<borders count="2">
<border><left/><right/><top/><bottom/><diagonal/></border>
<border><left style="thin"/><right style="thin"/><top style="thin"/><bottom style="thin"/><diagonal/></border>
</borders>
<cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>
<cellXfs count="4">
<xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/>
<xf numFmtId="0" fontId="1" fillId="0" borderId="0" xfId="0" applyFont="1" applyAlignment="1"><alignment horizontal="center" vertical="center"/></xf>
<xf numFmtId="0" fontId="2" fillId="0" borderId="1" xfId="0" applyFont="1" applyBorder="1" applyAlignment="1"><alignment horizontal="center" vertical="center" wrapText="1"/></xf>
<xf numFmtId="0" fontId="2" fillId="0" borderId="1" xfId="0" applyFont="1" applyBorder="1" applyAlignment="1"><alignment horizontal="center" vertical="center"/></xf>
</cellXfs>
</styleSheet>""",
        )
        for index, sheet in enumerate(sheets, start=1):
            archive.writestr(f"xl/worksheets/sheet{index}.xml", _sheet_xml(sheet))
    return buffer.getvalue()
