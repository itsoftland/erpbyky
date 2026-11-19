import frappe


def create_branch_warehouse(doc, method=None):
    """Automatically create and link a Warehouse when a Branch is created.

    Hook it on Branch -> after_insert, BEFORE byky_app.rms_sync.on_insert, so
    the Branch message sent to RMS already contains the linked warehouse.
    """

    # Stop if Warehouse is already linked
    if doc.get("warehouse"):
        return

    # Get Branch name
    branch_name = doc.get("branch") or doc.name

    if not branch_name:
        frappe.throw("Branch name is required.")

    # Get Company
    company = (
        doc.get("company")
        or frappe.defaults.get_global_default("company")
    )

    if not company:
        frappe.throw("Please configure a Default Company.")

    # Check whether Warehouse already exists
    existing = frappe.db.get_value(
        "Warehouse",
        {
            "warehouse_name": branch_name,
            "company": company,
        },
        "name",
    )

    if existing:
        warehouse_name = existing

    else:
        # Find the Company's root Warehouse
        root = frappe.db.sql(
            """
            SELECT name
            FROM `tabWarehouse`
            WHERE company = %s
              AND is_group = 1
              AND (parent_warehouse IS NULL
                   OR parent_warehouse = '')
            ORDER BY lft
            LIMIT 1
            """,
            (company,),
            as_dict=True,
        )

        if not root:
            frappe.throw(
                f"No root Warehouse found for Company {company}. "
                "Please create the Company's root Warehouse first."
            )

        warehouse_data = {
            "doctype": "Warehouse",
            "warehouse_name": branch_name,
            "company": company,
            "parent_warehouse": root[0].name,
            "is_group": 0,
        }

        # Set Branch link if Warehouse has this field
        if frappe.get_meta("Warehouse").has_field("branch"):
            warehouse_data["branch"] = doc.name

        # Create Warehouse
        warehouse = frappe.get_doc(warehouse_data)
        warehouse.insert(ignore_permissions=True)

        warehouse_name = warehouse.name

    # Link Warehouse to Branch
    doc.db_set(
        "warehouse",
        warehouse_name,
        update_modified=False,
    )

    frappe.logger().info(
        f"Warehouse {warehouse_name} linked to Branch {doc.name}"
    )