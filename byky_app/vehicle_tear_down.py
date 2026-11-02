import frappe
from frappe import _
from frappe.utils import flt

# ------------------------------------------------------------------
# CHANGE THESE TO YOUR REAL FIELDNAMES (Customize Form / DocType)
# ------------------------------------------------------------------
SERIAL_DOCTYPE = "Serial No"

VT = {                                       # fields on Vehicle Tear Down
    "serial": "vehicle_serial_no",
    "parts_table": "recovered_parts",
    "location_table": "storage_location",
    "scrap_warehouse": "scrap_warehouse",
    "recovery_date": "recovery_date",
    "company": "company",                    # optional: used only if it exists
    "vehicle_link": "vehicle_stock_entry",   # Link to Stock Entry: vehicle issue
    "reusable_link": "reusable_stock_entry", # Link to Stock Entry: reusable receipt
    "scrap_link": "scrap_stock_entry",       # Link to Stock Entry: scrap receipt
}
PART = {                                     # fields in the Recovered Parts table
    "item": "item_code",
    "qty": "quantity",
    "disposition": "disposition",
    "destination": "destination_warehouse",
}
LOC = {                                      # fields in the Storage Location table
    "item": "item",
    "warehouse": "warehouse",
    "floor": "floor",
    "area": "area",
    "shelf": "shelf",
    "rack": "rack",
    "uom": "uom",
    "qty": "quantity",
}
STORAGE_TABLE = "custom_storage_location"        # Storage Location table on Stock Entry
WAREHOUSE_FLAG = "custom_is_warehouse_storage_"   # "Is Warehouse storage" checkbox on Stock Entry
REQUIRE_SHELF = True                         # every reusable part must say which shelf it goes to
ISSUE_VEHICLE = True                         # post a Material Issue that removes the vehicle from stock
SPREAD_VEHICLE_VALUE = True                  # reusable parts carry the vehicle's value; scrap is zero

# Item Group of the NEW item created for each Disposition.
# The names must match your Item Group records exactly.
DISPOSITION_GROUP = {
    "reusable": "ReUse Spares",     # must match the Item Group name exactly
    "scrap": "Scrap",
}
# Suffix added to the old item code / name to make the new item
DISPOSITION_SUFFIX = {
    "reusable": "Reusable",
    "scrap": "Scrap",
}

def _check_vehicle_available(serial_no):
    """A vehicle can be torn down only if it is Active and in a warehouse."""
    status, warehouse = frappe.db.get_value(
        SERIAL_DOCTYPE, serial_no, ["status", "warehouse"]
    )

    if status != "Active" or not warehouse:
        frappe.throw(
            _("Vehicle Serial No {0} cannot be used. It must be Active and in a warehouse (status: {1}).")
            .format(serial_no, status)
        )

# ==================================================================
# FETCH VEHICLE DETAILS (used by the client script)
# ==================================================================

@frappe.whitelist()
def get_vehicle_details(serial_no):
    """Fetch Item, Brand and Item Group for a selected Serial No."""
    if not serial_no:
        frappe.throw(_("Please select a Vehicle Serial No."))

    if not frappe.db.exists(SERIAL_DOCTYPE, serial_no):
        frappe.throw(_("Serial No {0} does not exist.").format(serial_no))

    frappe.has_permission(SERIAL_DOCTYPE, doc=serial_no, throw=True)

    item_code = frappe.db.get_value(SERIAL_DOCTYPE, serial_no, "item_code")
    if not item_code:
        frappe.throw(_("No Item is linked to Serial No {0}.").format(serial_no))

    brand, item_group = frappe.db.get_value("Item", item_code, ["brand", "item_group"])

    return {"item": item_code, "brand": brand or "", "item_group": item_group or ""}


# ==================================================================
# ON SUBMIT / ON CANCEL
# ==================================================================

def process_vehicle_tear_down(doc, method=None):
    """Vehicle Tear Down on_submit:
    1. create (or find) a NEW item for every recovered part, in the group of its Disposition
    2. vehicle -> Material Issue (removes the bike from stock)
    3. reusable parts -> Material Receipt of the new items into the destination warehouse + shelf
    4. scrap parts -> Material Receipt of the new items into the scrap warehouse
    5. Serial No -> Inactive
    """
    serial_no = doc.get(VT["serial"])
    if not serial_no:
        frappe.throw(_("Please select the Vehicle Serial No."))

    if not frappe.db.exists(SERIAL_DOCTYPE, serial_no):
        frappe.throw(_("Serial No {0} does not exist.").format(serial_no))

    # the three Stock Entry link fields must exist on this doctype
    for key in ("vehicle_link", "reusable_link", "scrap_link"):
        if not doc.meta.has_field(VT[key]):
            frappe.throw(
                _("Field {0} is missing on {1}. Add it before submitting.")
                .format(VT[key], doc.doctype)
            )

    serial = frappe.get_doc(SERIAL_DOCTYPE, serial_no)

    if serial.get("status") == "Inactive":
        frappe.throw(_("Vehicle Serial No {0} is already Inactive.").format(serial_no))

    parts = doc.get(VT["parts_table"]) or []
    if not parts:
        frappe.throw(_("Please add rows to the Recovered Parts table."))

    company = _company(doc, serial)
    reusable, scrap = _split_parts(doc, parts, company)
    _validate_locations(doc, reusable)

    # 1. new items: (old item, disposition) -> new item code
    item_map = _ensure_items(reusable, scrap)

    vehicle_entry = reusable_entry = scrap_entry = None
    vehicle_value = 0

    # 2. the vehicle leaves stock
    if ISSUE_VEHICLE and not doc.get(VT["vehicle_link"]):
        vehicle_entry, vehicle_value = _post_vehicle_issue(doc, serial, company)
        doc.db_set(VT["vehicle_link"], vehicle_entry, update_modified=False)

    # 3. reusable parts -> destination warehouse and shelf
    if reusable and not doc.get(VT["reusable_link"]):
        rate = 0
        total_qty = sum(flt(r.get(PART["qty"])) for r in reusable)
        if SPREAD_VEHICLE_VALUE and vehicle_value > 0 and total_qty > 0:
            rate = flt(vehicle_value) / total_qty

        reusable_entry = _post_reusable(doc, reusable, company, rate, item_map)
        doc.db_set(VT["reusable_link"], reusable_entry, update_modified=False)

    # 4. scrap parts -> scrap warehouse
    if scrap and not doc.get(VT["scrap_link"]):
        scrap_entry = _post_scrap(doc, scrap, company, item_map)
        doc.db_set(VT["scrap_link"], scrap_entry, update_modified=False)

    # 5. vehicle is no longer active
    frappe.db.set_value(SERIAL_DOCTYPE, serial_no, "status", "Inactive", update_modified=False)

    new_items = ", ".join(sorted(set(item_map.values())))

    frappe.msgprint(
        _("Vehicle Tear Down {0} submitted.<br>Vehicle Stock Entry: {1}<br>"
          "Reusable Stock Entry: {2}<br>Scrap Stock Entry: {3}<br>"
          "Stock items used: {4}<br>"
          "Vehicle Serial No {5} is now Inactive.").format(
            doc.name, vehicle_entry or "-", reusable_entry or "-", scrap_entry or "-",
            new_items or "-", serial_no
        )
    )


def reverse_vehicle_tear_down(doc, method=None):
    """Vehicle Tear Down on_cancel: cancel the Stock Entries it created (parts first, vehicle last).
    The new items stay in the Item master."""
    for key in ("scrap_link", "reusable_link", "vehicle_link"):
        name = doc.get(VT[key])
        if name and frappe.db.get_value("Stock Entry", name, "docstatus") == 1:
            se = frappe.get_doc("Stock Entry", name)
            se.flags.ignore_permissions = True
            se.cancel()

    serial_no = doc.get(VT["serial"])
    if serial_no and frappe.db.exists(SERIAL_DOCTYPE, serial_no):
        frappe.db.set_value(SERIAL_DOCTYPE, serial_no, "status", "Active", update_modified=False)

def validate_vehicle_tear_down(doc, method=None):
    """Vehicle Tear Down validate: a draft cannot be saved with an unavailable vehicle."""
    serial_no = doc.get(VT["serial"])

    if doc.docstatus == 0 and serial_no:
        _check_vehicle_available(serial_no)

# ------------------------------------------------------------------
# NEW ITEMS (one per part and disposition)
# ------------------------------------------------------------------

def _ensure_items(reusable, scrap):
    """Return {(old_item, disposition): new_item_code}, creating missing items."""
    item_map = {}

    for disposition, rows in (("reusable", reusable), ("scrap", scrap)):
        for r in rows:
            old = r.get(PART["item"])
            key = (old, disposition)

            if key not in item_map:
                item_map[key] = _get_or_create_item(old, disposition)

    return item_map


def _get_or_create_item(old_code, disposition):
    """Find or create '<old item> - <suffix>' in the Item Group of the disposition."""
    group = DISPOSITION_GROUP[disposition]
    suffix = DISPOSITION_SUFFIX[disposition]

    if not frappe.db.exists("Item Group", group):
        frappe.throw(_("Item Group '{0}' does not exist. Create it first.").format(group))

    old = frappe.get_doc("Item", old_code)

    new_code = "{0} - {1}".format(old.name, suffix)
    new_name = "{0} - {1}".format(old.item_name or old.name, suffix)

    # already created by an earlier tear down?
    existing = (
        frappe.db.exists("Item", new_code)
        or frappe.db.get_value("Item", {"item_name": new_name, "item_group": group}, "name")
    )

    if existing:
        item_group, disabled = frappe.db.get_value("Item", existing, ["item_group", "disabled"])

        if item_group != group:
            frappe.throw(
                _("Item {0} already exists but is in Item Group '{1}', not '{2}'.")
                .format(existing, item_group, group)
            )

        if disabled:
            frappe.throw(_("Item {0} is disabled. Enable it or choose another part.").format(existing))

        return existing

    new = frappe.copy_doc(old)

    # Set the new Item identity and disposition Item Group.
    new.item_code = new_code
    new.item_name = new_name
    new.item_group = group

    # IMPORTANT:
    # frappe.copy_doc() also copies child tables from the original Item.
    # Clear the original Item's Storage Location rows so the new
    # Reusable/Scrap Item starts without locations belonging to another Item.
    if new.meta.has_field("custom_storage_location"):
        new.set("custom_storage_location", [])
    new.disabled = 0
    new.is_stock_item = 1

    # a recovered part is not a new variant or a serial/batch tracked unit
    new.has_variants = 0
    new.variant_of = None
    new.set("attributes", [])
    new.has_serial_no = 0
    new.has_batch_no = 0
    new.create_new_batch = 0
    new.opening_stock = 0

    # supplier/customer/barcode/reorder data belongs to the original item
    for table in ("barcodes", "supplier_items", "customer_items", "reorder_levels"):
        new.set(table, [])

    new.flags.ignore_permissions = True
    new.insert()

    return new.name


# ------------------------------------------------------------------
# STOCK ENTRIES
# ------------------------------------------------------------------

def _post_vehicle_issue(doc, serial, company):
    """Material Issue of the vehicle (qty 1). Plain warehouse entry: no shelf needed."""
    if not serial.get("warehouse") or not serial.get("item_code"):
        frappe.throw(_("Serial No {0} must have an Item and a Warehouse.").format(serial.name))

    se = _new_stock_entry(doc, "Material Issue", company)

    if se.meta.has_field(WAREHOUSE_FLAG):
        se.set(WAREHOUSE_FLAG, 1)

    row = {
        "item_code": serial.item_code,
        "qty": 1,
        "s_warehouse": serial.warehouse,
        "serial_no": serial.name,
    }
    # newer v15 versions need this for serial numbers entered directly on the row
    if frappe.get_meta("Stock Entry Detail").has_field("use_serial_batch_fields"):
        row["use_serial_batch_fields"] = 1

    se.append("items", row)

    name = _post(se)
    value = flt(frappe.db.get_value("Stock Entry", name, "total_outgoing_value"))
    return name, value


def _post_reusable(doc, rows, company, rate, item_map):
    """Material Receipt of the new reusable items into their warehouse and shelf."""
    se = _new_stock_entry(doc, "Material Receipt", company)

    if se.meta.has_field(WAREHOUSE_FLAG):
        se.set(WAREHOUSE_FLAG, 0)            # storage location based entry

    pairs = {}                               # (old item, warehouse) -> recovered qty

    for r in rows:
        old = r.get(PART["item"])
        qty = flt(r.get(PART["qty"]))
        warehouse = r.get(PART["destination"])

        line = {
            "item_code": item_map[(old, "reusable")],
            "qty": qty,
            "t_warehouse": warehouse,
        }

        if flt(rate) > 0:
            line["basic_rate"] = flt(rate)
        else:
            line["allow_zero_valuation_rate"] = 1

        se.append("items", line)
        pairs[(old, warehouse)] = pairs.get((old, warehouse), 0) + qty

    # one Storage Location row per row of the tear down's Storage Location table
    if se.meta.has_field(STORAGE_TABLE):
        for loc in doc.get(VT["location_table"]) or []:
            old = loc.get(LOC["item"])
            key = (old, loc.get(LOC["warehouse"]))

            if key in pairs:
                se.append(
                    STORAGE_TABLE,
                    _location_row(se, loc, pairs[key], item_map[(old, "reusable")]),
                )

    return _post(se)


def _post_scrap(doc, rows, company, item_map):
    """Material Receipt of the new scrap items into the scrap warehouse, valued at zero."""
    warehouse = doc.get(VT["scrap_warehouse"])
    se = _new_stock_entry(doc, "Material Receipt", company)

    if se.meta.has_field(WAREHOUSE_FLAG):
        se.set(WAREHOUSE_FLAG, 1)            # plain warehouse entry, no shelf needed for scrap

    for r in rows:
        se.append("items", {
            "item_code": item_map[(r.get(PART["item"]), "scrap")],
            "qty": flt(r.get(PART["qty"])),
            "t_warehouse": warehouse,
            "basic_rate": 0,
            "allow_zero_valuation_rate": 1,  # scrap is valued at zero: confirm with accounts
        })

    return _post(se)


def _location_row(se, loc, recovered_qty, new_item):
    """One Storage Location row for the Stock Entry. Only fields that exist are filled."""
    child_dt = se.meta.get_field(STORAGE_TABLE).options
    existing = {f.fieldname for f in frappe.get_meta(child_dt).fields}

    values = {
        "item": new_item,
        "warehouse": loc.get(LOC["warehouse"]),
        "floor": loc.get(LOC["floor"]),
        "area": loc.get(LOC["area"]),
        "shelf": loc.get(LOC["shelf"]),
        "rack": loc.get(LOC["rack"]),
        "uom": loc.get(LOC["uom"]) or frappe.db.get_value("Item", new_item, "stock_uom"),
        "quantity": flt(loc.get(LOC["qty"])) or recovered_qty,
    }
    return {k: v for k, v in values.items() if k in existing and v not in (None, "")}


# ------------------------------------------------------------------
# VALIDATION
# ------------------------------------------------------------------

def _split_parts(doc, parts, company):
    """Validate Recovered Parts and split them into reusable and scrap lists."""
    scrap_warehouse = doc.get(VT["scrap_warehouse"])
    reusable, scrap = [], []

    for r in parts:
        item = r.get(PART["item"])
        disposition = (r.get(PART["disposition"]) or "").strip().lower()

        # anything that is not "scrap" counts as reusable, whatever the label is
        if disposition and disposition != "scrap":
            disposition = "reusable"

        if not item or not frappe.db.exists("Item", item):
            frappe.throw(_("Row {0} (Recovered Parts): a valid item is required.").format(r.idx))

        if flt(r.get(PART["qty"])) <= 0:
            frappe.throw(_("Row {0} (Recovered Parts): quantity must be greater than zero.").format(r.idx))

        if disposition == "reusable":
            destination = r.get(PART["destination"]) or _location_warehouse(doc, item)

            if not destination:
                frappe.throw(_("Row {0} (Recovered Parts): Destination Warehouse is required for reusable item {1}.").format(r.idx, item))

            _check_warehouse(destination, company)

            if not r.get(PART["destination"]):
                r.set(PART["destination"], destination)
                r.db_set(PART["destination"], destination, update_modified=False)

            reusable.append(r)

        elif disposition == "scrap":
            if not scrap_warehouse:
                frappe.throw(_("Select the Scrap Warehouse for the scrap parts."))

            _check_warehouse(scrap_warehouse, company)
            scrap.append(r)

        else:
            frappe.throw(_("Row {0} (Recovered Parts): Disposition is required (Reusable or Scrap).").format(r.idx))

    return reusable, scrap


def _validate_locations(doc, reusable):
    """Every reusable part needs a Storage Location row (and a shelf) in its destination warehouse."""
    locations = doc.get(VT["location_table"]) or []

    for r in reusable:
        item = r.get(PART["item"])
        warehouse = r.get(PART["destination"])

        matches = [
            loc for loc in locations
            if loc.get(LOC["item"]) == item and loc.get(LOC["warehouse"]) == warehouse
        ]

        if not matches:
            frappe.throw(_("Row {0} (Recovered Parts): add a Storage Location row for {1} in {2}.").format(r.idx, item, warehouse))

        if REQUIRE_SHELF and not any(loc.get(LOC["shelf"]) for loc in matches):
            frappe.throw(_("Row {0} (Recovered Parts): choose the floor, area and shelf for {1}.").format(r.idx, item))


def _location_warehouse(doc, item):
    for loc in doc.get(VT["location_table"]) or []:
        if loc.get(LOC["item"]) == item and loc.get(LOC["warehouse"]):
            return loc.get(LOC["warehouse"])
    return None


def _check_warehouse(warehouse, company):
    if not frappe.db.exists("Warehouse", warehouse):
        frappe.throw(_("Warehouse {0} does not exist.").format(warehouse))

    wh_company = frappe.db.get_value("Warehouse", warehouse, "company")
    if wh_company and wh_company != company:
        frappe.throw(_("Warehouse {0} belongs to company {1}, not {2}.").format(warehouse, wh_company, company))


# ------------------------------------------------------------------
# SMALL HELPERS
# ------------------------------------------------------------------

def _company(doc, serial):
    if VT["company"] and doc.get(VT["company"]):
        return doc.get(VT["company"])

    warehouse = serial.get("warehouse")
    company = frappe.db.get_value("Warehouse", warehouse, "company") if warehouse else None

    if not company:
        frappe.throw(_("Unable to determine the Company. Ensure the Serial No has a valid warehouse."))

    return company


def _new_stock_entry(doc, entry_type, company):
    se = frappe.new_doc("Stock Entry")
    se.stock_entry_type = entry_type
    se.purpose = entry_type
    se.company = company
    se.remarks = _("Auto-created from {0} {1}. Vehicle Serial No: {2}").format(
        doc.doctype, doc.name, doc.get(VT["serial"])
    )

    if doc.get(VT["recovery_date"]):
        se.set_posting_time = 1
        se.posting_date = doc.get(VT["recovery_date"])

    return se


def _post(se):
    se.flags.ignore_permissions = True
    se.insert()
    se.submit()
    return se.name