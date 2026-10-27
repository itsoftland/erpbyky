import frappe
from frappe.utils import flt


# ---------------------------------------------------------
# CONFIGURATION
# ---------------------------------------------------------

LOCATION_TABLE = "custom_storage_location"
PROCESSED_FIELD = "custom_storage_locations_processed"

WO_SOURCE_TABLE = "custom_source_warehouse_storage_location"
WO_FINISHED_TABLE = "custom_finished_product_storage_location"

LOCATION_FIELDS = (
    "warehouse",
    "floor",
    "area",
    "shelf",
    "rack",
)

COPY_FIELDS = LOCATION_FIELDS + ("uom", "quantity")


# ---------------------------------------------------------
# HELPERS
# ---------------------------------------------------------

def normalize(value):
    """Normalize values for storage-location comparisons."""
    return str(value or "").strip()


def find_matching_row(item_doc, loc):
    """
    Find an Item storage-location row using:
    warehouse, floor, area, shelf and rack.
    """

    for existing in item_doc.get(LOCATION_TABLE) or []:

        if all(
            normalize(existing.get(field))
            == normalize(loc.get(field))
            for field in LOCATION_FIELDS
        ):
            return existing

    return None


def get_location_direction(doc, loc):
    """
    Determine the stock movement direction for one storage row.

    -1 = stock is consumed from the source warehouse.
    +1 = stock enters the target warehouse.
     0 = stock stays in the same warehouse (the row's Source and Target
         Warehouse are the same, for example a Material Transfer for
         Manufacture into a WIP warehouse that is the source warehouse).
         The storage-location quantity does not change.

    Direction is determined using both item and warehouse,
    rather than item code alone.
    """

    matching_directions = set()
    same_warehouse_move = False

    warehouse = normalize(loc.warehouse)

    for row in doc.get("items") or []:

        if row.item_code != loc.item:
            continue

        source = normalize(row.s_warehouse)
        target = normalize(row.t_warehouse)

        if not warehouse:
            continue

        # Stock is consumed from the source warehouse.
        if source == warehouse and source != target:
            matching_directions.add(-1)

        # Stock enters the target warehouse.
        if target == warehouse and target != source:
            matching_directions.add(1)

        # Source and target are the same warehouse: nothing moves.
        if source and source == target == warehouse:
            same_warehouse_move = True

    if len(matching_directions) > 1:
        frappe.throw(
            f"Cannot determine the stock movement direction for "
            f"Item {loc.item} at Warehouse {loc.warehouse} "
            f"in Stock Entry {doc.name}. "
            "Check the Stock Entry Items and storage-location rows."
        )

    if matching_directions:
        return matching_directions.pop()

    if same_warehouse_move:
        return 0

    frappe.throw(
        f"Cannot find a matching source or target warehouse for "
        f"Item {loc.item} at Warehouse {loc.warehouse} "
        f"in Stock Entry {doc.name}."
    )


def apply_location_change(item_doc, loc, change, reverse=False):
    """
    Apply a quantity change to an Item storage-location row.

    Positive change = increase quantity.
    Negative change = decrease quantity.
    """

    matching_row = find_matching_row(item_doc, loc)

    if matching_row:

        current_qty = flt(matching_row.quantity)
        new_qty = current_qty + change

        if new_qty < 0:
            frappe.throw(
                f"Insufficient storage-location quantity for "
                f"Item {loc.item}.\n"
                f"Warehouse: {loc.warehouse}\n"
                f"Floor: {loc.floor or '-'}\n"
                f"Area: {loc.area or '-'}\n"
                f"Shelf: {loc.shelf or '-'}\n"
                f"Rack: {loc.rack or '-'}\n"
                f"Available: {current_qty}\n"
                f"Required reduction: {abs(change)}"
            )

        if new_qty == 0:
            item_doc.remove(matching_row)

        else:
            matching_row.quantity = new_qty

            if loc.uom:
                matching_row.uom = loc.uom

    else:

        # Never allow consumption from a location
        # that does not exist in the Item's location table.
        if change < 0:
            frappe.throw(
                f"Item {loc.item} has no matching storage-location "
                f"record for Warehouse {loc.warehouse}, "
                f"Floor {loc.floor or '-'}, "
                f"Area {loc.area or '-'}, "
                f"Shelf {loc.shelf or '-'}, "
                f"Rack {loc.rack or '-'}."
            )

        # A positive change creates a new location record.
        item_doc.append(
            LOCATION_TABLE,
            {
                "item": loc.item,
                "warehouse": loc.warehouse,
                "floor": loc.floor,
                "area": loc.area,
                "shelf": loc.shelf,
                "rack": loc.rack,
                "uom": loc.uom,
                "quantity": change,
            },
        )


# ---------------------------------------------------------
# COPY WORK ORDER STORAGE LOCATIONS
# ---------------------------------------------------------

def copy_work_order_storage_locations(doc, method=None):
    """
    Copy Work Order storage locations into a Manufacture
    Stock Entry before validation.

    If the Stock Entry table is empty:
        Copy source rows and finished-product rows.

    If rows already exist:
        Preserve existing values and fill missing fields.
    """

    if doc.docstatus != 0:
        return

    if doc.purpose != "Manufacture":
        return

    if not doc.work_order:
        return

    work_order = frappe.get_doc("Work Order", doc.work_order)

    wo_rows = (
        list(work_order.get(WO_SOURCE_TABLE) or [])
        + list(work_order.get(WO_FINISHED_TABLE) or [])
    )

    if not wo_rows:
        return

    existing_rows = doc.get(LOCATION_TABLE) or []

    # Create all rows if the Stock Entry table is empty.
    if not existing_rows:

        for src in wo_rows:

            if not src.item:
                continue

            row = doc.append(LOCATION_TABLE, {})

            row.item = src.item

            for field in COPY_FIELDS:
                row.set(field, src.get(field))

        return

    # Preserve existing rows and fill only missing values.
    wo_by_item = {}

    for src in wo_rows:

        if src.item:
            wo_by_item.setdefault(src.item, []).append(src)

    item_indexes = {}

    for row in existing_rows:

        if not row.item:
            continue

        candidates = wo_by_item.get(row.item)

        if not candidates:
            continue

        index = item_indexes.get(row.item, 0)

        item_indexes[row.item] = index + 1

        src = candidates[min(index, len(candidates) - 1)]

        for field in LOCATION_FIELDS + ("uom",):

            if not row.get(field) and src.get(field):
                row.set(field, src.get(field))


# ---------------------------------------------------------
# UPDATE ITEM STORAGE LOCATIONS
# ---------------------------------------------------------

def update_item_storage_locations(doc, method=None):
    """
    Update Item storage locations when a Stock Entry is submitted.

    Consumed raw materials:
        Decrease the source storage-location quantity.

    Finished products:
        Increase the target storage-location quantity.

    Same-warehouse transfers (Source = Target Warehouse):
        No change. The stock does not leave the warehouse.
    """

    if doc.docstatus != 1:
        return

    if doc.get(PROCESSED_FIELD):
        return

    locations = doc.get(LOCATION_TABLE) or []

    if not locations:
        # Do not mark the entry processed when no location
        # rows exist. This also helps identify missing data.
        return

    grouped_items = {}

    # Validate and group storage-location rows.
    for loc in locations:

        if not loc.item:
            continue

        if not loc.warehouse:
            frappe.throw(
                f"Warehouse is required for Item {loc.item} "
                f"in Stock Entry {doc.name}."
            )

        quantity = flt(loc.quantity)

        if quantity <= 0:
            frappe.throw(
                f"Quantity must be greater than zero for "
                f"Item {loc.item} in Stock Entry {doc.name}."
            )

        grouped_items.setdefault(loc.item, []).append(loc)

    # Update each Item document once.
    for item_code, rows in grouped_items.items():

        if not frappe.db.exists("Item", item_code):
            frappe.throw(
                f"Item {item_code} does not exist."
            )

        item_doc = frappe.get_doc("Item", item_code)
        changed = False

        for loc in rows:

            direction = get_location_direction(doc, loc)

            # Stock stays in the same warehouse: nothing to change.
            if direction == 0:
                continue

            # Source warehouse: subtract.
            # Target warehouse: add.
            change = flt(loc.quantity) * direction

            apply_location_change(
                item_doc,
                loc,
                change,
            )

            changed = True

        if changed:
            item_doc.save(ignore_permissions=True)

    # Mark processed only after all Item documents are updated.
    frappe.db.set_value(
        "Stock Entry",
        doc.name,
        PROCESSED_FIELD,
        1,
        update_modified=False,
    )


# ---------------------------------------------------------
# REVERSE CHANGES WHEN STOCK ENTRY IS CANCELLED
# ---------------------------------------------------------

def reverse_item_storage_locations(doc, method=None):
    """
    Reverse storage-location changes when a submitted
    Stock Entry is cancelled.
    """

    if not doc.get(PROCESSED_FIELD):
        return

    locations = doc.get(LOCATION_TABLE) or []

    grouped_items = {}

    for loc in locations:

        if loc.item:
            grouped_items.setdefault(loc.item, []).append(loc)

    for item_code, rows in grouped_items.items():

        if not frappe.db.exists("Item", item_code):
            frappe.throw(
                f"Cannot reverse Stock Entry {doc.name}: "
                f"Item {item_code} does not exist."
            )

        item_doc = frappe.get_doc("Item", item_code)
        changed = False

        for loc in rows:

            direction = get_location_direction(doc, loc)

            # Same-warehouse transfer: submit changed nothing.
            if direction == 0:
                continue

            # Reverse the original stock movement.
            change = -flt(loc.quantity) * direction

            apply_location_change(
                item_doc,
                loc,
                change,
                reverse=True,
            )

            changed = True

        if changed:
            item_doc.save(ignore_permissions=True)

    # Allow the document's processing flag to be reset.
    frappe.db.set_value(
        "Stock Entry",
        doc.name,
        PROCESSED_FIELD,
        0,
        update_modified=False,
    )