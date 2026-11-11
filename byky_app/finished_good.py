import frappe


def _needs_boxes(row):
    """True only when the row must be replaced by its boxes:
    finished good, not a single item, and Needed Boxes is not empty."""
    if not row.item_code:
        return False
    item = frappe.get_cached_doc("Item", row.item_code)
    return bool(
        item.get("custom_finished_good")
        and not item.get("custom_single_item")
        and item.get("custom_needed_boxes")
    )


def zero_value(doc, method=None):
    """Purchase Order: a finished good that will be replaced by boxes
    stays as a label with no value. Others keep their normal rate."""
    for row in doc.items:
        if _needs_boxes(row):
            row.custom_finished_good = 1
            row.rate = 0
            row.price_list_rate = 0
            row.discount_percentage = 0
            row.discount_amount = 0


def drop_rows(doc, method=None):
    """Purchase Receipt / Purchase Invoice: rows that have boxes are
    removed, because only the boxes are received and billed."""
    keep = [r for r in doc.items if not _needs_boxes(r)]

    if not keep:
        frappe.throw("No billable items are left. Check the Needed Boxes of the items.")

    doc.set("items", keep)
    for i, r in enumerate(doc.items, 1):
        r.idx = i