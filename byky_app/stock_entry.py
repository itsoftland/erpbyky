import frappe


def fill_box_name(doc, method=None):
    """Copy the Item's Box Name (custom_box_name) into each row of the items table.

    Used for Stock Entry and Purchase Receipt (both have an "items" table
    with a custom_box_name column).
    """
    for row in doc.get("items") or []:
        if not row.item_code:
            continue
        box = frappe.db.get_value("Item", row.item_code, "custom_box_name")
        if box:
            row.custom_box_name = box