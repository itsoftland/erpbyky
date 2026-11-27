import frappe
from frappe.utils import today


def allow_warehouse_change(doc, method=None):
    """Serial No: before_validate
    Skips ERPNext's "Warehouse cannot be changed" check on manual
    edits and Data Import.
    """
    doc.via_stock_ledger = True


def set_production_date(doc, method=None):
    """Serial No: before_insert
    Fills today's date when a Serial No is created from the form or API.
    """
    if not doc.get("custom_production_date"):
        doc.custom_production_date = today()


def set_production_date_from_bundle(doc, method=None):
    """Serial and Batch Bundle: on_submit
    Fills today's date on auto-generated serials, which skip before_insert.
    """
    if doc.type_of_transaction != "Inward":
        return

    for row in doc.entries:
        if not row.serial_no:
            continue

        current_date = frappe.db.get_value(
            "Serial No", row.serial_no, "custom_production_date"
        )

        if not current_date:
            frappe.db.set_value(
                "Serial No",
                row.serial_no,
                "custom_production_date",
                today(),
                update_modified=False,
            )