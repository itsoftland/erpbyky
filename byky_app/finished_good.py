import frappe
from frappe.utils import flt

# Purchase Order: True = finished good row is a label with no value
#                 False = row keeps its own rate (default)
ZERO_FG_ROW_ON_PO = False

# Purchase Receipt / Invoice: True = finished good row is zeroed, boxes carry the value
#                             False = row keeps its own rate
ZERO_FG_ROW_ON_RECEIPT_INVOICE = False


def _pick(row, names):
    """Return the first non-empty value found in the given fieldnames."""
    for n in names:
        val = row.get(n)
        if val:
            return val
    return None


def _needs_boxes(row):
    """Finished good, not a single item, and Needed Boxes is not empty."""
    if not row.item_code:
        return False
    item = frappe.get_cached_doc("Item", row.item_code)
    return bool(
        item.get("custom_finished_good")
        and not item.get("custom_single_item")
        and item.get("custom_needed_boxes")
    )


def _zero_row(row):
    row.rate = 0
    row.price_list_rate = 0
    row.discount_percentage = 0
    row.discount_amount = 0


def zero_value(doc, method=None):
    """Purchase Order: flag finished goods that need boxes.
    The rate is only zeroed if ZERO_FG_ROW_ON_PO is True."""
    for row in doc.items:
        if _needs_boxes(row):
            row.custom_finished_good = 1
            if ZERO_FG_ROW_ON_PO:
                _zero_row(row)


def add_box_items(doc, method=None):
    """Purchase Receipt / Purchase Invoice:
    for every finished good that needs boxes, add its Needed Boxes
    (qty = box qty x finished good qty).

    - finished good with no boxes -> row stays as it is, with its rate
    - single item                 -> row stays as it is, with its rate
    """
    existing = {r.item_code for r in doc.items if r.item_code}
    to_add = []

    for row in list(doc.items):
        if not row.item_code:
            continue

        item = frappe.get_cached_doc("Item", row.item_code)
        if not item.get("custom_finished_good"):
            continue

        row.custom_finished_good = 1

        # Ray (no boxes) and single items: leave the row alone, keep the rate
        if not _needs_boxes(row):
            continue

        if ZERO_FG_ROW_ON_RECEIPT_INVOICE:
            _zero_row(row)

        for box in item.get("custom_needed_boxes") or []:
            box_code = _pick(box, ["item", "item_code", "box_item"])
            if not box_code or box_code in existing:
                continue   # already in the list, do not add it twice

            box_qty = flt(_pick(box, ["qty", "quantity"])) or 1
            box_item = frappe.get_cached_doc("Item", box_code)
            uom = _pick(box, ["uom"]) or box_item.stock_uom

            to_add.append({
                "item_code": box_code,
                "item_name": box_item.item_name,
                "description": box_item.description or box_item.item_name,
                "qty": box_qty * flt(row.qty or 1),
                "uom": uom,
                "stock_uom": box_item.stock_uom,
                "conversion_factor": 1,
                "rate": flt(box.get("rate")),   # 0 unless the box table has a rate field
                "warehouse": row.get("warehouse"),
                "cost_center": row.get("cost_center"),
                "expense_account": row.get("expense_account"),
            })
            existing.add(box_code)

    for values in to_add:
        doc.append("items", values)

    for i, r in enumerate(doc.items, 1):
        r.idx = i