import frappe
from frappe import _


# ------------------------------------------------------------------
# CONFIGURATION
# ------------------------------------------------------------------

FLAG = "custom_is_warehouse_storage"
SE_TABLE = "custom_storage_location"
SLE_TABLE = "custom_storage_location"

ROW_ITEM = "item"
ROW_WAREHOUSE = "warehouse"
ROW_SHELF = "shelf"

# Finished-goods warehouses require a warehouse only.
# Storage Location rows, shelves, floors, areas, and racks are optional.
FINISHED_GOODS_WAREHOUSES = {
    "Bajaj Finished Goods - B",
}

# Fields used to identify a storage location uniquely.
LOCATION_KEY_FIELDS = (
    "item",
    "warehouse",
    "floor",
    "area",
    "shelf",
    "rack",
)

# Fields that must not be copied to another document.
SKIP = {
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


def apply_locations_to_ledger(doc, method=None):
    """
    Stock Entry on_submit.

    Is Warehouse storage = Checked:
        Skip custom storage-location processing.

    Is Warehouse storage = Unchecked:
        Validate storage locations and copy them to the
        corresponding Stock Ledger Entry child table.

    Finished goods:
        Only the standard warehouse is required.
        Custom Storage Location rows are not required.
    """

    # Skip custom processing for ordinary warehouse entries.
    if doc.get(FLAG):
        return

    rows = doc.get(SE_TABLE) or []

    # Validate storage locations before processing.
    _validate(doc, rows)

    if not rows:
        return

    # Get the child DocType configured for the SLE table field.
    sle_field = frappe.get_meta("Stock Ledger Entry").get_field(
        SLE_TABLE
    )

    if not sle_field or not sle_field.options:
        frappe.throw(
            _("The field {0} is not configured as a child table "
              "in Stock Ledger Entry.").format(SLE_TABLE)
        )

    child_dt = sle_field.options

    # Get available fields in the SLE child DocType.
    target_fields = {
        field.fieldname
        for field in frappe.get_meta(child_dt).fields
    }

    # Use only location fields supported by the child DocType.
    key_fields = [
        field
        for field in LOCATION_KEY_FIELDS
        if field in target_fields
    ]

    if not key_fields:
        frappe.throw(
            _("No storage-location key fields were found in {0}.").format(
                child_dt
            )
        )

    # Process each storage-location row independently.
    for row in rows:

        item = row.get(ROW_ITEM)
        warehouse = row.get(ROW_WAREHOUSE)

        # Finished goods do not need custom Storage Location records.
        if warehouse in FINISHED_GOODS_WAREHOUSES:
            continue

        # Find corresponding Stock Ledger Entries.
        entries = frappe.get_all(
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

        if not entries:
            frappe.throw(
                _(
                    "Row {0} (Storage Location): no Stock Ledger Entry "
                    "found for item {1} in warehouse {2}."
                ).format(
                    row.idx,
                    item,
                    warehouse,
                )
            )

        # Copy only fields supported by the destination child DocType.
        values = {
            key: value
            for key, value in row.as_dict().items()
            if key in target_fields
            and key not in SKIP
            and value not in (None, "")
        }

        # Identify this storage location.
        location_key = tuple(
            str(values.get(field) or "")
            for field in key_fields
        )

        # Process every matching Stock Ledger Entry.
        for sle_name in entries:

            # Read existing location rows for this SLE.
            existing_rows = frappe.get_all(
                child_dt,
                filters={
                    "parent": sle_name,
                    "parenttype": "Stock Ledger Entry",
                    "parentfield": SLE_TABLE,
                },
                fields=key_fields,
            )

            # Skip only if the same location already exists.
            duplicate = any(
                tuple(
                    str(existing.get(field) or "")
                    for field in key_fields
                ) == location_key
                for existing in existing_rows
            )

            if duplicate:
                continue

            # Insert the new storage-location row.
            frappe.get_doc(
                {
                    "doctype": child_dt,
                    "parent": sle_name,
                    "parenttype": "Stock Ledger Entry",
                    "parentfield": SLE_TABLE,
                    **values,
                }
            ).insert(ignore_permissions=True)


def _validate(doc, rows):
    """
    Validate storage locations for Stock Entry.

    Normal warehouses:
        Incoming items require a Storage Location row with a shelf.

    Finished-goods warehouses:
        Only the standard warehouse is required.
        No custom Storage Location row or shelf is required.
    """

    moves = set()
    incoming = set()

    # Collect item movements from the Stock Entry Items table.
    for item_row in doc.items:

        if item_row.s_warehouse:
            moves.add(
                (
                    item_row.item_code,
                    item_row.s_warehouse,
                )
            )

        if item_row.t_warehouse:
            moves.add(
                (
                    item_row.item_code,
                    item_row.t_warehouse,
                )
            )

            incoming.add(
                (
                    item_row.item_code,
                    item_row.t_warehouse,
                )
            )

    # Validate each provided Storage Location row.
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

        # Ensure the item and warehouse exist in the Stock Entry.
        if (item, warehouse) not in moves:
            frappe.throw(
                _(
                    "Row {0} (Storage Location): item {1} with "
                    "warehouse {2} is not present in the Items "
                    "table of this Stock Entry."
                ).format(
                    row.idx,
                    item,
                    warehouse,
                )
            )

        # Finished goods require no shelf or other location details.
        if warehouse in FINISHED_GOODS_WAREHOUSES:
            covered.add((item, warehouse))
            continue

        # Other warehouses require a shelf.
        if not row.get(ROW_SHELF):
            frappe.throw(
                _(
                    "Row {0} (Storage Location): Shelf is required."
                ).format(row.idx)
            )

        covered.add((item, warehouse))

    # Every incoming item must have a Storage Location row,
    # except items entering a configured finished-goods warehouse.
    missing = {
        (item, warehouse)
        for item, warehouse in incoming
        if warehouse not in FINISHED_GOODS_WAREHOUSES
        and (item, warehouse) not in covered
    }

    if missing:

        item, warehouse = sorted(missing)[0]

        frappe.throw(
            _(
                "No Storage Location row for item {0} in warehouse "
                "{1}. Add its shelf, or tick 'Is Warehouse storage'."
            ).format(
                item,
                warehouse,
            )
        )