import frappe
from frappe.utils import flt


LOCATION_TABLE = "custom_storage_location"
PROCESSED_FIELD = "custom_storage_locations_processed"

LOCATION_FIELDS = (
    "warehouse",
    "floor",
    "area",
    "shelf",
    "rack",
)


def update_item_storage_locations(doc, method=None):
    """Add submitted Stock Entry quantities to the corresponding Item locations."""

    if doc.docstatus != 1:
        return

    if doc.get(PROCESSED_FIELD):
        return

    locations = doc.get(LOCATION_TABLE) or []

    if not locations:
        frappe.db.set_value(
            "Stock Entry",
            doc.name,
            PROCESSED_FIELD,
            1,
            update_modified=False,
        )
        return

    # Group rows by Item to avoid repeatedly loading the same Item.
    grouped_locations = {}

    for loc in locations:
        if not loc.item:
            continue

        if not loc.warehouse:
            frappe.throw(
                f"Warehouse is required for the Storage Location row "
                f"in Stock Entry {doc.name}."
            )

        grouped_locations.setdefault(loc.item, []).append(loc)

    for item_code, rows in grouped_locations.items():
        if not frappe.db.exists("Item", item_code):
            frappe.throw(f"Item {item_code} does not exist.")

        item_doc = frappe.get_doc("Item", item_code)

        for loc in rows:
            matching_row = None

            for existing in item_doc.get(LOCATION_TABLE) or []:
                if all(
                    existing.get(field) == loc.get(field)
                    for field in LOCATION_FIELDS
                ):
                    matching_row = existing
                    break

            quantity = flt(loc.quantity)

            if matching_row:
                matching_row.quantity = (
                    flt(matching_row.quantity) + quantity
                )

                if loc.uom:
                    matching_row.uom = loc.uom

            else:
                # Let Frappe create and name the child row on the server.
                item_doc.append(
                    LOCATION_TABLE,
                    {
                        "item": item_code,
                        "warehouse": loc.warehouse,
                        "floor": loc.floor,
                        "area": loc.area,
                        "shelf": loc.shelf,
                        "rack": loc.rack,
                        "uom": loc.uom,
                        "quantity": quantity,
                    },
                )

        # Frappe manages child-row names and persistence.
        item_doc.save(ignore_permissions=True)

    # Mark processed only after all Items have been updated.
    frappe.db.set_value(
        "Stock Entry",
        doc.name,
        PROCESSED_FIELD,
        1,
        update_modified=False,
    )


def reverse_item_storage_locations(doc, method=None):
    """Reverse the location quantities when a processed Stock Entry is cancelled."""

    if not doc.get(PROCESSED_FIELD):
        return

    grouped_locations = {}

    for loc in doc.get(LOCATION_TABLE) or []:
        if loc.item:
            grouped_locations.setdefault(loc.item, []).append(loc)

    for item_code, rows in grouped_locations.items():
        if not frappe.db.exists("Item", item_code):
            continue

        item_doc = frappe.get_doc("Item", item_code)

        for loc in rows:
            matching_row = None

            for existing in item_doc.get(LOCATION_TABLE) or []:
                if all(
                    existing.get(field) == loc.get(field)
                    for field in LOCATION_FIELDS
                ):
                    matching_row = existing
                    break

            if not matching_row:
                continue

            remaining = flt(matching_row.quantity) - flt(loc.quantity)

            if remaining < 0:
                frappe.throw(
                    f"Cannot cancel Stock Entry {doc.name}: "
                    f"Item {item_code} location quantity would become negative."
                )

            matching_row.quantity = remaining

            # Remove the row if its quantity reaches zero.
            if remaining == 0:
                item_doc.remove(matching_row)

        item_doc.save(ignore_permissions=True)

    frappe.db.set_value(
        "Stock Entry",
        doc.name,
        PROCESSED_FIELD,
        0,
        update_modified=False,
    )