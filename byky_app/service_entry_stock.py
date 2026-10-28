import frappe
from frappe import _
from frappe.utils import flt

# ------------------------------------------------------------------
# CHANGE THESE TO YOUR REAL FIELDNAMES (Customize Form / DocType)
# ------------------------------------------------------------------
SE = {                                       # fields on Service Entry
    "flag": "is_spare_parts_used",           # checkbox "Is Spare Parts Used"
    "used_table": "serviced_items",          # table "Serviced Items"  (parts taken from stock)
    "damaged_table": "damaged_items",        # table "Damaged Items"   (old parts going to scrap)
    "scrap_warehouse": "scrap_warehouse",    # field "Scrap Warehouse"
    "company": "company",
    "issue_link": "stock_entry",             # Link to Stock Entry: the parts issue
    "scrap_link": "scrap_stock_entry",       # Link to Stock Entry: the scrap receipt (add this field)
}
USED = {                                     # fields in the Serviced Items table
    "item": "item",
    "uom": "uom",
    "qty": "quantity",
    "warehouse": "warehouse",
    "floor": "floor",
    "area": "area",
    "shelf": "shelf",
    "rack": "rack",
}
DAMAGED = {                                  # fields in the Damaged Items table
    "item": "damaged_item",
    "uom": "uom",
    "qty": "quantity",
}
STORAGE_TABLE = "custom_storage_location"        # Storage Location table on Stock Entry
WAREHOUSE_FLAG = "custom_is_warehouse_storage_"   # "Is Warehouse storage" checkbox on Stock Entry
REQUIRE_SHELF = True                         # every used part must say which shelf it came from
        

def process_service_stock(doc, method=None):
    """Service Entry on_submit: issue the used parts, receive the damaged parts into scrap."""
    if not doc.get(SE["flag"]):
        return

    used = doc.get(SE["used_table"]) or []
    damaged = doc.get(SE["damaged_table"]) or []

    if not used and not damaged:
        frappe.throw(_("Add the Serviced Items or Damaged Items, or untick 'Is Spare Parts Used'."))

    _validate_used(used)
    _validate_damaged(doc, damaged)
    company = _company(doc, used)

    # 1. parts used -> Material Issue from the warehouse and shelf they were taken from
    if used and not doc.get(SE["issue_link"]):
        name = _post_issue(doc, used, company)
        doc.db_set(SE["issue_link"], name, update_modified=False)

    # 2. damaged parts -> Material Receipt into the scrap warehouse
    if damaged and not doc.get(SE["scrap_link"]):
        name = _post_scrap(doc, damaged, company)
        doc.db_set(SE["scrap_link"], name, update_modified=False)


def reverse_service_stock(doc, method=None):
    """Service Entry on_cancel: cancel the Stock Entries it created (scrap first)."""
    for key in ("scrap_link", "issue_link"):
        name = doc.get(SE[key])
        if name and frappe.db.get_value("Stock Entry", name, "docstatus") == 1:
            se = frappe.get_doc("Stock Entry", name)
            se.flags.ignore_permissions = True
            se.cancel()


# ------------------------------------------------------------------
def _post_issue(doc, rows, company):
    se = _new_stock_entry(doc, "Material Issue", company)

    if se.meta.has_field(WAREHOUSE_FLAG):
        se.set(WAREHOUSE_FLAG, 0)            # storage location based entry

    for r in rows:
        item = r.get(USED["item"])
        qty = flt(r.get(USED["qty"]))
        warehouse = r.get(USED["warehouse"])

        se.append("items", {"item_code": item, "qty": qty, "s_warehouse": warehouse})

        # the shelf the part was taken from: the storage location hooks use this row
        if se.meta.has_field(STORAGE_TABLE):
            se.append(STORAGE_TABLE, _location_row(se, r, item, warehouse, qty))

    return _post(se)


def _post_scrap(doc, rows, company):
    warehouse = doc.get(SE["scrap_warehouse"])
    se = _new_stock_entry(doc, "Material Receipt", company)

    if se.meta.has_field(WAREHOUSE_FLAG):
        se.set(WAREHOUSE_FLAG, 1)            # plain warehouse entry, no shelf needed for scrap

    for r in rows:
        se.append("items", {
            "item_code": r.get(DAMAGED["item"]),
            "qty": flt(r.get(DAMAGED["qty"])),
            "t_warehouse": warehouse,
            "basic_rate": 0,
            "allow_zero_valuation_rate": 1,  # scrap is valued at zero: confirm with accounts
        })

    return _post(se)


def _location_row(se, row, item, warehouse, qty):
    """One Storage Location row for the Stock Entry. Only fields that exist are filled."""
    child_dt = se.meta.get_field(STORAGE_TABLE).options
    existing = {f.fieldname for f in frappe.get_meta(child_dt).fields}

    values = {
        "item": item,
        "warehouse": warehouse,
        "floor": row.get(USED["floor"]),
        "area": row.get(USED["area"]),
        "shelf": row.get(USED["shelf"]),
        "rack": row.get(USED["rack"]),
        "uom": frappe.db.get_value("Item", item, "stock_uom"),
        "quantity": qty,
    }
    return {k: v for k, v in values.items() if k in existing and v not in (None, "")}


def _validate_used(rows):
    for r in rows:
        if not r.get(USED["item"]) or flt(r.get(USED["qty"])) <= 0:
            frappe.throw(_("Row {0} (Serviced Items): item and a positive quantity are required.").format(r.idx))
        if not r.get(USED["warehouse"]):
            frappe.throw(_("Row {0} (Serviced Items): warehouse is required.").format(r.idx))

        # the quantity is booked in the item's stock UOM
        uom = r.get(USED["uom"])
        stock_uom = frappe.db.get_value("Item", r.get(USED["item"]), "stock_uom")
        if uom and stock_uom and uom != stock_uom:
            frappe.throw(
                _("Row {0} (Serviced Items): UOM must be the stock UOM of {1} ({2}).").format(
                    r.idx, r.get(USED["item"]), stock_uom
                )
            )
        if REQUIRE_SHELF and not r.get(USED["shelf"]):
            frappe.throw(_("Row {0} (Serviced Items): choose the floor, area and shelf the part was taken from.").format(r.idx))


def _validate_damaged(doc, rows):
    if rows and not doc.get(SE["scrap_warehouse"]):
        frappe.throw(_("Select the Scrap Warehouse for the Damaged Items."))

    for r in rows:
        item = r.get(DAMAGED["item"])
        if not item or flt(r.get(DAMAGED["qty"])) <= 0:
            frappe.throw(_("Row {0} (Damaged Items): item and a positive quantity are required.").format(r.idx))

        # the quantity is booked in the item's stock UOM
        uom = r.get(DAMAGED["uom"])
        stock_uom = frappe.db.get_value("Item", item, "stock_uom")
        if uom and stock_uom and uom != stock_uom:
            frappe.throw(
                _("Row {0} (Damaged Items): UOM must be the stock UOM of {1} ({2}).").format(r.idx, item, stock_uom)
            )


def _company(doc, used_rows):
    if SE["company"] and doc.get(SE["company"]):
        return doc.get(SE["company"])
    warehouse = used_rows[0].get(USED["warehouse"]) if used_rows else doc.get(SE["scrap_warehouse"])
    return frappe.db.get_value("Warehouse", warehouse, "company")


def _new_stock_entry(doc, entry_type, company):
    se = frappe.new_doc("Stock Entry")
    se.stock_entry_type = entry_type
    se.purpose = entry_type
    se.company = company
    se.remarks = _("Auto-created from {0} {1}").format(doc.doctype, doc.name)
    return se


def _post(se):
    se.flags.ignore_permissions = True
    se.insert()
    se.submit()
    return se.name