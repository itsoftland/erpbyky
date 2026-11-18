import frappe


def sync_item_group(doc, method=None):
    name = doc.category

    if frappe.db.exists("Item Group", name):
        ig = frappe.get_doc("Item Group", name)
        is_new = False
    else:
        # Category was renamed: find the group by its code and rename it
        old = frappe.db.get_value(
            "Item Group", {"custom_item_group_code": doc.category_code}
        )
        if old:
            frappe.rename_doc("Item Group", old, name, force=True)
            ig = frappe.get_doc("Item Group", name)
            is_new = False
        else:
            ig = frappe.new_doc("Item Group")
            ig.parent_item_group = "All Item Groups"
            is_new = True

    ig.item_group_name = name
    ig.custom_item_group_code = doc.category_code
    ig.custom_brand = doc.brand
    ig.custom_description = doc.get("description")  # no description field on Category yet

    if is_new:
        ig.insert(ignore_permissions=True)
    else:
        ig.save(ignore_permissions=True)

    frappe.msgprint(
        f"Item Group {name} {'created' if is_new else 'updated'}.", alert=True
    )