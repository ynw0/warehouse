"""Shared inventory source and status values."""

STOCK_SOURCE_FORMAL = "formal"
STOCK_SOURCE_TEMPORARY = "temporary"
STOCK_SOURCES = frozenset({STOCK_SOURCE_FORMAL, STOCK_SOURCE_TEMPORARY})

INVENTORY_STATUS_AVAILABLE = "available"
INVENTORY_STATUS_TRANSFER_LOCKED = "transfer_locked"
INVENTORY_STATUS_TRANSFERRED = "transferred"
INVENTORY_STATUSES = frozenset(
    {
        INVENTORY_STATUS_AVAILABLE,
        INVENTORY_STATUS_TRANSFER_LOCKED,
        INVENTORY_STATUS_TRANSFERRED,
    }
)

BUSINESS_TYPE_MANUAL = "manual"
BUSINESS_TYPE_TEMPORARY_MANUAL_INBOUND = "temporary_manual_inbound"
BUSINESS_TYPE_TEMPORARY_MANUAL_ADJUST_IN = "temporary_manual_adjust_in"
BUSINESS_TYPE_TEMPORARY_MANUAL_ADJUST_OUT = "temporary_manual_adjust_out"
BUSINESS_TYPE_CLAIM_OUTBOUND = "claim_outbound"
BUSINESS_TYPE_BORROW_OUTBOUND = "borrow_outbound"
BUSINESS_TYPE_BORROW_RETURN_INBOUND = "borrow_return_inbound"
BUSINESS_TYPE_TEMPORARY_TRANSFER_CLOSE = "temporary_transfer_close"

AUTO_CLAIM_ORIGIN_TYPE = "temporary_transfer_auto_claim"

TEMPORARY_WORKFLOW_ORIGIN_TYPES = frozenset(
    {
        "temporary",
        "temporary_inventory",
        "temporary_transfer",
        AUTO_CLAIM_ORIGIN_TYPE,
    }
)


def validate_stock_source(value):
    stock_source = str(value or STOCK_SOURCE_FORMAL).strip()
    if stock_source not in STOCK_SOURCES:
        raise ValueError("库存来源必须为 formal 或 temporary")
    return stock_source


def validate_inventory_status(value):
    inventory_status = str(value or INVENTORY_STATUS_AVAILABLE).strip()
    if inventory_status not in INVENTORY_STATUSES:
        raise ValueError("库存状态不受支持")
    return inventory_status
