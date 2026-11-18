import frappe
from frappe.utils import flt

# order of the levels (same names on the Work Order row and on the Item storage row)
LOCATION_FIELDS = ["warehouse", "floor", "area", "shelf", "rack"]
ITEM_STORAGE_TABLE = "custom_storage_location"      # child table on Item
ITEM_QTY_FIELDS = ("quantity", "qty")               # quantity field of that child table


def _qty_fieldname():
    """Quantity field of the Item's Storage Location row, found by name or by label."""
    table_df = frappe.get_meta("Item").get_field(ITEM_STORAGE_TABLE)
    if not table_df:
        return None
    meta = frappe.get_meta(table_df.options)
    for name in ITEM_QTY_FIELDS:
        if meta.has_field(name):
            return name
    for f in meta.fields:
        if (f.label or "").strip().lower() in ("quantity", "qty"):
            return f.fieldname
    return None


@frappe.whitelist()
@frappe.validate_and_sanitize_search_inputs
def item_location_query(doctype, txt, searchfield, start, page_len, filters):
    """Link search for Warehouse, Floor, Area, Shelf and Rack: only the values the
    item has in its Storage Location table. The levels before it must match what
    is already chosen on the row (warehouse -> floor -> area -> shelf -> rack)."""
    filters = frappe._dict(filters or {})
    item = filters.get("item")
    field = filters.get("field")

    if not item or field not in LOCATION_FIELDS:
        return []

    earlier = LOCATION_FIELDS[:LOCATION_FIELDS.index(field)]
    rows = frappe.get_cached_doc("Item", item).get(ITEM_STORAGE_TABLE) or []

    values = []
    for r in rows:
        if any(filters.get(f) and r.get(f) != filters.get(f) for f in earlier):
            continue
        value = r.get(field)
        if value and value not in values and (not txt or txt.lower() in str(value).lower()):
            values.append(value)

    start, page_len = int(start or 0), int(page_len or 20)
    return [[v] for v in values[start:start + page_len]]


@frappe.whitelist()
def get_location_quantity(item, location=None):
    """Quantity saved in the item's Storage Location rows that match the chosen
    location levels, e.g. {"warehouse": "...", "floor": "..."}."""
    frappe.has_permission("Item", "read", throw=True)

    chosen = {k: v for k, v in (frappe.parse_json(location) or {}).items()
              if k in LOCATION_FIELDS and v}
    qty_field = _qty_fieldname()
    rows = frappe.get_cached_doc("Item", item).get(ITEM_STORAGE_TABLE) or []

    matches = [r for r in rows if all(r.get(f) == v for f, v in chosen.items())]
    return {
        "rows": len(matches),
        "has_quantity_field": bool(qty_field),
        "quantity": sum(flt(r.get(qty_field)) for r in matches) if qty_field else None,
    }