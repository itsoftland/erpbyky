
import frappe


def create_branch_warehouse(doc, method=None):
    """
    Automatically create a Warehouse for every new Branch
    and link it to the Branch document.
    """

    # --------------------------------------------------
    # 1. Check existing Warehouse link
    # --------------------------------------------------

    if doc.get("warehouse"):
        return

    # --------------------------------------------------
    # 2. Get Branch Name
    # --------------------------------------------------

    branch_name = doc.get("branch")

    if not branch_name:
        frappe.throw("Branch name is required.")

    # --------------------------------------------------
    # 3. Get Company
    # --------------------------------------------------

    company = (
        doc.get("company")
        or frappe.defaults.get_global_default("company")
    )

    if not company:
        frappe.throw("Please select a Company.")

       # --------------------------------------------------
    # 4. Find (or create) BYKY Root Warehouse
    # --------------------------------------------------

    root_warehouse = frappe.db.get_value(
        "Warehouse",
        {
            "warehouse_name": "Byky Warehouse",
            "company": company,
            "is_group": 1
        },
        "name"
    )

    if not root_warehouse:
        root_doc = frappe.get_doc({
            "doctype": "Warehouse",
            "warehouse_name": "Byky Warehouse",
            "company": company,
            "is_group": 1
        })
        root_doc.insert(ignore_permissions=True)
        root_warehouse = root_doc.name

    # --------------------------------------------------
    # 5. Check Existing Warehouse
    # --------------------------------------------------

    existing_warehouse = frappe.db.get_value(
        "Warehouse",
        {
            "warehouse_name": branch_name,
            "company": company
        },
        "name"
    )

    if existing_warehouse:

        warehouse_name = existing_warehouse

    else:

        # --------------------------------------------------
        # 6. Create New Warehouse
        # --------------------------------------------------

        warehouse_data = {
            "doctype": "Warehouse",
            "warehouse_name": branch_name,
            "company": company,
            "parent_warehouse": root_warehouse,
            "is_group": 0
        }

        # Link Branch if Warehouse has a Branch field
        if frappe.get_meta("Warehouse").has_field("branch"):
            warehouse_data["branch"] = doc.name

        warehouse_doc = frappe.get_doc(warehouse_data)

        warehouse_doc.insert(ignore_permissions=True)

        warehouse_name = warehouse_doc.name

    # --------------------------------------------------
    # 7. Link Warehouse to Branch
    # --------------------------------------------------

    doc.db_set(
        "warehouse",
        warehouse_name,
        update_modified=False
    )

    # --------------------------------------------------
    # 8. Success Message
    # --------------------------------------------------

    frappe.msgprint(
        msg=f"Warehouse <b>{warehouse_name}</b> successfully linked to Branch <b>{branch_name}</b>.",
        title="Warehouse Created",
        indicator="green"
    )

    frappe.logger().info(
        f"Branch Warehouse Created: {branch_name} -> {warehouse_name}"
    )
