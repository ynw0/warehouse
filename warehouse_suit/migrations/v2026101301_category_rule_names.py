"""按现行大中小类校正原分类。仓库码不参与分类，已确认的规则不改。"""

from warehouse_suit.category_rules import sync_seed_rules


def upgrade(conn):
    sync_seed_rules(conn)


MIGRATION = {
    "version": "2026101301",
    "name": "category rule names",
    "upgrade": upgrade,
}
