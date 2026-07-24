"""HTTP API route registration modules for warehouse_suit."""

from warehouse_suit.api.acceptance import register_acceptance_routes
from warehouse_suit.api.ai import register_ai_routes
from warehouse_suit.api.auth import register_auth_routes
from warehouse_suit.api.attachments import register_attachment_routes
from warehouse_suit.api.backup import register_backup_routes
from warehouse_suit.api.borrow import register_borrow_routes
from warehouse_suit.api.claim import register_claim_routes
from warehouse_suit.api.history import register_history_routes
from warehouse_suit.api.materials import register_material_routes
from warehouse_suit.api.notifications import register_notification_routes
from warehouse_suit.api.production import register_production_routes
from warehouse_suit.api.recycle import register_recycle_routes
from warehouse_suit.api.shelves import register_shelf_routes
from warehouse_suit.api.stock import register_stock_routes
from warehouse_suit.api.system_bootstrap import register_system_bootstrap_routes
from warehouse_suit.api.system_log import register_system_log_routes
from warehouse_suit.api.system_settings import register_system_settings_routes
from warehouse_suit.api.system_users import register_system_user_routes
from warehouse_suit.api.temporary_inventory import register_temporary_inventory_routes
from warehouse_suit.api.extended import register_extended_routes
from warehouse_suit.api.temporary_transfers import register_temporary_transfer_routes
from warehouse_suit.api.workflow import register_workflow_routes

__all__ = [
    "register_acceptance_routes",
    "register_ai_routes",
    "register_auth_routes",
    "register_attachment_routes",
    "register_backup_routes",
    "register_borrow_routes",
    "register_claim_routes",
    "register_history_routes",
    "register_material_routes",
    "register_notification_routes",
    "register_production_routes",
    "register_recycle_routes",
    "register_shelf_routes",
    "register_stock_routes",
    "register_system_bootstrap_routes",
    "register_system_log_routes",
    "register_system_settings_routes",
    "register_system_user_routes",
    "register_temporary_inventory_routes",
    "register_extended_routes",
    "register_temporary_transfer_routes",
    "register_workflow_routes",
]
