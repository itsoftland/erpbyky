import frappe


@frappe.whitelist()
def get_item_storage_summary(search_text=None):
    # Inventory data must not be exposed to logged-out visitors.
    if frappe.session.user == "Guest":
        frappe.throw("Please log in to view inventory.")

    if not frappe.has_permission("Item", "read"):
        frappe.throw("You do not have permission to view Items.")

    locations = frappe.get_all(
        "Storage Location",
        filters={
            "parenttype": "Item",
            "parentfield": "custom_storage_location",
        },
        fields=[
            "parent",
            "item",
            "warehouse",
            "floor",
            "area",
            "shelf",
            "rack",
            "uom",
            "quantity",
        ],
        order_by="parent asc",
    )

    search_text = (search_text or "").strip().lower()
    results = []

    # Cache item names and calculate total quantity per item.
    item_info = {}
    totals = {}

    for row in locations:
        item_code = row.item or row.parent

        if item_code not in item_info:
            item_info[item_code] = frappe.db.get_value(
                "Item",
                item_code,
                "item_name",
            ) or ""

        totals[item_code] = (
            totals.get(item_code, 0) + (row.quantity or 0)
        )

    for row in locations:
        item_code = row.item or row.parent
        item_name = item_info.get(item_code, "")

        if search_text and (
            search_text not in item_code.lower()
            and search_text not in item_name.lower()
        ):
            continue

        results.append({
            "item": item_code,
            "item_name": item_name,
            "total_quantity": totals.get(item_code, 0),
            "location_quantity": row.quantity or 0,
            "uom": row.uom or "",
            "warehouse": row.warehouse or "",
            "floor": row.floor or "",
            "area": row.area or "",
            "shelf": row.shelf or "",
            "rack": row.rack or "",
        })

    return results