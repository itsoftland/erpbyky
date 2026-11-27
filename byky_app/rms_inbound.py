"""
Messages that come FROM RMS INTO ERPNext.

Two endpoints, both create a record:

    POST /api/method/byky_app.rms_inbound.vehicle_transfer_request
    POST /api/method/byky_app.rms_inbound.service_request

Headers:
    Authorization: token <api_key>:<api_secret>   (a user with role "RMS Integration")
    Content-Type: application/json

Body (same envelope as the ERPNext -> RMS messages):

  Vehicle Transfer Request
    {
      "event_id": "uuid",
      "action": "create",
      "sent_at": "2026-11-26 10:15:00",
      "data": {
        "transfer_id": "VT-0001",          required, unique, created in RMS
        "vehicle_number": "BIKE-0001",     required (or send "rfid_epc" instead)
        "source_branch": "CREEK PARK 1",   required, Branch name or branch code
        "target_branch": "MARINA 2",       required, Branch name or branch code
        "requested_on": "2026-11-26 10:14:00"
      }
    }
    source_warehouse and target_warehouse are filled from the branches.

  Service Request
    {
      "event_id": "uuid",
      "action": "create",
      "sent_at": "2026-11-26 10:15:00",
      "data": {
        "service_id": "SR-0101",           required, unique, created in RMS
        "vehicle_number": "BIKE-0001",     required (or send "rfid_epc" instead)
        "complaint_type": "Regular",
        "complaint_details": "Brake is loose",
        "brand": "...", "vehicle_type": "...", "category": "..."   optional
      }
    }

Replies (the JSON body always has "status"):
    200 {"status": "received", "name": "..."}                 stored
    200 {"status": "duplicate", "name": "...", "erp_status": "Open"}   already stored
    401 / 403                                               wrong key or no permission
    422 {"status": "rejected", "errors": [...]}             data problem: fix it, do not retry
    500 {"status": "error", "errors": [...]}                ERPNext setup problem: tell the ERPNext team
    5xx                                                     retry later

The same transfer_id / service_id sent again is never created twice.
"""

import frappe
from frappe.utils import strip_html_tags

from byky_app.rms_sync import RMS_VEHICLE_CATEGORY, is_rental_vehicle

INTEGRATION_ROLES = ("RMS Integration", "System Manager")


# ============================================================
# WHAT EACH ENDPOINT CREATES
# ============================================================
# "fields" maps an RMS key to the ERPNext fieldname. Change a fieldname here
# if your DocType uses a different one, then run check_inbound_setup().

INBOUND = {
    "vehicle_transfer_request": {
        "doctype": "Vehicle Transfer Request",
        "id_key": "transfer_id",
        "fields": {
            "transfer_id": "transfer_id",
            "vehicle_number": "vehicle_number",
            "source_branch": "source_branch",
            "target_branch": "target_branch",
            "requested_on": "requested_on",
        },
        "required": ["transfer_id", "vehicle_number", "source_branch", "target_branch"],
        "vehicle_field": "vehicle_number",
        "branch_fields": ["source_branch", "target_branch"],
        "warehouse_from_branch": {
            "source_warehouse": "source_branch",
            "target_warehouse": "target_branch",
        },
        "must_differ": ("source_branch", "target_branch"),
    },

    "service_request": {
        "doctype": "Service Request",
        "id_key": "service_id",
        "fields": {
            "service_id": "service_id",
            "vehicle_number": "vehicle_number",
            "complaint_type": "complaint_type",
            "complaint_details": "complaint_details",
            "brand": "brand",
            "vehicle_type": "vehicle_type",
            "category": "category",
        },
        "required": ["service_id", "vehicle_number"],
        "vehicle_field": "vehicle_number",
    },
}


# ============================================================
# ENDPOINTS
# ============================================================

@frappe.whitelist(methods=["POST"])
def vehicle_transfer_request():
    return _handle("vehicle_transfer_request")


@frappe.whitelist(methods=["POST"])
def service_request():
    return _handle("service_request")


# ============================================================
# COMMON HANDLER
# ============================================================

def _reply(status, code=200, **extra):
    frappe.response.update({"status": status, **extra})
    frappe.local.response["http_status_code"] = code


def _clean(error):
    return strip_html_tags(str(error)).strip()


def _handle(key):
    frappe.only_for(INTEGRATION_ROLES)

    cfg = INBOUND[key]

    body = frappe.request.get_json(silent=True)

    if not isinstance(body, dict):
        return _reply("rejected", 422, errors=["Body must be a JSON object."])

    action = (body.get("action") or "create").lower()

    if action != "create":
        return _reply("rejected", 422, errors=[f"Unsupported action: {action}. Only create is accepted."])

    data = dict(body.get("data") or {})

    # ---- required values -------------------------------------------------
    if not data.get("vehicle_number") and data.get("rfid_epc"):
        data["vehicle_number"] = _vehicle_from_rfid(data["rfid_epc"])

    errors = [
        f"data.{k} is required."
        for k in cfg["required"]
        if not str(data.get(k) or "").strip()
    ]

    if errors:
        return _reply("rejected", 422, errors=errors)

    meta = frappe.get_meta(cfg["doctype"])

    # ---- the ERPNext side must have every mapped field ------------------
    missing = sorted({
        field for field in cfg["fields"].values() if not meta.has_field(field)
    })

    if missing:
        frappe.log_error(
            title="RMS inbound mapping",
            message=f"{cfg['doctype']} has no field(s): {', '.join(missing)}",
        )
        return _reply("error", 500, errors=[
            f"ERPNext setup problem: {cfg['doctype']} has no field(s) {', '.join(missing)}."
        ])

    # ---- already stored? --------------------------------------------------
    id_field = cfg["fields"][cfg["id_key"]]
    request_id = str(data[cfg["id_key"]]).strip()

    existing = _find(cfg, id_field, request_id, meta)

    if existing:
        return _reply("duplicate", name=existing.name, erp_status=existing.get("status"))

    # ---- build the values --------------------------------------------------
    values, errors = {}, []

    for rms_key, field in cfg["fields"].items():
        value = data.get(rms_key)

        if value in (None, ""):
            continue

        values[field] = value.strip() if isinstance(value, str) else value

    bad_branches = set()

    for field in cfg.get("branch_fields", []):
        if field in values:
            resolved = _resolve_branch(values[field])

            if resolved:
                values[field] = resolved
            else:
                bad_branches.add(field)
                errors.append(f"Branch not found for data.{field} = {values[field]}.")

    a, b = cfg.get("must_differ", (None, None))

    if a and values.get(a) and values.get(a) == values.get(b):
        errors.append(f"data.{a} and data.{b} must be different branches.")

    for warehouse_field, branch_field in cfg.get("warehouse_from_branch", {}).items():
        if (
            meta.has_field(warehouse_field)
            and warehouse_field not in values
            and values.get(branch_field)
            and branch_field not in bad_branches
        ):
            warehouse = frappe.db.get_value("Branch", values[branch_field], "custom_warehouse")

            if warehouse:
                values[warehouse_field] = warehouse
            else:
                errors.append(f"Branch {values[branch_field]} has no Warehouse linked.")

    vehicle_field = cfg.get("vehicle_field")

    if vehicle_field and values.get(vehicle_field):
        errors += _check_vehicle(values[vehicle_field], meta.get_field(vehicle_field))

    # every Link value must exist
    for field, value in values.items():
        if field == vehicle_field or field in cfg.get("branch_fields", []):
            continue    # checked above

        df = meta.get_field(field)

        if df and df.fieldtype == "Link" and df.options and not frappe.db.exists(df.options, value):
            errors.append(f"{df.label or field}: {df.options} '{value}' does not exist in ERPNext.")

    if errors:
        return _reply("rejected", 422, errors=sorted(set(errors)))

    # ---- create ---------------------------------------------------------------
    # tells rms_sync.on_insert not to send this record back to RMS
    frappe.flags.rms_inbound = True

    try:
        doc = frappe.get_doc({"doctype": cfg["doctype"], **values})
        doc.flags.ignore_permissions = True
        doc.insert(ignore_permissions=True)

    except frappe.DuplicateEntryError:
        frappe.db.rollback()
        existing = _find(cfg, id_field, request_id, meta)
        return _reply("duplicate", name=existing.name if existing else request_id)

    except frappe.ValidationError as e:
        frappe.db.rollback()
        return _reply("rejected", 422, errors=[_clean(e)])

    doc.add_comment("Info", f"Received from RMS (event {body.get('event_id') or '-'}).")

    return _reply("received", name=doc.name)


# ============================================================
# LOOKUPS
# ============================================================

def _find(cfg, id_field, request_id, meta):
    fields = ["name"] + (["status"] if meta.has_field("status") else [])

    return frappe.db.get_value(
        cfg["doctype"], {id_field: request_id}, fields, as_dict=True
    )


def _resolve_branch(value):
    """RMS sends a Branch name or its branch code."""
    if frappe.db.exists("Branch", value):
        return value

    return frappe.db.get_value("Branch", {"branch_code": value}, "name")


def _vehicle_from_rfid(rfid):
    if not frappe.get_meta("Serial No").has_field("custom_rfid_epc"):
        return None

    return frappe.db.get_value("Serial No", {"custom_rfid_epc": str(rfid).strip()}, "name")


def _check_vehicle(value, df):
    """
    The vehicle must exist and be a vehicle (its Item Group has Category Type
    Vehicle), the same rule that decides what is sent to RMS.
    """
    if df and df.fieldtype == "Link" and df.options != "Serial No":
        return []   # linked to something else: the generic Link check handles it

    item_code = frappe.db.get_value("Serial No", value, "item_code")

    if not item_code:
        # a plain text field may hold a plate or other number; a Link may not
        if df and df.fieldtype == "Link":
            return [f"Vehicle {value} does not exist in ERPNext."]
        return []

    if not is_rental_vehicle(item_code):
        return [
            f"{value} is not a vehicle (its Item Group's Category Type is "
            f"not {RMS_VEHICLE_CATEGORY})."
        ]

    return []


# ============================================================
# SETUP CHECK (run once after creating the DocTypes)
# ============================================================

def check_inbound_setup():
    """
    bench --site <site> execute byky_app.rms_inbound.check_inbound_setup

    Lists, for each endpoint, whether every mapped field exists on the DocType,
    and which mandatory fields the RMS message does not fill.
    """
    for key, cfg in INBOUND.items():
        print(f"\n{key}  ->  {cfg['doctype']}")

        if not frappe.db.exists("DocType", cfg["doctype"]):
            print("  DocType not found")
            continue

        meta = frappe.get_meta(cfg["doctype"])
        mapped = set(cfg["fields"].values()) | set(cfg.get("warehouse_from_branch", {}))

        for rms_key, field in cfg["fields"].items():
            df = meta.get_field(field)

            if df:
                extra = f" -> {df.options}" if df.fieldtype in ("Link", "Select") and df.options else ""
                print(f"  OK       {rms_key:20} {field} ({df.fieldtype}{extra})".replace("\n", ", "))
            else:
                print(f"  MISSING  {rms_key:20} {field}")

        layout = ("Section Break", "Column Break", "Tab Break", "HTML", "Button")

        for df in meta.fields:
            if df.reqd and df.fieldname not in mapped and not df.default and df.fieldtype not in layout:
                print(f"  NOTE     mandatory field not in the RMS message: {df.fieldname} ({df.fieldtype})")


# ============================================================
# Kept only so an old hooks.py line does not break Stock Entry.
# If hooks.py still lists these two, remove those lines, then delete these.
# ============================================================

def on_stock_entry_submit(doc, method=None):
    return


def on_stock_entry_cancel(doc, method=None):
    return