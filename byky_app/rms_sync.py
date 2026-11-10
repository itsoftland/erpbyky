import json
import re

import frappe
import requests


# ============================================================
# RMS API CONFIGURATION
# ============================================================

SYNC_MAP = {
    "Country": {
        "path": "/api/country",
        "fields": ["name", "country_name", "code"],
    },

    "State": {
        "path": "/api/state",
        "fields": ["name", "state_name", "country", "state_code"],
    },

    "Branch Type": {
        "path": "/api/branch-type",
        "fields": ["name", "branch_type", "branch_type_code"],
    },

    "Branch": {
        "path": "/api/branch",
        "fields": ["name", "branch", "branch_type", "state", "warehouse"],
    },

    "Event Location": {
        "path": "/api/event-location",
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
        "path": "/api/warehouse",
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
        "path": "/api/designation",
        "fields": ["name", "designation_name", "description"],
    },

    "Employee": {
        "path": "/api/employee",
        "fields": [
            "name",
            "employee_name",
            "first_name",
            "middle_name",
            "last_name",
            "gender",
            "date_of_birth",
            "date_of_joining",
            "status",
            "department",
            "designation",
            "branch",
            "reports_to",
            "employment_type",
            "cell_number",
            "personal_email",
            "company_email",
            "user_id",
        ],
    },

    "Department": {
        "path": "/api/department",
        "fields": [
            "name",
            "department_name",
            "parent_department",
            "is_group",
        ],
    },

    "Brand": {
        "path": "/api/brand",
        "fields": ["name", "brand", "description"],
    },

    "Item Group": {
        "path": "/api/item-group",
        "fields": [
            "name",
            "item_group_name",
            "parent_item_group",
            "is_group",
        ],
    },

    "Item": {
        "path": "/api/item",
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
    },

    "Serial No": {
        "path": "/api/vehicle",
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
        ],
    },

    "Asset": {
        "path": "/api/asset",
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


# ============================================================
# COMMON AUTOMATIC INSERT HOOK
# ============================================================

def on_insert(doc, method=None):
    """
    Automatically synchronize newly inserted master records.
    Serial No is handled through Manufacture Stock Entry instead.
    """

    if doc.doctype not in SYNC_MAP:
        return

    if doc.doctype == "Serial No":
        return

    frappe.enqueue(
        "byky_app.rms_sync.push",
        doctype=doc.doctype,
        name=doc.name,
        action="create",
        queue="short",
        enqueue_after_commit=True,
    )


# ============================================================
# RMS API PUSH FUNCTION
# ============================================================

def push(doctype, name, action="create"):
    """
    Send ERPNext document details to RMS API.
    Returns True when the RMS API responds successfully.
    """

    try:

        settings = frappe.get_single("RMS Settings")

        if not settings.enabled:
            frappe.logger().warning("RMS synchronization is disabled.")
            return False

        if doctype not in SYNC_MAP:
            frappe.logger().error(
                f"RMS synchronization not configured for {doctype}"
            )
            return False

        if not settings.base_url:
            frappe.throw("RMS Base URL is missing.")

        cfg = SYNC_MAP[doctype]

        doc = frappe.get_doc(doctype, name)

        payload = {
            field: doc.get(field)
            for field in cfg["fields"]
        }

        url = settings.base_url.rstrip("/") + cfg["path"]

        headers = {
            "Content-Type": "application/json",
        }

        token = settings.get_password("auth_token")

        if token:
            headers["Authorization"] = f"Bearer {token}"

        response = requests.post(
            url,
            data=json.dumps(payload, default=str),
            headers=headers,
            timeout=30,
        )

        response.raise_for_status()

        write_log(
            doctype=doctype,
            name=name,
            action=action,
            status="Success",
            response=response.text[:2000],
        )

        frappe.logger().info(
            f"RMS sync successful: {doctype} {name}"
        )

        return True

    except Exception as e:

        error_response = ""

        if "response" in locals() and response is not None:
            error_response = response.text[:2000]

        write_log(
            doctype=doctype,
            name=name,
            action=action,
            status="Failed",
            response=error_response,
            error=str(e),
        )

        frappe.logger().error(
            f"RMS sync failed: {doctype} {name}: {str(e)}"
        )

        return False


# ============================================================
# RMS SYNC LOG
# ============================================================

def write_log(
    doctype,
    name,
    action,
    status,
    response=None,
    error=None,
):
    """
    Create or update RMS Sync Log.
    """

    filters = {
        "ref_doctype": doctype,
        "ref_name": name,
        "action": action,
    }

    log_name = frappe.db.get_value(
        "RMS Sync Log",
        filters,
        "name",
    )

    if log_name:
        log = frappe.get_doc("RMS Sync Log", log_name)
    else:
        log = frappe.new_doc("RMS Sync Log")

    log.update({
        **filters,
        "status": status,
        "response": response,
        "error": error,
        "last_attempt": frappe.utils.now(),
    })

    log.save(ignore_permissions=True)


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

        # ----------------------------------------------------
        # METHOD 1: Serial numbers stored directly in row
        # ----------------------------------------------------

        raw_serials = row.get("serial_no") or ""

        if isinstance(raw_serials, str):

            serial_numbers.update(
                serial.strip()
                for serial in re.split(r"[\n,;]+", raw_serials)
                if serial.strip()
            )

        # ----------------------------------------------------
        # METHOD 2: Serial and Batch Bundle (ERPNext v15)
        # ----------------------------------------------------

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
# SYNCHRONIZE MANUFACTURED SERIAL NUMBERS
# ============================================================

def sync_manufactured_serial_nos(stock_entry_name):
    """
    Called automatically after Manufacture Stock Entry submission.

    Finds finished-product Serial Nos and sends them to RMS.
    """

    try:

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

            # Confirm Serial No exists.
            if not frappe.db.exists("Serial No", serial_no):

                frappe.log_error(
                    title="RMS Vehicle Sync",
                    message=(
                        f"Serial No {serial_no} does not exist. "
                        f"Stock Entry: {stock_entry_name}"
                    ),
                )

                continue

            # Check previous successful synchronization.
            existing_status = frappe.db.get_value(
                "RMS Sync Log",
                {
                    "ref_doctype": "Serial No",
                    "ref_name": serial_no,
                    "action": "create",
                },
                "status",
            )

            # Avoid duplicate successful synchronization.
            if existing_status == "Success":
                continue

            # Send vehicle to RMS.
            push(
                doctype="Serial No",
                name=serial_no,
                action="create",
            )

    except Exception:

        frappe.log_error(
            title="RMS Manufacture Vehicle Sync Error",
            message=frappe.get_traceback(),
        )

        raise


# ============================================================
# RETRY FAILED SYNCHRONIZATION
# ============================================================

def retry_failed():
    """
    Retry failed RMS API requests.
    """

    rows = frappe.get_all(
        "RMS Sync Log",
        filters={
            "status": "Failed",
        },
        fields=[
            "ref_doctype",
            "ref_name",
            "action",
        ],
        limit=50,
    )

    for row in rows:

        if row.ref_doctype not in SYNC_MAP:
            continue

        if frappe.db.exists(
            row.ref_doctype,
            row.ref_name,
        ):

            push(
                doctype=row.ref_doctype,
                name=row.ref_name,
                action=row.action,
            )


# ============================================================
# AUTOMATIC PENDING SERIAL NUMBER SYNCHRONIZATION
# ============================================================

def sync_pending_serial_nos(batch=100):
    """
    Safety-net synchronization for existing Serial Nos.

    Sends Serial Nos that do not have a successful RMS Sync Log.
    Useful for bulk-generated or previously missed vehicles.
    """

    settings = frappe.get_single("RMS Settings")

    if not settings.enabled:
        return

    done = frappe.get_all(
        "RMS Sync Log",
        filters={
            "ref_doctype": "Serial No",
            "status": "Success",
        },
        pluck="ref_name",
    )

    pending = frappe.get_all(
        "Serial No",
        filters={
            "name": ["not in", done or [""]],
        },
        pluck="name",
        order_by="creation asc",
        limit=batch,
    )

    for name in pending:

        success = push(
            doctype="Serial No",
            name=name,
            action="create",
        )

        # Stop if RMS is unavailable.
        if not success:
            break