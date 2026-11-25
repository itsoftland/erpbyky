"""
byky_app/branch.py

Creates a Warehouse named after the Branch when a Branch is created, and links both:
    Branch.custom_warehouse  -> Warehouse
    Warehouse.custom_branch  -> Branch

hooks.py (already pointing here, keep only this one entry for Branch):

    doc_events = {
        "Branch": {
            "after_insert": "byky_app.branch.create_branch_warehouse",
        },
    }
"""

import frappe
from frappe import _

# Optional: e.g. "All Warehouses - B". Leave None to use the company default.
PARENT_WAREHOUSE = None


def create_branch_warehouse(doc, method=None):
    warehouse_name = (doc.branch or "").strip()
    if not warehouse_name:
        return

    company = doc.get("custom_company") or frappe.db.get_single_value(
        "Global Defaults", "default_company"
    )
    if not company:
        frappe.throw(_("Set a Company on the Branch or a default company in Global Defaults."))

    existing = frappe.db.get_value(
        "Warehouse", {"warehouse_name": warehouse_name, "company": company}, "name"
    )

    if existing:
        warehouse_id = existing
        frappe.db.set_value("Warehouse", warehouse_id, "custom_branch", doc.name)
    else:
        warehouse = frappe.get_doc(
            {
                "doctype": "Warehouse",
                "warehouse_name": warehouse_name,
                "company": company,
                "is_group": 0,
                "custom_branch": doc.name,
            }
        )
        if PARENT_WAREHOUSE:
            warehouse.parent_warehouse = PARENT_WAREHOUSE
        warehouse.insert(ignore_permissions=True)
        warehouse_id = warehouse.name

    # Branch is already saved at after_insert, so write the link directly.
    doc.db_set("custom_warehouse", warehouse_id)
    frappe.msgprint(
        _("Warehouse {0} linked to branch {1}").format(warehouse_id, doc.name),
        alert=True,
    )