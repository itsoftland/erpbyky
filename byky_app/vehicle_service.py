import frappe


@frappe.whitelist()
def get_vehicle_details(serial_no):
    """Fetch Item, Brand, and Item Group for a selected Serial No."""

    if not serial_no:
        frappe.throw("Please select a Vehicle Number.")

    if not frappe.db.exists("Serial No", serial_no):
        frappe.throw(f"Serial No '{serial_no}' does not exist.")

    serial = frappe.get_doc("Serial No", serial_no)

    if not serial.item_code:
        frappe.throw(
            f"No Item is linked to Serial No '{serial_no}'."
        )

    item = frappe.get_doc("Item", serial.item_code)

    return {
        "item": item.name,
        "brand": item.get("brand") or "",
        "item_group": item.item_group or ""
    }