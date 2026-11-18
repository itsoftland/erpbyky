import json

import frappe
from frappe import _
from frappe.utils import cint, flt

from byky_app.finished_good import _pick

# Purchase Invoice
ZERO_ITEM_ROW_WITH_BOXES = True          # the identify row is zero value, the boxes carry the payment
BOX_RATE_MODE = "equal"                  # "equal" = rate split over the boxes, "first_box" = all on the first box
REQUIRE_BOX_NAME = True                  # every box row needs its Box Name
BOX_ENTRY_TABLE = "custom_box_entry"     # Box Entry table on Purchase Invoice
BOX_ENTRY_ITEM = "item"                  # Item field of that table
BOX_ENTRY_COUNT = "box_count"            # Box Count field of that table
BOX_ENTRY_REQUIRED = "box_required"      # Box Required field of that table
NEEDED = "Needed"                        # value of Box Required when boxes are needed
IS_BOX_FIELD = "custom_is_box"           # item row check: this row is a box row

TAX_FIELD = "custom_tax_amount"
NET_WITH_TAX_FIELD = "custom_net_amount_with_tax"


def _box_name_field():
    """The Box Name cell of the item row is the field that links to Box Entry."""
    for f in frappe.get_meta("Purchase Invoice Item").fields:
        if f.fieldtype == "Link" and f.options == "Box Entry":
            return f.fieldname
    return "custom_box_name"


# ---------------------------------------------------------------------------
# Purchase Order + Purchase Invoice: per-row tax (validate)
# ---------------------------------------------------------------------------
def set_row_tax(doc, method=None):
    """Split each tax line's amount to the item rows and store it in the
    Tax Amount / Net Amount (With Tax) fields of the row."""

    tax_by_key = {}
    for tax in doc.get("taxes") or []:
        try:
            detail = json.loads(tax.item_wise_tax_detail or "{}")
        except ValueError:
            detail = {}
        for key, val in detail.items():
            amount = flt(val[1]) if isinstance(val, (list, tuple)) and len(val) > 1 else 0
            tax_by_key[key] = tax_by_key.get(key, 0) + amount

    # same item on several rows (identify row + box rows): share the tax by net amount
    rows_by_key = {}
    for row in doc.items:
        key = row.item_code or row.item_name
        rows_by_key.setdefault(key, []).append(row)

    for key, rows in rows_by_key.items():
        total_tax = tax_by_key.get(key, 0)
        total_net = sum(flt(r.net_amount or r.amount) for r in rows)

        for r in rows:
            base = flt(r.net_amount or r.amount)
            share = (base / total_net) if total_net else 0
            tax_amount = flt(total_tax * share, r.precision("amount"))

            r.set(TAX_FIELD, tax_amount)
            r.set(NET_WITH_TAX_FIELD, flt(base + tax_amount, r.precision("amount")))


# ---------------------------------------------------------------------------
# Purchase Invoice: identify row + box rows (before_validate)
# ---------------------------------------------------------------------------
def _split_rate(total, n, precision):
    total = flt(total)
    if not n:
        return []
    if BOX_RATE_MODE == "first_box":
        return [total] + [0] * (n - 1)
    base = flt(total / n, precision)
    rates = [base] * n
    rates[-1] = flt(total - base * (n - 1), precision)   # the last box takes the rounding
    return rates


def process_box_rows(doc, method=None):
    """Box Required = Not Needed: normal rows, Box Of and Box Name empty.
    Box Required = Needed: only the box rows, one per Box Count, each with
    Box Of = the item, a Box Name and the item quantity."""
    name_field = _box_name_field()

    specs = {}
    for e in doc.get(BOX_ENTRY_TABLE) or []:
        code = e.get(BOX_ENTRY_ITEM)
        if code:
            specs[code] = {
                "needed": (e.get(BOX_ENTRY_REQUIRED) or NEEDED) == NEEDED,
                "count": cint(e.get(BOX_ENTRY_COUNT)),
            }

    groups = {}
    for r in doc.items:
        if r.item_code:
            groups.setdefault(r.item_code, []).append(r)

    for code, rows in groups.items():
        spec = specs.get(code)

        # ---- normal purchase invoice entry ---------------------------------
        if not spec or not spec["needed"]:
            for r in rows:
                r.custom_box_of = None
                r.set(name_field, None)
            continue

        # ---- boxes needed: only the box rows -------------------------------
        n = spec["count"]
        if n < 1:
            frappe.throw(
                _("{0}: Box Required is Needed, enter the Box Count (or set Box Required to Not Needed)").format(code)
            )

        if len(rows) != n:
            frappe.throw(
                _("{0}: Box Count is {1}, so the Items table needs {1} rows for it, but it has {2}").format(
                    code, n, len(rows))
            )

        for r in rows:
            r.custom_box_of = code
            if REQUIRE_BOX_NAME and not (r.get(name_field) or "").strip():
                frappe.throw(_("Row {0}: enter the Box Name").format(r.idx))

        # the boxes together must bill what the Purchase Order says
        first = rows[0]
        if first.get("po_detail"):
            po_rate = frappe.db.get_value("Purchase Order Item", first.po_detail, "rate")
            expected = flt(po_rate) * flt(first.qty)
            actual = sum(flt(r.rate) * flt(r.qty) for r in rows)
            if abs(expected - actual) > 0.05:
                frappe.msgprint(
                    _("{0}: the box rows add up to {1}, the Purchase Order says {2}").format(
                        code, flt(actual, 2), flt(expected, 2)),
                    indicator="orange", alert=True,
                )


# ---------------------------------------------------------------------------
# Purchase Invoice: Supplier and Vehicle of the Box Entry (validate)
# ---------------------------------------------------------------------------
def fill_box_entries(doc, method=None):
    """Every Box Entry used in the Box Name cells gets the invoice supplier and
    the item of its row, when those fields are still empty."""
    name_field = _box_name_field()

    for r in doc.items:
        box = (r.get(name_field) or "").strip()
        if not box or not frappe.db.exists("Box Entry", box):
            continue

        current = frappe.db.get_value("Box Entry", box, ["supplier", "vehicle"], as_dict=True) or {}

        values = {}
        if doc.supplier and not current.get("supplier"):
            values["supplier"] = doc.supplier
        if r.item_code and not current.get("vehicle"):
            values["vehicle"] = r.item_code

        if values:
            frappe.db.set_value("Box Entry", box, values)


# ---------------------------------------------------------------------------
# Whitelisted helper for the Item Details tables
# ---------------------------------------------------------------------------
@frappe.whitelist()
def get_item_details(item_codes):
    """HSN, country of origin and Needed Boxes (with barcode)."""
    frappe.has_permission("Item", "read", throw=True)
    out = {}

    for code in frappe.parse_json(item_codes):
        item = frappe.get_cached_doc("Item", code)
        boxes = []

        if item.get("custom_finished_good") and not item.get("custom_single_item"):
            for b in item.get("custom_needed_boxes") or []:
                box_code = _pick(b, ["item", "item_code", "box_item"])
                if not box_code:
                    continue
                box_item = frappe.get_cached_doc("Item", box_code)
                barcodes = box_item.get("barcodes") or []
                boxes.append({
                    "barcode": barcodes[0].barcode if barcodes else "",
                    "name": box_item.item_name,
                    "qty": flt(_pick(b, ["qty", "quantity"])) or 1,
                })

        out[code] = {
            "hsn": item.get("gst_hsn_code") or "",
            "country": item.get("country_of_origin") or "",
            "boxes": boxes,
        }
    return out