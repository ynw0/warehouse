"""把本库大中小类规则放进数据库，原分类和新分类同一张表。"""

from warehouse_suit.category_rules import seed_rules


def upgrade(conn):
    seed_rules(conn)


MIGRATION = {
    "version": "2026100902",
    "name": "category rules",
    "upgrade": upgrade,
}
