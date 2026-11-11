"""
byky_app/box_unpacking.py
(Item storage-location updates on submit/cancel are done by storage_location.update_item_storage_locations
 and reverse_item_storage_locations. This file only prepares rates and validates.)

Box Unpacking = Stock Entry with Stock Entry Type "Box Unpacking" (Purpose: Repack)

  * Items table      : 1 row for the box (source warehouse)  +  1 row per inside item (target warehouse)
  * custom_storage_location (Stock Entry child table):
        rows for the BOX item   -> racks the box is taken from   (quantity is DEDUCTED on the Item)
        rows for any OTHER item -> racks the inside items go to  (quantity is ADDED on the Item)

!! If your field names differ, only change the constants below.
"""
import frappe
from frappe import _
from frappe.utils import flt

UNPACK_TYPE = "Box Unpacking"

# ---- Item doctype ----------------------------------------------------------
ITEM_STORAGE_TABLE = "custom_storage_location"   # Item > Storage Location table
ITEM_BOX_TABLE = "custom_box_items"              # Item > Inside Items table
BOX_CHILD_ITEM = "item"                          # field in Inside Items row
BOX_CHILD_QTY = "quantity"                       # field in Inside Items row

# ---- Storage rows (same names on the Item child table and Stock Entry child table)
SE_STORAGE_TABLE = "custom_storage_location"     # Stock Entry > storage table
ROW_ITEM = "item"
ROW_QTY = "quantity"
LOC_KEYS = ("warehouse", "floor", "area", "shelf", "rack")


def _is_unpack(doc):
    return doc.get("stock_entry_type") == UNPACK_TYPE


def _add(d, key, val):
    d[key] = d.get(key, 0) + flt(val)


def _expected_inside(box, box_qty):
    expected = {}
    for r in box.get(ITEM_BOX_TABLE) or []:
        _add(expected, r.get(BOX_CHILD_ITEM), flt(r.get(BOX_CHILD_QTY)) * box_qty)
    return expected


# ---------------------------------------------------------------------------
# 1) before_validate : split the box value across the inside items
# ---------------------------------------------------------------------------
def _rate(item_code, warehouse):
    rate = 0
    if warehouse:
        rate = frappe.db.get_value("Bin", {"item_code": item_code, "warehouse": warehouse}, "valuation_rate")
    if not flt(rate):
        rate = frappe.db.get_value("Item", item_code, "valuation_rate") or \
               frappe.db.get_value("Item", item_code, "last_purchase_rate")
    return flt(rate)


def allocate_rates(doc, method=None):
    if not _is_unpack(doc) or doc.docstatus != 0 or not doc.custom_box_item:
        return

    box_row = next((r for r in doc.items if r.item_code == doc.custom_box_item), None)
    inside = [r for r in doc.items if r.item_code != doc.custom_box_item]
    if not box_row or not inside:
        return

    total_value = _rate(box_row.item_code, box_row.s_warehouse) * flt(box_row.qty)
    weights = [_rate(r.item_code, r.t_warehouse) * flt(r.qty) for r in inside]
    total_w = sum(weights)
    total_q = sum(flt(r.qty) for r in inside)

    for r, w in zip(inside, weights):
        if not flt(r.qty):
            continue
        if total_w:
            share = total_value * w / total_w
        elif total_q:
            share = total_value * flt(r.qty) / total_q
        else:
            share = 0
        r.set_basic_rate_manually = 1
        r.basic_rate = share / flt(r.qty)
        if not flt(r.basic_rate):
            r.allow_zero_valuation_rate = 1


# ---------------------------------------------------------------------------
# 2) validate
# ---------------------------------------------------------------------------
def validate_box_unpacking(doc, method=None):
    if not _is_unpack(doc):
        return
    if not doc.custom_box_item:
        frappe.throw(_("Select the Box Item"))

    box_qty = flt(doc.custom_box_qty) or 1
    box = frappe.get_doc("Item", doc.custom_box_item)
    expected = _expected_inside(box, box_qty)
    if not expected:
        frappe.throw(_("{0} has no Inside Items").format(box.name))

    # ---- stock rows
    box_rows = [r for r in doc.items if r.item_code == box.name]
    if len(box_rows) != 1 or flt(box_rows[0].qty) != box_qty:
        frappe.throw(_("Items table must have exactly one row for {0} with Qty {1}").format(box.name, box_qty))
    if not box_rows[0].s_warehouse or box_rows[0].t_warehouse:
        frappe.throw(_("The box row needs a Source Warehouse and no Target Warehouse"))

    got, inside_wh = {}, {}
    for r in doc.items:
        if r.item_code == box.name:
            continue
        if not r.t_warehouse or r.s_warehouse:
            frappe.throw(_("Row {0}: inside items need a Target Warehouse and no Source Warehouse").format(r.idx))
        _add(got, r.item_code, r.qty)
        inside_wh.setdefault(r.item_code, set()).add(r.t_warehouse)

    for item, qty in expected.items():
        if flt(got.get(item)) != qty:
            frappe.throw(_("{0}: Items table has {1}, box contents require {2}").format(item, flt(got.get(item)), qty))
    for item in got:
        if item not in expected:
            frappe.throw(_("{0} is not inside {1}").format(item, box.name))

    # ---- storage rows
    loc = {}
    for s in doc.get(SE_STORAGE_TABLE) or []:
        _add(loc, s.get(ROW_ITEM), s.get(ROW_QTY))
        if s.get(ROW_ITEM) == box.name:
            if s.get("warehouse") != box_rows[0].s_warehouse:
                frappe.throw(_("Storage row for the box must be in warehouse {0}").format(box_rows[0].s_warehouse))
        elif s.get(ROW_ITEM) in expected:
            if s.get("warehouse") not in inside_wh.get(s.get(ROW_ITEM), set()):
                frappe.throw(_("{0}: storage warehouse {1} does not match the Items table target warehouse")
                             .format(s.get(ROW_ITEM), s.get("warehouse")))
        else:
            frappe.throw(_("Storage row item {0} does not belong to this unpacking").format(s.get(ROW_ITEM)))

    if flt(loc.get(box.name)) != box_qty:
        frappe.throw(_("Storage rows for the box must total {0} (now {1})").format(box_qty, flt(loc.get(box.name))))
    for item, qty in expected.items():
        if flt(loc.get(item)) != qty:
            frappe.throw(_("Storage rows for {0} must total {1} (now {2})").format(item, qty, flt(loc.get(item))))