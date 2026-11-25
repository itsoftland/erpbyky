import base64
import csv
import hashlib
import hmac
import json
import os
import re
import time
import uuid

import frappe
import requests
from frappe.utils import add_to_date, now_datetime


# ============================================================
# RMS API CONFIGURATION
# ============================================================
# Paths are relative to RMS Settings -> Base URL, e.g.
#   https://q8byky.baratosoftware.com/api/v1/integrations/erpnext
# Final paths are confirmed by RMS.
#
# "rename" maps an ERPNext fieldname to the key sent to RMS.
# Confirm the key names with the RMS team.

# Item Group Category Types that are sent to RMS (Item Groups, and the Items
# that belong to them). Spare, Scarp and ReUse Spare are never sent.
RMS_CATEGORY_TYPES = ["Vehicle", "Asset"]

# Serial Nos are sent to RMS as vehicles only when the item's Item Group has
# this Category Type. Asset groups never produce vehicles.
RMS_VEHICLE_CATEGORY = "Vehicle"

SYNC_MAP = {
    "Country": {
        "path": "/country",
        "fields": ["name", "country_name", "code"],
    },

    "State": {
        "path": "/state",
        "fields": ["name", "state_name", "country", "state_code"],
    },

    "Branch Type": {
        "path": "/branch-type",
        "fields": ["name", "branch_type", "branch_type_code"],
    },

    "Branch": {
        "path": "/branch",
        "fields": [
            "name",
            "branch",
            "branch_type",
            "state",
            "warehouse",
            "branch_code",
            "company",
            "location",
        ],
    },

    "Event Location": {
        "path": "/event-location",
        "fields": [
            "name",
            "event_location",
            "state",
            "branch",
            "status",
            "contact_person_name",
            "contact_person_phone_no",
            "address",
        ],
    },

    "Warehouse": {
        "path": "/warehouse",
        "fields": [
            "name",
            "warehouse_name",
            "warehouse_type",
            "parent_warehouse",
            "is_group",
            "disabled",
        ],
    },

    "Designation": {
        "path": "/designation",
        "fields": ["name", "designation_name", "description"],
    },

    # Only what RMS needs. Date of birth, personal email etc. are NOT sent
    # (confirm with the client before adding them back).
    "Employee": {
        "path": "/employee",
        "fields": [
            "name",
            "employee_name",
            "status",
            "department",
            "designation",
            "branch",
            "cell_number",
        ],
    },

    "Department": {
        "path": "/department",
        "fields": [
            "name",
            "department_name",
            "parent_department",
            "is_group",
        ],
    },

    "Brand": {
        "path": "/brand",
        "fields": ["name", "brand", "description"],
    },

    "Item Group": {
        "path": "/item-group",
        "fields": [
            "name",
            "item_group_name",
            "parent_item_group",
            "is_group",
            "custom_code",
            "custom_category_type",
        ],
        "rename": {
            "custom_code": "code",
            "custom_category_type": "category_type",
        },
        # Only Item Groups whose Category Type is Vehicle or Asset go to RMS.
        # Spare, Scarp and ReUse Spare groups are never sent.
        "sync_when": {"custom_category_type": RMS_CATEGORY_TYPES},
    },

    "Item": {
        "path": "/item",
        "fields": [
            "name",
            "item_code",
            "item_name",
            "item_group",
            "brand",
            "stock_uom",
            "has_serial_no",
            "serial_no_series",
            "disabled",
            "description",
        ],
        # An Item is sent only when its Item Group's Category Type qualifies.
        # The group is looked up live, so no field is needed on Item and
        # nothing goes stale when a group's Category Type changes.
        "sync_when_linked": [
            {
                "field": "item_group",
                "doctype": "Item Group",
                "target_field": "custom_category_type",
                "allowed": RMS_CATEGORY_TYPES,
            },
        ],
    },

    "Serial No": {
        "path": "/vehicle",
        "fields": [
            "name",
            "serial_no",
            "item_code",
            "item_name",
            "description",
            "warehouse",
            "status",
            "purchase_date",
            "supplier",
            "warranty_expiry_date",
            "warranty_period",
            "amc_expiry_date",
            "custom_rfid_epc",
            "custom_registration_no",
        ],
        "rename": {
            "custom_rfid_epc": "rfid_epc",
            "custom_registration_no": "registration_no",
        },
    },

    "Asset": {
        "path": "/asset",
        "fields": [
            "name",
            "asset_name",
            "item_code",
            "asset_category",
            "location",
            "status",
            "purchase_date",
        ],
    },
}

# Minutes to wait after attempt 1, 2, 3 ... (the last value repeats).
BACKOFF_MINUTES = [5, 30, 120, 360, 720]
DEFAULT_TIMEOUT = 10
DEFAULT_MAX_ATTEMPTS = 10
PENDING_GRACE_MINUTES = 10

FINAL_STATUSES = ("Success", "Failed", "Failed - Auth", "Exhausted")


# ============================================================
# SETTINGS HELPERS
# ============================================================

def _settings():
    return frappe.get_single("RMS Settings")


def is_rental_vehicle(item_code):
    """
    The one rule for vehicles: the item's Item Group has Category Type
    "Vehicle" (RMS_VEHICLE_CATEGORY). Asset groups do not produce vehicles.
    """
    if not item_code:
        return False

    item_group = frappe.db.get_value("Item", item_code, "item_group")

    if not item_group:
        return False

    category = frappe.db.get_value(
        "Item Group", item_group, "custom_category_type"
    )

    return category == RMS_VEHICLE_CATEGORY


# ============================================================
# ENVELOPE / PAYLOAD
# ============================================================

def _is_deactivated(doc):
    if doc.doctype == "Employee":
        return doc.get("status") == "Left"

    if doc.doctype == "Asset":
        return doc.get("status") in ("Scrapped", "Sold")

    return bool(doc.get("disabled"))


def should_sync(doc):
    """
    False when SYNC_MAP says this record must not go to RMS.

    SYNC_MAP[doctype]["sync_when"]
        {fieldname: [allowed values]} on the record itself.
    SYNC_MAP[doctype]["sync_when_linked"]
        [{"field", "doctype", "target_field", "allowed"}]: the value of
        target_field on the linked document (looked up now) must be allowed.
        Example: an Item is sent only if its Item Group's Category Type is
        Vehicle or Asset.
    """
    cfg = SYNC_MAP.get(doc.doctype, {})

    rules = cfg.get("sync_when") or {}

    if not all(doc.get(field) in allowed for field, allowed in rules.items()):
        return False

    for rule in cfg.get("sync_when_linked") or []:
        target = doc.get(rule["field"])

        if not target:
            return False

        value = frappe.db.get_value(
            rule["doctype"], target, rule["target_field"]
        )

        if value not in rule["allowed"]:
            return False

    return True


def _sync_filters(doctype):
    """The same rules as a frappe filter dict (for exports and bulk marking)."""
    cfg = SYNC_MAP.get(doctype, {})

    filters = {
        field: ["in", allowed]
        for field, allowed in (cfg.get("sync_when") or {}).items()
    }

    for rule in cfg.get("sync_when_linked") or []:
        names = frappe.get_all(
            rule["doctype"],
            filters={rule["target_field"]: ["in", rule["allowed"]]},
            pluck="name",
        )

        filters[rule["field"]] = ["in", names or [""]]

    return filters


def _build_data(doc, action):
    cfg = SYNC_MAP[doc.doctype]
    rename = cfg.get("rename", {})

    # A leaving employee needs no personal data to be deactivated.
    if doc.doctype == "Employee" and action == "deactivate":
        return {"name": doc.name, "status": doc.get("status")}

    return {
        rename.get(field, field): doc.get(field)
        for field in cfg["fields"]
    }


def build_payload(doc, action, event_id, settings=None):
    settings = settings or _settings()

    company = (
        doc.get("company")
        or settings.get("company")
        or frappe.defaults.get_global_default("company")
    )

    return {
        "event_id": event_id,
        "doctype": doc.doctype,
        "name": doc.name,
        "action": action,
        "modified": str(doc.modified),
        "sent_at": str(now_datetime()),
        "company": company,
        "data": _build_data(doc, action),
    }


# ============================================================
# RMS SYNC LOG (one row per change; retries reuse the row)
# ============================================================

def _queue(doc, action):
    """
    Create the RMS Sync Log row for one change and return its name.
    The event_id is created here and reused on every retry.
    """
    settings = _settings()

    if not settings.enabled:
        return None

    event_id = str(uuid.uuid4())

    payload = build_payload(doc, action, event_id, settings)

    # Employee payloads are not stored (personal data). They are rebuilt
    # from the document on retry. Deactivations carry no personal data.
    store_payload = not (doc.doctype == "Employee" and action != "deactivate")

    log = frappe.get_doc({
        "doctype": "RMS Sync Log",
        "ref_doctype": doc.doctype,
        "ref_name": doc.name,
        "action": action,
        "event_id": event_id,
        "status": "Pending",
        "attempts": 0,
        # safety net: picked up by the retry job if the enqueue is lost
        "next_retry": add_to_date(now_datetime(), minutes=PENDING_GRACE_MINUTES),
        "payload": json.dumps(payload, default=str) if store_payload else None,
    })

    log.insert(ignore_permissions=True)

    return log.name


def _enqueue_change(doc, action):
    log_name = _queue(doc, action)

    if not log_name:
        return

    frappe.enqueue(
        "byky_app.rms_sync.send",
        log_name=log_name,
        queue="short",
        enqueue_after_commit=True,
    )


# ============================================================
# DOC EVENT HOOKS (master data)
# ============================================================

def on_insert(doc, method=None):
    """
    Send newly inserted master records.
    Serial No is handled through Manufacture Stock Entry and the sweep.
    """

    if doc.doctype not in SYNC_MAP or doc.doctype == "Serial No":
        return

    if not should_sync(doc):
        return

    _enqueue_change(doc, "create")


def on_update(doc, method=None):
    """
    Send an update (or deactivation) when a synced field changed.
    """

    if doc.doctype not in SYNC_MAP or doc.doctype == "Serial No":
        return

    before = doc.get_doc_before_save()

    # on_update also runs inside an insert; on_insert handles that case.
    if doc.flags.in_insert or not before:
        return

    was_synced = should_sync(before)
    now_synced = should_sync(doc)

    # Never was and still is not sent to RMS (for example a Spare group).
    if not was_synced and not now_synced:
        return

    # Became eligible (for example Category Type changed to Vehicle).
    if not was_synced and now_synced:
        already_sent = frappe.db.exists(
            "RMS Sync Log",
            {
                "ref_doctype": doc.doctype,
                "ref_name": doc.name,
                "status": "Success",
            },
        )

        _enqueue_change(doc, "update" if already_sent else "create")
        return

    # No longer eligible (for example changed to Spare): RMS deactivates it.
    if was_synced and not now_synced:
        _enqueue_change(doc, "deactivate")
        return

    fields = [f for f in SYNC_MAP[doc.doctype]["fields"] if f != "name"]

    if not any(before.get(f) != doc.get(f) for f in fields):
        return

    if _is_deactivated(doc) and not _is_deactivated(before):
        action = "deactivate"
    else:
        action = "update"

    _enqueue_change(doc, action)


def on_trash(doc, method=None):
    """
    RMS never deletes; a deleted record is sent as a deactivation.
    """

    if doc.doctype not in SYNC_MAP or doc.doctype == "Serial No":
        return

    if not should_sync(doc):
        return

    _enqueue_change(doc, "deactivate")


# ============================================================
# RMS API PUSH
# ============================================================

def _user_agent():
    try:
        from byky_app import __version__
    except Exception:
        __version__ = "unknown"

    return f"byky_app/{__version__}"


def _headers(settings, body):
    headers = {
        "Content-Type": "application/json",
        "User-Agent": _user_agent(),
    }

    token = settings.get_password("auth_token")

    if token:
        headers["Authorization"] = f"Bearer {token}"

    # Optional request signing: HMAC-SHA256 over "<timestamp>.<body>"
    secret = settings.get_password("signing_secret", raise_exception=False)

    if secret:
        timestamp = str(int(time.time()))

        digest = hmac.new(
            secret.encode(),
            f"{timestamp}.{body}".encode(),
            hashlib.sha256,
        ).digest()

        headers["X-Byky-Timestamp"] = timestamp
        headers["X-Byky-Signature"] = base64.b64encode(digest).decode()

    return headers


def _alert_admin(log):
    """E-mail System Managers, at most once an hour per status."""

    cache_key = f"rms_sync_alert_{log.status}"

    if frappe.cache().get_value(cache_key):
        return

    frappe.cache().set_value(cache_key, 1, expires_in_sec=3600)

    users = frappe.get_all(
        "Has Role",
        filters={"role": "System Manager", "parenttype": "User"},
        pluck="parent",
    )

    recipients = [
        u for u in set(users)
        if u not in ("Administrator", "Guest")
        and frappe.db.get_value("User", u, "enabled")
    ]

    if not recipients:
        return

    frappe.sendmail(
        recipients=recipients,
        subject=f"RMS sync {log.status}: {log.ref_doctype} {log.ref_name}",
        message=(
            f"RMS sync status: {log.status}<br>"
            f"Record: {log.ref_doctype} {log.ref_name}<br>"
            f"Error: {log.error or ''}"
        ),
    )


def _success(log, response):
    log.db_set({
        "status": "Success",
        "response": response,
        "error": "",
        "next_retry": None,
    })

    if log.ref_doctype == "Serial No" and log.action == "create":
        frappe.db.set_value(
            "Serial No",
            log.ref_name,
            "rms_synced",
            1,
            update_modified=False,
        )

    return "Success"


def _fail(log, error, retry=False, status="Failed", response=None):
    updates = {
        "error": (error or "")[:500],
        "response": response,
        "next_retry": None,
    }

    if retry:
        max_attempts = _settings().get("max_attempts") or DEFAULT_MAX_ATTEMPTS

        if (log.attempts or 0) >= max_attempts:
            status = "Exhausted"
        else:
            index = min(max((log.attempts or 1) - 1, 0), len(BACKOFF_MINUTES) - 1)

            status = "Retrying"
            updates["next_retry"] = add_to_date(
                now_datetime(), minutes=BACKOFF_MINUTES[index]
            )

    updates["status"] = status

    log.db_set(updates)

    if status in ("Failed - Auth", "Exhausted"):
        _alert_admin(log)

    return status


def send(log_name):
    """
    Send one RMS Sync Log entry to RMS and record the outcome.
    Returns the resulting status.

    RMS reply handling:
      200 received / duplicate -> Success
      401                      -> Failed - Auth (no retry, admin alerted)
      413                      -> Failed (no retry)
      429 / 5xx / timeout / no connection -> Retrying with back-off
      any other 4xx            -> Failed (no retry)
    """

    log = frappe.get_doc("RMS Sync Log", log_name)

    if log.status not in ("Pending", "Retrying"):
        return log.status

    settings = _settings()

    if not settings.enabled:
        return log.status

    cfg = SYNC_MAP.get(log.ref_doctype)

    if not cfg:
        return _fail(log, f"No RMS sync configuration for {log.ref_doctype}")

    base_url = (settings.base_url or "").rstrip("/")

    if not base_url:
        log.db_set("error", "RMS Base URL is missing.")
        return log.status

    if not base_url.startswith("https://") and not frappe.conf.developer_mode:
        log.db_set("error", "RMS Base URL must use HTTPS.")
        return log.status

    # ---- build the body (stored payload, or rebuilt from the document) ----
    if log.payload:
        payload = json.loads(log.payload)
    else:
        if not log.event_id:
            log.db_set("event_id", str(uuid.uuid4()))

        if not frappe.db.exists(log.ref_doctype, log.ref_name):
            return _fail(log, "Document no longer exists")

        doc = frappe.get_doc(log.ref_doctype, log.ref_name)
        payload = build_payload(doc, log.action, log.event_id, settings)

    payload["sent_at"] = str(now_datetime())

    body = json.dumps(payload, default=str)

    # ---- attempt ----
    attempts = (log.attempts or 0) + 1

    log.db_set({"attempts": attempts, "last_attempt": now_datetime()})

    try:
        response = requests.post(
            base_url + cfg["path"],
            data=body,
            headers=_headers(settings, body),
            timeout=settings.get("timeout") or DEFAULT_TIMEOUT,
        )
    except requests.RequestException as e:
        status = _fail(log, str(e), retry=True)
        frappe.db.commit()
        return status

    code = response.status_code
    text = (response.text or "")[:2000]

    if code == 200:
        try:
            reply = (response.json() or {}).get("status")
        except ValueError:
            reply = None

        if reply in ("received", "duplicate"):
            status = _success(log, text)
        else:
            status = _fail(log, f"Unexpected 200 reply: {text[:200]}", response=text)

    elif code == 401:
        status = _fail(log, "401 Unauthorized (check the Auth Token)",
                       status="Failed - Auth", response=text)

    elif code == 413:
        status = _fail(log, "413 Request body too large", response=text)

    elif code == 429 or code >= 500:
        status = _fail(log, f"{code} from RMS", retry=True, response=text)

    else:
        status = _fail(log, f"{code} from RMS: {text[:200]}", response=text)

    frappe.db.commit()

    return status


# ============================================================
# MANUFACTURE STOCK ENTRY SUBMIT HOOK
# ============================================================

def on_manufacture_submit(doc, method=None):
    """
    Trigger vehicle synchronization after Manufacture Stock Entry submission.
    """

    if doc.purpose != "Manufacture":
        return

    frappe.enqueue(
        "byky_app.rms_sync.sync_manufactured_serial_nos",
        stock_entry_name=doc.name,
        queue="short",
        enqueue_after_commit=True,
    )


# ============================================================
# EXTRACT SERIAL NUMBERS FROM STOCK ENTRY
# ============================================================

def get_finished_item_serial_numbers(stock_entry):
    """
    Extract finished-product Serial Nos from Stock Entry.

    Supports:
    1. Stock Entry Detail.serial_no
    2. Serial and Batch Bundle
    """

    serial_numbers = set()

    production_item = stock_entry.get("production_item")

    if not production_item and stock_entry.get("work_order"):
        production_item = frappe.db.get_value(
            "Work Order",
            stock_entry.work_order,
            "production_item",
        )

    for row in stock_entry.get("items") or []:

        # Only process finished-product rows.
        is_finished = (
            row.get("is_finished_item")
            or (
                production_item
                and row.item_code == production_item
            )
        )

        if not is_finished:
            continue

        # METHOD 1: Serial numbers stored directly in row
        raw_serials = row.get("serial_no") or ""

        if isinstance(raw_serials, str):

            serial_numbers.update(
                serial.strip()
                for serial in re.split(r"[\n,;]+", raw_serials)
                if serial.strip()
            )

        # METHOD 2: Serial and Batch Bundle (ERPNext v15)
        bundle_name = row.get("serial_and_batch_bundle")

        if bundle_name:

            bundle_serials = frappe.get_all(
                "Serial and Batch Entry",
                filters={
                    "parent": bundle_name,
                    "parenttype": "Serial and Batch Bundle",
                },
                pluck="serial_no",
            )

            serial_numbers.update(
                serial
                for serial in bundle_serials
                if serial
            )

    return sorted(serial_numbers)


# ============================================================
# SYNCHRONIZE ONE VEHICLE
# ============================================================

def _send_vehicle_now(serial_no):
    """
    Create the log for a vehicle and send it straight away.
    Returns the resulting status ("Skipped" if nothing was sent).
    """

    doc = frappe.get_doc("Serial No", serial_no)

    log_name = _queue(doc, "create")

    if not log_name:
        return "Skipped"

    return send(log_name)


def _vehicle_needs_sending(serial_no):
    values = frappe.db.get_value(
        "Serial No",
        serial_no,
        ["item_code", "rms_synced"],
        as_dict=True,
    )

    if not values:
        return False

    if values.rms_synced:
        return False

    # Rental vehicles only.
    if not is_rental_vehicle(values.item_code):
        return False

    # Already waiting to be sent or retried.
    if frappe.db.exists(
        "RMS Sync Log",
        {
            "ref_doctype": "Serial No",
            "ref_name": serial_no,
            "action": "create",
            "status": ["in", ["Pending", "Retrying"]],
        },
    ):
        return False

    return True


# ============================================================
# SYNCHRONIZE MANUFACTURED SERIAL NUMBERS
# ============================================================

def sync_manufactured_serial_nos(stock_entry_name):
    """
    Called automatically after Manufacture Stock Entry submission.

    Finds finished-product Serial Nos (vehicles only) and sends
    them to RMS.
    """

    try:

        settings = _settings()

        if not settings.enabled:
            return

        stock_entry = frappe.get_doc(
            "Stock Entry",
            stock_entry_name,
        )

        if stock_entry.docstatus != 1:
            return

        if stock_entry.purpose != "Manufacture":
            return

        serial_numbers = get_finished_item_serial_numbers(
            stock_entry
        )

        if not serial_numbers:

            frappe.log_error(
                title="RMS Vehicle Sync",
                message=(
                    f"No finished-product Serial Nos found "
                    f"in Stock Entry {stock_entry_name}."
                ),
            )

            return

        frappe.logger().info(
            f"Manufacture Stock Entry {stock_entry_name}: "
            f"Found {len(serial_numbers)} Serial Nos."
        )

        for serial_no in serial_numbers:

            if not frappe.db.exists("Serial No", serial_no):

                frappe.log_error(
                    title="RMS Vehicle Sync",
                    message=(
                        f"Serial No {serial_no} does not exist. "
                        f"Stock Entry: {stock_entry_name}"
                    ),
                )

                continue

            if not _vehicle_needs_sending(serial_no):
                continue

            _send_vehicle_now(serial_no)

    except Exception:

        frappe.log_error(
            title="RMS Manufacture Vehicle Sync Error",
            message=frappe.get_traceback(),
        )

        raise


# ============================================================
# RETRY (scheduler)
# ============================================================

def retry_failed(limit=200):
    """
    Send log rows that are due: Retrying rows whose next_retry has passed,
    and Pending rows whose first send never ran.

    Permanent failures (Failed, Failed - Auth, Exhausted) are never retried
    automatically. Use "Resend" or requeue_auth_failed().

    The back-off starts at 5 minutes, so run this job every 5 minutes
    (see hooks.py) rather than hourly.
    """

    settings = _settings()

    if not settings.enabled:
        return

    rows = frappe.get_all(
        "RMS Sync Log",
        filters={
            "status": ["in", ["Pending", "Retrying"]],
            "next_retry": ["<=", now_datetime()],
        },
        pluck="name",
        order_by="next_retry asc",
        limit=limit,
    )

    for name in rows:
        send(name)


# ============================================================
# AUTOMATIC PENDING SERIAL NUMBER SYNCHRONIZATION (every 5 minutes)
# ============================================================

def sync_pending_serial_nos(batch=100):
    """
    Safety-net synchronization for vehicle Serial Nos that RMS has
    not received yet.

    Uses Serial No.rms_synced instead of a growing "not in" list. Serials
    that already have an open or permanently failed log are skipped; they
    are handled by retry_failed or by a manual Resend.
    """

    settings = _settings()

    if not settings.enabled:
        return

    pending = frappe.db.sql(
        """
        select sn.name
        from `tabSerial No` sn
        inner join `tabItem` i on i.name = sn.item_code
        inner join `tabItem Group` ig on ig.name = i.item_group
        where ifnull(sn.rms_synced, 0) = 0
          and ig.custom_category_type = %(category)s
          and not exists (
              select 1
              from `tabRMS Sync Log` l
              where l.ref_doctype = 'Serial No'
                and l.ref_name = sn.name
                and l.action = 'create'
                and l.status != 'Success'
          )
        order by sn.creation asc
        limit %(batch)s
        """,
        {"category": RMS_VEHICLE_CATEGORY, "batch": batch},
        pluck="name",
    )

    for name in pending:

        status = _send_vehicle_now(name)

        # Stop if RMS is unavailable or rejecting us.
        if status != "Success":
            break


# ============================================================
# RESEND / REQUEUE (admin actions)
# ============================================================

@frappe.whitelist()
def resend(doctype, name):
    """
    Resend the current state of a record with a new event_id,
    action "update". Used by the "Resend to RMS" form button.
    """

    frappe.only_for("System Manager")

    if doctype not in SYNC_MAP:
        frappe.throw(f"{doctype} is not synchronised with RMS.")

    doc = frappe.get_doc(doctype, name)

    if not should_sync(doc):
        frappe.throw(f"{doctype} {name} is not sent to RMS (see sync_when in SYNC_MAP).")

    if doctype == "Serial No" and not is_rental_vehicle(doc.item_code):
        frappe.throw(
            f"Serial No {name} is not a vehicle (its Item Group's "
            f"Category Type is not {RMS_VEHICLE_CATEGORY})."
        )

    log_name = _queue(doc, "update")

    if not log_name:
        frappe.throw("RMS synchronization is disabled.")

    frappe.enqueue(
        "byky_app.rms_sync.send",
        log_name=log_name,
        queue="short",
        enqueue_after_commit=True,
    )

    return log_name


@frappe.whitelist()
def requeue_auth_failed():
    """
    After fixing the Auth Token, put every "Failed - Auth" row back in
    the retry queue.
    """

    frappe.only_for("System Manager")

    rows = frappe.get_all(
        "RMS Sync Log",
        filters={"status": "Failed - Auth"},
        pluck="name",
    )

    for name in rows:
        frappe.db.set_value(
            "RMS Sync Log",
            name,
            {"status": "Retrying", "attempts": 0, "next_retry": now_datetime()},
        )

    return len(rows)


# ============================================================
# ONE-TIME INITIAL LOAD (run with RMS Settings -> Enabled = off)
# ============================================================

def export_masters():
    """
    Export the existing masters for RMS to match against.

    bench --site <site> execute byky_app.rms_sync.export_masters

    Files are written to <site>/private/files/rms_export/.
    Item Group and Item follow the same rules as the live sync (Category Type
    Vehicle or Asset). Serial No is limited to vehicles (Category Type Vehicle).
    """

    folder = frappe.get_site_path("private", "files", "rms_export")
    os.makedirs(folder, exist_ok=True)

    for doctype, cfg in SYNC_MAP.items():

        meta = frappe.get_meta(doctype)

        fields = [
            f for f in cfg["fields"]
            if f == "name" or meta.has_field(f)
        ]

        if doctype != "Serial No":
            rows = frappe.get_all(
                doctype, fields=fields, filters=_sync_filters(doctype)
            )

        else:
            rows = frappe.db.sql(
                """
                select sn.{cols}
                from `tabSerial No` sn
                inner join `tabItem` i on i.name = sn.item_code
                inner join `tabItem Group` ig on ig.name = i.item_group
                where ig.custom_category_type = %(category)s
                """.format(cols=", sn.".join(fields)),
                {"category": RMS_VEHICLE_CATEGORY},
                as_dict=True,
            )

        rename = cfg.get("rename", {})

        path = os.path.join(folder, f"{doctype.replace(' ', '_')}.csv")

        with open(path, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow([rename.get(c, c) for c in fields])

            for row in rows:
                writer.writerow([row.get(c) for c in fields])

        print(f"{doctype}: {len(rows)} rows -> {path}")


def mark_existing_as_synced(doctypes=None):
    """
    Run ONLY after RMS confirms matching is done, and BEFORE enabling sync:

    bench --site <site> execute byky_app.rms_sync.mark_existing_as_synced

    - vehicle Serial Nos get rms_synced = 1 so the sweep skips them
    - every existing master gets a Success log row so it is shown as synced
    """

    frappe.db.sql(
        """
        update `tabSerial No` sn
        inner join `tabItem` i on i.name = sn.item_code
        inner join `tabItem Group` ig on ig.name = i.item_group
        set sn.rms_synced = 1
        where ig.custom_category_type = %(category)s
        """,
        {"category": RMS_VEHICLE_CATEGORY},
    )

    for doctype in doctypes or list(SYNC_MAP):

        # Vehicles are marked through rms_synced above, never by log rows.
        if doctype == "Serial No":
            continue

        already = set(
            frappe.get_all(
                "RMS Sync Log",
                filters={"ref_doctype": doctype, "status": "Success"},
                pluck="ref_name",
            )
        )

        for name in frappe.get_all(
            doctype, filters=_sync_filters(doctype), pluck="name"
        ):

            if name in already:
                continue

            frappe.get_doc({
                "doctype": "RMS Sync Log",
                "ref_doctype": doctype,
                "ref_name": name,
                "action": "create",
                "event_id": str(uuid.uuid4()),
                "status": "Success",
                "attempts": 0,
                "response": "Matched in initial load",
                "last_attempt": now_datetime(),
            }).insert(ignore_permissions=True)

    frappe.db.commit()


# ============================================================
# PATCH: migrate logs written by the previous version
# ============================================================

def migrate_legacy_logs():
    """
    Add to patches.txt (run once, with sync disabled):

        byky_app.rms_sync.migrate_legacy_logs

    - Serial Nos that already have a Success log get rms_synced = 1
    - old "Failed" rows go back into the retry queue
    - rows without an event_id get one
    """

    frappe.db.sql(
        """
        update `tabSerial No` sn
        set sn.rms_synced = 1
        where exists (
            select 1 from `tabRMS Sync Log` l
            where l.ref_doctype = 'Serial No'
              and l.ref_name = sn.name
              and l.status = 'Success'
        )
        """
    )

    for name in frappe.get_all(
        "RMS Sync Log",
        filters={"event_id": ["in", ["", None]]},
        pluck="name",
    ):
        frappe.db.set_value(
            "RMS Sync Log", name, "event_id", str(uuid.uuid4()),
            update_modified=False,
        )

    frappe.db.sql(
        """
        update `tabRMS Sync Log`
        set status = 'Retrying', attempts = 0, next_retry = %s
        where status = 'Failed'
        """,
        now_datetime(),
    )

    frappe.db.commit()