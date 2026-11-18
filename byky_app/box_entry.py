import frappe

from byky_app.item_sync import upsert_item


def sync_box_item(doc, method=None):
    upsert_item(
        doc.box_name,
        {
            "item_name": doc.box_name,
            "item_group": "Box",
            "stock_uom": "Box",
            "custom_is_box_item": 1,
            "custom_item_type": "Box Item",   # confirm fieldname in Customize Form
            "custom_supplier": doc.supplier,
            "custom_box_name": doc.name,      # Link to Box Entry
        },
        has_serial_no=doc.has_serial_no,
        serial_series=doc.serial_no,
        child_tables={"custom_box_items": doc.get("box_items")},   # Box Items -> Inside Items
    )


@frappe.whitelist()
def get_box_items(box_entry):
    """Return the Box Items table of a Box Entry for the popup."""
    frappe.has_permission("Box Entry", "read", box_entry, throw=True)
    doc = frappe.get_doc("Box Entry", box_entry)
    return [
        {
            "item": row.get("item"),
            "uom": row.get("uom"),
            "quantity": row.get("quantity") or row.get("qty"),
        }
        for row in doc.get("box_items") or []
    ]


@frappe.whitelist()
def save_box_items(box_entry, items):
    """Replace the Box Items table of a Box Entry with the rows from the popup."""
    items = frappe.parse_json(items) or []

    doc = frappe.get_doc("Box Entry", box_entry)
    doc.check_permission("write")

    # Find the real quantity fieldname of the child table (quantity or qty)
    child_meta = frappe.get_meta(doc.meta.get_field("box_items").options)
    qty_field = "quantity" if child_meta.has_field("quantity") else "qty"

    doc.set("box_items", [])
    for row in items:
        if not row.get("item"):
            continue
        uom = row.get("uom") or frappe.db.get_value("Item", row.get("item"), "stock_uom")
        doc.append("box_items", {
            "item": row.get("item"),
            "uom": uom,
            qty_field: row.get("quantity") or 0,
        })
    doc.save()
    return get_box_items(box_entry)