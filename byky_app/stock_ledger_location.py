import frappe
from frappe import _


# ---------------------------------------------------------
# CONFIGURATION
# ---------------------------------------------------------

# Actual checkbox fieldname, including the trailing underscore
FLAG = "custom_is_warehouse_storage_"

# Child table fieldnames
SE_TABLE = "custom_storage_location"
SLE_TABLE = "custom_storage_location"

# NEW: finished goods (Item custom_finished_good = 1) are stored in their own table
FG_TABLE = "custom_finished_item_storage"

# Storage Location child-table fieldnames
ROW_ITEM = "item"
ROW_WAREHOUSE = "warehouse"
ROW_SHELF = "shelf"

# Fields to copy to the Stock Ledger Entry storage-location table
LOCATION_KEY_FIELDS = (
    "item",
    "warehouse",
    "floor",
    "area",
    "shelf",
    "rack",
)

# Standard child-table/system fields that should not be copied
SKIP_FIELDS = {
    "name",
    "owner",
    "creation",
    "modified",
    "modified_by",
    "docstatus",
    "idx",
    "parent",
    "parentfield",
    "parenttype",
    "doctype",
}


# ---------------------------------------------------------
# NEW: FINISHED GOOD CHECK
# ---------------------------------------------------------

def _is_finished_good(item):
    """True when the Item has custom_finished_good = 1."""
    return bool(item and frappe.get_cached_value("Item", item, "custom_finished_good"))


# ---------------------------------------------------------
# IDENTIFY THE FINISHED ITEM IN A MANUFACTURE STOCK ENTRY
# ---------------------------------------------------------

def is_finished_goods_row(doc, item, warehouse):
    """
    Return True only when the item/warehouse combination represents
    the finished product being received from a Manufacture Stock Entry.

    The production_item field is used when available. The item's
    is_finished_item flag is also checked when present.
    """

    if doc.get("purpose") != "Manufacture":
        return False

    if not item or not warehouse:
        return False

    production_item = doc.get("production_item")

    for row in doc.get("items") or []:

        if row.get("item_code") != item:
            continue

        if row.get("t_warehouse") != warehouse:
            continue

        # Standard Manufacture Stock Entry finished product
        if production_item and item == production_item:
            return True

        # Fallback for a row explicitly marked as the finished item
        if row.get("is_finished_item"):
            return True

    return False


# ---------------------------------------------------------
# STOCK ENTRY SUBMIT HOOK
# ---------------------------------------------------------

def apply_locations_to_ledger(doc, method=None):
    """
    Runs on Stock Entry submission.

    Behaviour:
    1. If Is Warehouse storage is checked, skip custom processing.
    2. For ordinary incoming stock, require a Storage Location row
       with a Shelf.
    3. For the finished product in a Manufacture Stock Entry,
       do not require a custom Storage Location row or Shelf.
    4. Copy valid Storage Location rows to matching Stock Ledger Entries.
    5. Avoid inserting duplicate location rows.
    6. NEW: finished goods are validated in the Finished Item Storage
       table; that table is not copied to the Stock Ledger Entry.
    """

    # Warehouse storage: skip custom storage-location processing
    if doc.get(FLAG):
        return

    rows = doc.get(SE_TABLE) or []

    # Validate storage-location requirements
    _validate(doc, rows)

    # Nothing to copy
    if not rows:
        return

    # Verify that the Stock Ledger Entry has the configured child table
    sle_field = frappe.get_meta("Stock Ledger Entry").get_field(
        SLE_TABLE
    )

    if not sle_field or sle_field.fieldtype != "Table" or not sle_field.options:
        frappe.throw(
            _(
                "The field {0} is not configured as a child table "
                "in Stock Ledger Entry."
            ).format(SLE_TABLE)
        )

    child_doctype = sle_field.options

    child_meta = frappe.get_meta(child_doctype)

    target_fields = {
        field.fieldname
        for field in child_meta.fields
    }

    key_fields = [
        field
        for field in LOCATION_KEY_FIELDS
        if field in target_fields
    ]

    if not key_fields:
        frappe.throw(
            _(
                "No storage-location key fields were found in "
                "the child DocType {0}."
            ).format(child_doctype)
        )

    # Process every custom Storage Location row
    for row in rows:

        item = row.get(ROW_ITEM)
        warehouse = row.get(ROW_WAREHOUSE)

        if not item or not warehouse:
            continue

        # The finished product in Manufacture does not need
        # custom location rows copied into its Stock Ledger Entry.
        if is_finished_goods_row(doc, item, warehouse):
            continue

        # Find the matching Stock Ledger Entries created by this Stock Entry
        sle_names = frappe.get_all(
            "Stock Ledger Entry",
            filters={
                "voucher_type": doc.doctype,
                "voucher_no": doc.name,
                "item_code": item,
                "warehouse": warehouse,
                "is_cancelled": 0,
            },
            pluck="name",
        )

        if not sle_names:
            frappe.throw(
                _(
                    "Row {0} (Storage Location): no Stock Ledger Entry "
                    "was found for item {1} in warehouse {2}."
                ).format(row.idx, item, warehouse)
            )

        # Copy only fields that exist in the destination child DocType
        values = {
            key: value
            for key, value in row.as_dict().items()
            if (
                key in target_fields
                and key not in SKIP_FIELDS
                and value not in (None, "")
            )
        }

        # Create a consistent key for duplicate detection
        location_key = tuple(
            str(values.get(field) or "")
            for field in key_fields
        )

        for sle_name in sle_names:

            existing_rows = frappe.get_all(
                child_doctype,
                filters={
                    "parent": sle_name,
                    "parenttype": "Stock Ledger Entry",
                    "parentfield": SLE_TABLE,
                },
                fields=key_fields,
            )

            duplicate_exists = any(
                tuple(
                    str(existing.get(field) or "")
                    for field in key_fields
                ) == location_key
                for existing in existing_rows
            )

            if duplicate_exists:
                continue

            # Insert the location details into the SLE child table
            frappe.get_doc({
                "doctype": child_doctype,
                "parent": sle_name,
                "parenttype": "Stock Ledger Entry",
                "parentfield": SLE_TABLE,
                **values,
            }).insert(ignore_permissions=True)


# ---------------------------------------------------------
# VALIDATE STORAGE LOCATIONS
# ---------------------------------------------------------

def _validate(doc, rows):
    """
    Validation rules:

    1. Item and Warehouse must be supplied for each Storage Location row.
    2. Each Storage Location row must match an item/warehouse combination
       in the Stock Entry Items table.
    3. Shelf is mandatory for ordinary incoming stock.
    4. The finished product in a Manufacture Stock Entry does not require
       a custom Storage Location row or Shelf.
    5. Other incoming items still require their own Storage Location rows.
    6. NEW: finished goods (Item custom_finished_good = 1) are checked in
       the Finished Item Storage table instead: Item and Warehouse are
       required, no Shelf is needed.
    """

    # All item/warehouse combinations in the Stock Entry
    moves = set()

    # Item/warehouse combinations receiving stock
    incoming = set()

    for item_row in doc.get("items") or []:

        if item_row.get("s_warehouse"):
            moves.add((
                item_row.get("item_code"),
                item_row.get("s_warehouse"),
            ))

        if item_row.get("t_warehouse"):
            moves.add((
                item_row.get("item_code"),
                item_row.get("t_warehouse"),
            ))

            incoming.add((
                item_row.get("item_code"),
                item_row.get("t_warehouse"),
            ))

    # Track incoming combinations covered by Storage Location rows
    covered = set()

    for row in rows:

        item = row.get(ROW_ITEM)
        warehouse = row.get(ROW_WAREHOUSE)

        if not item or not warehouse:
            frappe.throw(
                _(
                    "Row {0} (Storage Location): Item and Warehouse "
                    "are required."
                ).format(row.idx)
            )

        combination = (item, warehouse)

        if combination not in moves:
            allowed = sorted({w for (i, w) in moves if i == item})
            frappe.throw(
                _(
                    "Row {0} (Finished Item Storage): item {1} with warehouse "
                    "{2} is not present in the Items table of this "
                    "Stock Entry. Warehouses in the Items table for this item: {3}."
                ).format(row.idx, item, warehouse, ", ".join(allowed) or _("none"))
            )

        # Finished product: custom shelf information is optional
        if is_finished_goods_row(doc, item, warehouse):
            covered.add(combination)
            continue

        # Ordinary stock entering a warehouse must have a Shelf
        if not row.get(ROW_SHELF):
            frappe.throw(
                _(
                    "Row {0} (Storage Location): Shelf is required "
                    "for item {1} in warehouse {2}."
                ).format(row.idx, item, warehouse)
            )

        covered.add(combination)

    # NEW: finished goods, validate the Finished Item Storage rows
    fg_covered = set()

    for row in doc.get(FG_TABLE) or []:

        item = row.get(ROW_ITEM)
        warehouse = row.get(ROW_WAREHOUSE)

        if not item or not warehouse:
            frappe.throw(
                _(
                    "Row {0} (Finished Item Storage): Item and Warehouse "
                    "are required."
                ).format(row.idx)
            )

        combination = (item, warehouse)

        if combination not in moves:
            frappe.throw(
                _(
                    "Row {0} (Finished Item Storage): item {1} with warehouse "
                    "{2} is not present in the Items table of this "
                    "Stock Entry."
                ).format(row.idx, item, warehouse)
            )

        if not _is_finished_good(item):
            frappe.throw(
                _(
                    "Row {0} (Finished Item Storage): item {1} is not a "
                    "finished good. Use the Storage Location table for it."
                ).format(row.idx, item)
            )

        fg_covered.add(combination)

    # CHANGED: check incoming stock that still lacks a storage row.
    # Exempt only the actual finished product in Manufacture.
    # Finished goods are looked up in the Finished Item Storage table,
    # all other items in the Storage Location table.
    for item, warehouse in sorted(incoming):

        if is_finished_goods_row(doc, item, warehouse):
            continue

        if _is_finished_good(item):
            if (item, warehouse) not in fg_covered:
                frappe.throw(
                    _(
                        "No Finished Item Storage row for item {0} in "
                        "warehouse {1}."
                    ).format(item, warehouse)
                )
        elif (item, warehouse) not in covered:
            frappe.throw(
                _(
                    "No Storage Location row for item {0} in warehouse {1}. "
                    "Add a Storage Location row with a Shelf, or tick "
                    "'Is Warehouse storage'."
                ).format(item, warehouse)
            )