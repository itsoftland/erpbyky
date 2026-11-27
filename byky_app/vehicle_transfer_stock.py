import frappe

# Stock Entry Type used for vehicle transfers (Stock Entry -> Stock Entry Type)
ENTRY_TYPE = "Vehicle Transfer"

# fieldname of the "Is Warehouse storage" checkbox on Stock Entry
# (the first one that exists on the DocType is used)
STORAGE_FLAGS = ("custom_is_warehouse_storage", "custom_is_warehouse_storage_")


@frappe.whitelist()
def make_stock_entry(source_name):
    """
    Build an UNSAVED Stock Entry from a Vehicle Transfer Request. The form
    opens filled in; the user checks it and saves.

        Stock Entry Type       = Vehicle Transfer
        Is Warehouse storage   = ticked
        items[0].s_warehouse   = request.source_warehouse
        items[0].t_warehouse   = request.target_warehouse
        items[0].item_code     = request.vehicle_type
        items[0].serial_no     = request.vehicle_number
        items[0].qty           = 1

    Called by the "Create > Stock Entry" button through
    frappe.model.open_mapped_doc.
    """
    request = frappe.get_doc("Vehicle Transfer Request", source_name)
    request.check_permission("read")

    required = (
        ("source_warehouse", "Source Warehouse"),
        ("target_warehouse", "Target Warehouse"),
        ("vehicle_type", "Vehicle Type"),
        ("vehicle_number", "Vehicle Number"),
    )

    missing = [label for field, label in required if not request.get(field)]

    if missing:
        frappe.throw(f"The request has no {', '.join(missing)}.")

    purpose = frappe.db.get_value("Stock Entry Type", ENTRY_TYPE, "purpose")

    if purpose is None and not frappe.db.exists("Stock Entry Type", ENTRY_TYPE):
        frappe.throw(f"Stock Entry Type '{ENTRY_TYPE}' does not exist.")

    meta = frappe.get_meta("Stock Entry")
    flag = next((f for f in STORAGE_FLAGS if meta.has_field(f)), None)

    if not flag:
        frappe.throw(
            "Stock Entry has no 'Is Warehouse storage' field. Tried: "
            + ", ".join(STORAGE_FLAGS)
            + ". Put the real fieldname in STORAGE_FLAGS (vehicle_transfer_stock.py)."
        )

    stock_uom = frappe.db.get_value("Item", request.vehicle_type, "stock_uom")

    if not stock_uom:
        frappe.throw(
            f"Item '{request.vehicle_type}' has no Stock UOM set in the Item master."
        )

    entry = frappe.new_doc("Stock Entry")
    entry.stock_entry_type = ENTRY_TYPE
    entry.purpose = purpose or "Material Transfer"
    entry.company = frappe.db.get_value("Warehouse", request.source_warehouse, "company")
    entry.set(flag, 1)

    # also how the button finds an earlier Stock Entry for this request
    entry.remarks = (
        f"Vehicle Transfer Request: {request.name} "
        f"({request.source_branch} to {request.target_branch})"
    )

    row = {
        "item_code": request.vehicle_type,
        "qty": 1,
        "uom": stock_uom,
        "stock_uom": stock_uom,
        "conversion_factor": 1,
        "transfer_qty": 1,
        "s_warehouse": request.source_warehouse,
        "t_warehouse": request.target_warehouse,
        "serial_no": request.vehicle_number,
    }

    # ERPNext v15: serial numbers typed in the row, no bundle needed first
    if frappe.get_meta("Stock Entry Detail").has_field("use_serial_batch_fields"):
        row["use_serial_batch_fields"] = 1

    entry.append("items", row)

    return entry