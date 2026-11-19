import frappe

STANDARD_FIELDS = {
    "name", "owner", "creation", "modified", "modified_by", "docstatus",
    "idx", "parent", "parentfield", "parenttype", "doctype",
}


def has_stock_history(item_code):
    return bool(frappe.db.exists(
        "Stock Ledger Entry", {"item_code": item_code, "is_cancelled": 0}
    ))


def resolve_item_group(value):
    """Return the Item Group ID (name) for a value that may be the ID itself
    or a display value such as Item Group Name / Item Group Code.

    Never creates a group. Throws a clear error if nothing matches.
    """
    if not value:
        return value

    # 1. Already a valid ID
    if frappe.db.exists("Item Group", value):
        return value

    # 2. Match on display/code fields (only those that exist on this site)
    meta = frappe.get_meta("Item Group")
    for fieldname in ("item_group_name", "item_group_code"):
        if meta.has_field(fieldname):
            name = frappe.db.get_value("Item Group", {fieldname: value}, "name")
            if name:
                return name

    frappe.throw(
        f"Item Group '{value}' does not exist. Create it under Stock > Item Group "
        f"or correct the Category on the Vehicle Type."
    )


def upsert_item(item_code, values, has_serial_no=0, serial_series=None,
                suppliers=None, child_tables=None):
    """Create the Item if missing, otherwise update it with the given values.

    values:       plain Item fields {fieldname: value}
    suppliers:    child rows for Supplier Items, or None to leave untouched
    child_tables: {item_table_fieldname: source_rows} for custom child tables
    """
    if not item_code:
        return None

    is_new = not frappe.db.exists("Item", item_code)

    if is_new:
        item = frappe.new_doc("Item")
        item.item_code = item_code
    else:
        item = frappe.get_doc("Item", item_code)

    # Convert the Category value to the real Item Group ID before setting it
    values = dict(values or {})
    if values.get("item_group"):
        values["item_group"] = resolve_item_group(values["item_group"])

    for field, value in values.items():
        item.set(field, value)

    # Has Serial No / series cannot change once the item has stock entries
    if is_new or not has_stock_history(item_code):
        item.has_serial_no = 1 if has_serial_no else 0
        item.serial_no_series = serial_series if has_serial_no else None
    elif bool(item.has_serial_no) != bool(has_serial_no):
        frappe.msgprint(
            f"Item {item_code} already has stock, so Has Serial No was not changed.",
            alert=True, indicator="orange",
        )

    if suppliers is not None:
        item.set("supplier_items", [])
        for row in suppliers:
            item.append("supplier_items", {
                "supplier": row.supplier,
                "supplier_part_no": row.get("supplier_part_no") or row.get("supplier_part_number"),
            })

    for table, rows in (child_tables or {}).items():
        item.set(table, [])
        for row in rows or []:
            row_values = {k: v for k, v in row.as_dict().items() if k not in STANDARD_FIELDS}
            item.append(table, row_values)

    if is_new:
        item.insert(ignore_permissions=True)
    else:
        item.save(ignore_permissions=True)

    frappe.msgprint(
        f"Item {item_code} {'created' if is_new else 'updated'}.", alert=True
    )
    return item