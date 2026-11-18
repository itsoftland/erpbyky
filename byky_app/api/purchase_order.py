# import frappe
# from frappe import _
# from frappe.utils import flt, nowdate
# from erpnext.accounts.doctype.tax_rule.tax_rule import get_tax_template

# # Used only when no tax template is found (fallback).
# # Change it to the exact name of an account in your Chart of Accounts.
# TAX_ACCOUNT = "Import Duty - B"


# @frappe.whitelist()
# def get_supplier_tax(supplier, posting_date=None):
#     """Return the tax template and rate for a supplier, using ERPNext Tax Rules."""
#     supplier_doc = frappe.get_cached_doc("Supplier", supplier)
#     company = (
#         frappe.defaults.get_user_default("Company")
#         or frappe.db.get_single_value("Global Defaults", "default_company")
#     )

#     template = get_tax_template(
#         posting_date or nowdate(),
#         {
#             "tax_type": "Purchase",
#             "supplier": supplier,
#             "supplier_group": supplier_doc.supplier_group,
#             "billing_country": supplier_doc.country,
#             "company": company,
#         },
#     )

#     rate = 0
#     if template:
#         rows = frappe.get_all(
#             "Purchase Taxes and Charges",
#             filters={
#                 "parenttype": "Purchase Taxes and Charges Template",
#                 "parent": template,
#             },
#             fields=["rate"],
#             order_by="idx asc",
#             limit=1,
#         )
#         rate = flt(rows[0].rate) if rows else 0

#     return {"template": template, "rate": rate}


# @frappe.whitelist()
# def create_purchase_order(data):
#     data = frappe.parse_json(data)

#     if not data.get("items"):
#         frappe.throw(_("Please add at least one item."))

#     po = frappe.new_doc("Purchase Order")
#     po.update({
#         "supplier": data["supplier"],
#         "transaction_date": data["transaction_date"],
#         "schedule_date": data["schedule_date"],
#         "set_warehouse": data["set_warehouse"],
#         "shipping_address": data.get("shipping_address"),
#         "custom_supplier_country_tax_percentage": flt(data.get("tax_pct")),
#     })

#     for r in data["items"]:
#         po.append("items", {
#             "item_code": r["item_code"],
#             "qty": flt(r["qty"]),
#             "rate": flt(r["rate"]),
#             "schedule_date": data["schedule_date"],
#         })

#     po.set_missing_values()

#     # Set after set_missing_values so supplier defaults don't override them
#     po.currency = data["currency"]
#     po.conversion_rate = flt(data["conversion_rate"])

#     # Clear any taxes that set_missing_values may have added, then apply ours
#     po.set("taxes", [])

#     template = data.get("tax_template")
#     if template:
#         po.taxes_and_charges = template
#         template_doc = frappe.get_doc("Purchase Taxes and Charges Template", template)
#         for t in template_doc.taxes:
#             po.append("taxes", {
#                 "category": t.category,
#                 "add_deduct_tax": t.add_deduct_tax,
#                 "charge_type": t.charge_type,
#                 "account_head": t.account_head,
#                 "description": t.description,
#                 "rate": t.rate,
#                 "row_id": t.row_id,
#                 "tax_amount": t.tax_amount,
#                 "cost_center": t.cost_center,
#                 "included_in_print_rate": t.included_in_print_rate,
#             })
#     elif flt(data.get("tax_pct")):
#         po.append("taxes", {
#             "charge_type": "On Net Total",
#             "account_head": TAX_ACCOUNT,
#             "description": "Supplier Country Tax",
#             "rate": flt(data["tax_pct"]),
#         })

#     po.insert()  # saved as draft; normal user permissions apply
#     return po.name