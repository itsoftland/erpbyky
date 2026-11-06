import frappe


def _is_fg(row):
    if row.get("custom_finished_good"):
        return True
    # fallback: the fetched value may not have arrived yet
    return bool(row.item_code and frappe.db.get_value("Item", row.item_code, "custom_finished_good"))


def zero_value(doc, method=None):
    """Purchase Order: keep the finished good row as a label with no value."""
    for row in doc.items:
        if _is_fg(row):
            row.custom_finished_good = 1
            row.rate = 0
            row.price_list_rate = 0
            row.discount_percentage = 0
            row.discount_amount = 0


def drop_rows(doc, method=None):
    """Purchase Receipt / Purchase Invoice: only the boxes are received and billed."""
    doc.items = [r for r in doc.items if not _is_fg(r)]
    for i, r in enumerate(doc.items, 1):
        r.idx = i