"""
byky_app/branch_warehouse.py

Creates a Warehouse (named after the Branch) when a Branch is created, and links
the two through the custom fields:
    Branch.custom_warehouse   -> Warehouse
    Warehouse.custom_branch   -> Branch

Hook it up in byky_app/hooks.py:

    doc_events = {
        "Branch": {
            "after_insert": "byky_app.branch_warehouse.create_warehouse_for_branch",
        },
        # ... keep your existing "Stock Entry" events here
    }

Then run:
    bench --site byky migrate
    bench restart
"""

import frappe
from frappe import _

# Optional: set to your real parent group, e.g. "All Warehouses - B".
# Leave as None to let ERPNext use the company's default root.
PARENT_WAREHOUSE = None


def create_warehouse_for_branch(doc, method=None):
    warehouse_name = (doc.branch or "").strip()
    if not warehouse_name:
        return

    company = doc.get("company") or frappe.db.get_single_value(
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