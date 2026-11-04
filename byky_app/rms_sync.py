# import json

# import frappe
# import requests

# # doctype -> web endpoint path and the fields sent
# SYNC_MAP = {
#     "Country": {"path": "/api/country", "fields": ["name", "country_name", "code"]},
#     "State": {"path": "/api/state", "fields": ["name", "state_name", "country", "state_code"]},
#     "Branch Type": {"path": "/api/branch-type", "fields": ["name", "branch_type", "branch_type_code"]},
#     "Branch": {"path": "/api/branch", "fields": ["name", "branch", "branch_type", "state", "warehouse"]},
#     "Event Location": {"path": "/api/event-location", "fields": [
#         "name", "event_location", "state", "branch", "status",
#         "contact_person_name", "contact_person_phone_no", "address",
#     ]},
#     "Warehouse": {"path": "/api/warehouse", "fields": [
#         "name", "warehouse_name", "warehouse_type", "parent_warehouse", "is_group", "disabled",
#     ]},
#     "Designation": {"path": "/api/designation", "fields": ["name", "designation_name", "description"]},
#     "Employee": {"path": "/api/employee", "fields": [
#         "name", "employee_name", "first_name", "middle_name", "last_name",
#         "gender", "date_of_birth", "date_of_joining", "status",
#         "department", "designation", "branch", "reports_to", "employment_type",
#         "cell_number", "personal_email", "company_email", "user_id",
#     ]},
#     "Department": {"path": "/api/department", "fields": [
#         "name", "department_name", "parent_department", "is_group",
#     ]},
#     "Brand": {"path": "/api/brand", "fields": ["name", "brand", "description"]},
#     "Item Group": {"path": "/api/item-group", "fields": [
#         "name", "item_group_name", "parent_item_group", "is_group",
#     ]},
#     "Item": {"path": "/api/item", "fields": [
#         "name", "item_code", "item_name", "item_group", "brand", "stock_uom",
#         "has_serial_no", "serial_no_series", "disabled", "description",
#     ]},
#     "Serial No": {"path": "/api/vehicle", "fields": [
#         "name", "serial_no", "item_code", "item_name", "description", "warehouse", "status",
#         "purchase_date", "supplier", "warranty_expiry_date", "warranty_period", "amc_expiry_date",
#     ]},
#     "Asset": {"path": "/api/asset", "fields": [
#         "name", "asset_name", "item_code", "asset_category", "location", "status", "purchase_date",
#     ]},
# }


# def on_insert(doc, method=None):
#     frappe.enqueue(
#         "byky_app.rms_sync.push",
#         doctype=doc.doctype,
#         name=doc.name,
#         action="create",
#         queue="short",
#         enqueue_after_commit=True,
#     )


# def push(doctype, name, action="create"):
#     settings = frappe.get_single("RMS Settings")
#     if not settings.enabled:
#         return False

#     cfg = SYNC_MAP[doctype]
#     doc = frappe.get_doc(doctype, name)
#     payload = {f: doc.get(f) for f in cfg["fields"]}

#     url = settings.base_url.rstrip("/") + cfg["path"]
#     headers = {
#         "Content-Type": "application/json",
#         "Authorization": f"Bearer {settings.get_password('auth_token')}",
#     }

#     try:
#         r = requests.post(url, data=json.dumps(payload, default=str), headers=headers, timeout=20)
#         r.raise_for_status()
#         write_log(doctype, name, action, "Success", response=r.text[:2000])
#         return True
#     except Exception as e:
#         resp = getattr(getattr(e, "response", None), "text", "") or ""
#         write_log(doctype, name, action, "Failed", response=resp[:2000], error=str(e))
#         return False


# def write_log(doctype, name, action, status, response=None, error=None):
#     filters = {"ref_doctype": doctype, "ref_name": name, "action": action}
#     log_name = frappe.db.get_value("RMS Sync Log", filters)
#     log = frappe.get_doc("RMS Sync Log", log_name) if log_name else frappe.new_doc("RMS Sync Log")
#     log.update({**filters, "status": status, "response": response,
#                 "error": error, "last_attempt": frappe.utils.now()})
#     log.save(ignore_permissions=True)
#     frappe.db.commit()


# def retry_failed():
#     rows = frappe.get_all(
#         "RMS Sync Log", filters={"status": "Failed"},
#         fields=["ref_doctype", "ref_name", "action"], limit=50,
#     )
#     for r in rows:
#         if frappe.db.exists(r.ref_doctype, r.ref_name):
#             push(r.ref_doctype, r.ref_name, r.action)


# def sync_pending_serial_nos(batch=100):
#     """Send Serial Nos that have no Success row yet (bulk-created ones skip hooks)."""
#     settings = frappe.get_single("RMS Settings")
#     if not settings.enabled:
#         return

#     done = frappe.get_all(
#         "RMS Sync Log",
#         filters={"ref_doctype": "Serial No", "status": "Success"},
#         pluck="ref_name",
#     )
#     pending = frappe.get_all(
#         "Serial No",
#         filters={"name": ["not in", done or [""]]},
#         pluck="name",
#         order_by="creation asc",
#         limit=batch,
#     )
#     for name in pending:
#         if not push("Serial No", name, "create"):
#             break  # web app unreachable: stop and try again on the next run