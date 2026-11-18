import frappe
from erpnext.accounts.doctype.purchase_invoice.purchase_invoice import (
    make_purchase_receipt as erpnext_make_purchase_receipt,
)

# ---- Confirm these fieldnames in Customize Form / DocType ----
BOX_TABLE = "custom_box_entry"        # Purchase Invoice: "Box Entry" table
BOX_TABLE_ITEM = "item"               # Box Entry row: Item
BOX_TABLE_REQUIRED = "box_required"   # Box Entry row: Box Required
ITEM_BOX_NAME = "custom_box_name"     # Purchase Invoice Item: "Box Name"
NEEDED = "Needed"
# --------------------------------------------------------------


@frappe.whitelist()
def make_purchase_receipt(source_name, target_doc=None, args=None):
    pr = erpnext_make_purchase_receipt(source_name, target_doc)
    pi = frappe.get_doc("Purchase Invoice", source_name)

    # Items whose box is Needed
    needed = {
        row.get(BOX_TABLE_ITEM)
        for row in pi.get(BOX_TABLE) or []
        if row.get(BOX_TABLE_REQUIRED) == NEEDED
    }

    # Match each mapped Purchase Receipt row to its source Purchase Invoice row
    unused = list(pi.items)
    for pr_row in pr.items:
        src = next(
            (r for r in unused if r.item_code == pr_row.item_code and r.qty == pr_row.qty),
            None,
        )
        if not src:
            continue
        unused.remove(src)

        box_name = src.get(ITEM_BOX_NAME)
        if src.item_code not in needed or not box_name:
            continue  # Not Needed: normal process, keep the row as it is

        if not frappe.db.exists("Item", box_name):
            frappe.throw(f"Box item {box_name} does not exist. Create the Box Entry first.")

        # Needed: receive the box item instead of the vehicle item
        pr_row.item_code = box_name
        pr_row.item_name = box_name
        pr_row.description = box_name
        pr_row.uom = pr_row.stock_uom = "Box"
        pr_row.conversion_factor = 1
        pr_row.rate = pr_row.price_list_rate = 0

        # Unlink from the Purchase Order row, otherwise ERPNext checks item/UOM against the PO
        pr_row.purchase_order = None
        pr_row.purchase_order_item = None

    return pr