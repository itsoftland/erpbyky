import frappe
from frappe.utils import flt

SE_TABLE = "custom_finished_item_storage"             # Stock Entry table
ITEM_TABLE = "custom_finished_item_storage_location"  # Item table


def _qty_field(doctype):
    meta = frappe.get_meta(doctype)
    for name in ("qty", "quantity", "qunatity", "stock_qty"):
        if meta.has_field(name):
            return name
    for f in meta.fields:
        if f.fieldtype in ("Int", "Float") and not f.hidden:
            return f.fieldname
    frappe.throw(f"No quantity field found in {doctype}")

def _collect(doc):
    """Totals per (item, warehouse) from the Stock Entry rows.
    Only items with custom_finished_good = 1 are counted."""
    qty_f = _qty_field(frappe.get_meta("Stock Entry").get_field(SE_TABLE).options)
    totals = {}

    for r in doc.get(SE_TABLE) or []:
        item, warehouse = r.get("item"), r.get("warehouse")
        if not item or not warehouse:
            continue
        if not frappe.db.get_value("Item", item, "custom_finished_good"):
            continue

        d = totals.setdefault((item, warehouse), {"qty": 0, "uom": r.get("uom")})
        d["qty"] += flt(r.get(qty_f))

    return totals


def _apply(doc, sign):
    child_dt = frappe.get_meta("Item").get_field(ITEM_TABLE).options
    meta = frappe.get_meta(child_dt)
    qty_f = _qty_field(child_dt)

    for (item, warehouse), d in _collect(doc).items():
        filters = {
            "parent": item,
            "parenttype": "Item",
            "parentfield": ITEM_TABLE,
            "warehouse": warehouse,
        }
        existing = frappe.db.get_value(child_dt, filters, ["name", qty_f], as_dict=True)

        if existing:
            new_qty = flt(existing[qty_f]) + sign * d["qty"]
            if new_qty > 0:
                frappe.db.set_value(child_dt, existing.name, qty_f, new_qty)
            else:
                frappe.delete_doc(child_dt, existing.name, force=True, ignore_permissions=True)

        elif sign > 0:
            row = {
                "doctype": child_dt,
                "parent": item,
                "parenttype": "Item",
                "parentfield": ITEM_TABLE,
                "idx": frappe.db.count(child_dt, {
                    "parent": item, "parenttype": "Item", "parentfield": ITEM_TABLE
                }) + 1,
                "warehouse": warehouse,
                qty_f: d["qty"],
            }
            if meta.has_field("item"):
                row["item"] = item
            if meta.has_field("uom"):
                row["uom"] = d["uom"]

            frappe.get_doc(row).insert(ignore_permissions=True)

        frappe.clear_document_cache("Item", item)


def on_submit(doc, method=None):
    _apply(doc, 1)


def on_cancel(doc, method=None):
    _apply(doc, -1)