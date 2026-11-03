import frappe
from frappe import _
from frappe.utils import flt

# same helper and Item Group names the Vehicle Tear Down uses
from byky_app.vehicle_tear_down import DISPOSITION_GROUP, _get_or_create_item

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
    "scrap_link": "scrap_stock_entry",       # Link to Stock Entry: the scrap receipt
    "vehicle": "vehicle_number",             # Vehicle Number (Link to Serial No)
    "service_type": "service_type",          # Service Type select (Regular / Major Repair)
    "transfer_link": "stock_transfer",       # Link to Stock Entry (read-only): the vehicle transfer
    "transfer_warehouse": "transfer_warehouse",  # Link to Warehouse: where the vehicle goes
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
SUBMIT_ALLOWED_STATUS = ("completed", "non-repairable")  # Service Status values that allow Submit (lower case)

SERVICE_REQUEST_FIELD = "service_id"         # field on Service Entry linking to the Service Request (e.g. SR-023)
SR_STATUS_ON_SUBMIT = "Closed"               # Service Request status after the Service Entry is submitted
SR_STATUS_ON_CANCEL = "Open"                 # status restored if the Service Entry is cancelled

MAJOR_REPAIR = "Major Repair"                # Service Type that triggers the vehicle transfer


def check_submit_status(doc, method=None):
    """Service Entry before_submit: the vehicle transfer must be submitted first (Major Repair),
    and the Service Status must be Completed or Non-repairable."""
    check_vehicle_transfer_submitted(doc)

    status = (doc.get("service_status") or "").strip().lower()

    if status not in SUBMIT_ALLOWED_STATUS:
        frappe.throw(
            _("A Service Entry can be submitted only when Service Status is Completed or Non-repairable.")
        )


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

    # 2. damaged parts -> Material Receipt of NEW scrap items into the scrap warehouse
    if damaged and not doc.get(SE["scrap_link"]):
        name = _post_scrap(doc, damaged, company)
        doc.db_set(SE["scrap_link"], name, update_modified=False)


def reverse_service_stock(doc, method=None):
    """Service Entry on_cancel: cancel the Stock Entries it created (scrap first).
    The scrap items created for damaged parts stay in the Item master."""
    for key in ("scrap_link", "issue_link"):
        name = doc.get(SE[key])
        if name and frappe.db.get_value("Stock Entry", name, "docstatus") == 1:
            se = frappe.get_doc("Stock Entry", name)
            se.flags.ignore_permissions = True
            se.cancel()


# ------------------------------------------------------------------
# Major Repair -> DRAFT Material Transfer of the vehicle; the user submits it
# ------------------------------------------------------------------
def sync_vehicle_transfer(doc, method=None):
    """Service Entry on_update (every save):
    - Major Repair + Transfer To Warehouse selected, no transfer yet -> create a DRAFT transfer
    - draft transfer exists -> keep its target warehouse in step, or delete it if no longer needed
    - submitted transfer + Service Type back to Regular -> cancel it"""
    if doc.docstatus != 0:
        return

    is_major = doc.get(SE["service_type"]) == MAJOR_REPAIR
    target = doc.get(SE["transfer_warehouse"])
    existing = doc.get(SE["transfer_link"])

    if not existing:
        if is_major and target:
            _create_transfer_draft(doc, target)
        return

    status = frappe.db.get_value("Stock Entry", existing, "docstatus")

    if status is None or status == 2:                 # deleted or cancelled elsewhere: allow a new one
        doc.db_set(SE["transfer_link"], None, update_modified=False)
        if is_major and target:
            _create_transfer_draft(doc, target)
    elif status == 0:                                 # still a draft
        if not is_major or not target:
            _delete_draft(doc, existing)
        else:
            _update_draft_target(existing, target)
    elif status == 1 and not is_major:                # already submitted, but service is now Regular
        reverse_vehicle_transfer(doc)
        doc.db_set(SE["transfer_link"], None, update_modified=False)


def _create_transfer_draft(doc, target):
    """Create (but do NOT submit) the Material Transfer of the vehicle."""
    serial_no = doc.get(SE["vehicle"])
    if not serial_no:
        frappe.throw(_("Vehicle Number is required to transfer the vehicle."))

    sn = frappe.db.get_value("Serial No", serial_no, ["item_code", "warehouse"], as_dict=True)
    if not sn or not sn.warehouse:
        frappe.throw(_("Vehicle {0} is not in any warehouse.").format(serial_no))

    _check_leaf_warehouse(target, _("Transfer To Warehouse"))
    _check_leaf_warehouse(sn.warehouse, _("Vehicle warehouse"))

    if sn.warehouse == target:
        frappe.throw(_("Vehicle {0} is already in {1}.").format(serial_no, target))

    se = _new_stock_entry(doc, "Material Transfer", _company(doc, []))

    if se.meta.has_field(WAREHOUSE_FLAG):
        se.set(WAREHOUSE_FLAG, 1)                     # plain warehouse transfer, no shelf rows

    se.append("items", {
        "item_code": sn.item_code,
        "s_warehouse": sn.warehouse,
        "t_warehouse": target,
        "qty": 1,
        "serial_no": serial_no,
        "use_serial_batch_fields": 1,
    })

    se.flags.ignore_permissions = True
    se.insert()                                       # draft only: the user submits it

    doc.db_set(SE["transfer_link"], se.name, update_modified=False)
    frappe.msgprint(
        _("Stock Entry {0} created. Submit it to move the vehicle to {1}.").format(se.name, target),
        alert=True,
    )


def _update_draft_target(name, target):
    """Keep the draft transfer's target warehouse equal to Transfer To Warehouse."""
    se = frappe.get_doc("Stock Entry", name)
    if all(r.t_warehouse == target for r in se.items):
        return
    _check_leaf_warehouse(target, _("Transfer To Warehouse"))
    for r in se.items:
        r.t_warehouse = target
    se.flags.ignore_permissions = True
    se.save()


def _delete_draft(doc, name):
    doc.db_set(SE["transfer_link"], None, update_modified=False)   # remove the link first
    frappe.delete_doc("Stock Entry", name, ignore_permissions=True, force=1)


def _check_leaf_warehouse(warehouse, label):
    """Stock can only move between non-group warehouses."""
    if frappe.db.get_value("Warehouse", warehouse, "is_group"):
        frappe.throw(_("{0} {1} is a group warehouse. Choose a non-group warehouse.").format(label, warehouse))


def check_vehicle_transfer_submitted(doc):
    """A Major Repair Service Entry can only be submitted after its transfer Stock Entry is submitted."""
    if doc.get(SE["service_type"]) != MAJOR_REPAIR:
        return

    name = doc.get(SE["transfer_link"])
    if not name:
        frappe.throw(_("Select the Transfer To Warehouse and save to create the vehicle transfer."))

    if frappe.db.get_value("Stock Entry", name, "docstatus") != 1:
        frappe.throw(
            _("Submit the vehicle transfer Stock Entry {0} before continuing the service.").format(name)
        )


def reverse_vehicle_transfer(doc, method=None):
    """Service Entry on_cancel / on_trash: undo the vehicle transfer (cancel it, or delete the draft)."""
    name = doc.get(SE["transfer_link"])
    if not name:
        return

    status = frappe.db.get_value("Stock Entry", name, "docstatus")
    if status == 1:
        se = frappe.get_doc("Stock Entry", name)
        se.flags.ignore_permissions = True
        se.flags.ignore_links = True
        se.cancel()
    elif status == 0:
        _delete_draft(doc, name)


# ------------------------------------------------------------------
def close_service_request(doc, method=None):
    """Service Entry on_submit: set the linked Service Request status to Closed."""
    _set_service_request_status(doc, SR_STATUS_ON_SUBMIT)


def reopen_service_request(doc, method=None):
    """Service Entry on_cancel: put the Service Request back to Open."""
    _set_service_request_status(doc, SR_STATUS_ON_CANCEL)


def _set_service_request_status(doc, status):
    # try the Service ID link field first, then the Service Entry's own name (same id as the request)
    candidates = [doc.get(SERVICE_REQUEST_FIELD), doc.name]
    service_request = next(
        (c for c in candidates if c and frappe.db.exists("Service Request", c)), None
    )

    if not service_request:
        frappe.log_error(
            f"No Service Request found for Service Entry {doc.name}",
            "Service Request status update",
        )
        return

    frappe.db.set_value("Service Request", service_request, "status", status)
    frappe.msgprint(
        _("Service Request {0} set to {1}").format(service_request, status),
        alert=True,
    )


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
    """Receive the damaged parts as NEW items in the Scrap Item Group, valued at zero."""
    warehouse = doc.get(SE["scrap_warehouse"])
    se = _new_stock_entry(doc, "Material Receipt", company)

    if se.meta.has_field(WAREHOUSE_FLAG):
        se.set(WAREHOUSE_FLAG, 1)            # plain warehouse entry, no shelf needed for scrap

    scrap_items = {}                         # damaged item -> its scrap item

    for r in rows:
        damaged_item = r.get(DAMAGED["item"])

        if damaged_item not in scrap_items:
            scrap_items[damaged_item] = _scrap_item(damaged_item)

        se.append("items", {
            "item_code": scrap_items[damaged_item],
            "qty": flt(r.get(DAMAGED["qty"])),
            "t_warehouse": warehouse,
            "basic_rate": 0,
            "allow_zero_valuation_rate": 1,  # scrap is valued at zero: confirm with accounts
        })

    return _post(se)


def _scrap_item(item):
    """The item to post for a damaged part: a '<item> - Scrap' item in the Scrap Item Group."""
    # already a scrap item (picked directly): do not create "X - Scrap - Scrap"
    if frappe.db.get_value("Item", item, "item_group") == DISPOSITION_GROUP["scrap"]:
        return item

    return _get_or_create_item(item, "scrap")


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