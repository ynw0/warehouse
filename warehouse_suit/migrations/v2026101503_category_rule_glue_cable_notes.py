"""去掉 02/04/02 膏体类，研发用胶归 24/02/03；收口 39/04 线类边界 note。不改物料主数据。"""


def upgrade(conn):
    # 删除已从种子去掉的小类；sync_seed_rules 不会删行
    conn.execute(
        """
        DELETE FROM category_rules
        WHERE major_code = '02' AND middle_code = '04' AND small_code = '02'
        """
    )
    updates = [
        ("04", "02", "", "排线、电源线、信号线、触发线、电缆、转接线等研发用线"),
        ("04", "02", "01", "排线、电源线、信号线、触发线、电缆、转接线等研发用线"),
        ("24", "02", "", "胶带、胶水、研发用胶"),
        ("24", "02", "03", "研发用胶、膏体、UV胶、免钉胶、修复膏等"),
        ("39", "02", "", "成品数据线、USB线、视频线、网线、水晶头、PCIE延长线等信息化成品线"),
        ("39", "02", "01", "USB线、各类视频线等类似成品数据线"),
    ]
    for major, middle, small, note in updates:
        conn.execute(
            """
            UPDATE category_rules
            SET note = ?
            WHERE warehouse_code = '' AND major_code = ? AND middle_code = ?
              AND small_code = ? AND origin = 'seed'
            """,
            (note, major, middle, small),
        )


MIGRATION = {
    "version": "2026101503",
    "name": "category rule glue cable notes",
    "upgrade": upgrade,
}
