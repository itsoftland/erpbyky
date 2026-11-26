import json

import frappe
import frappe.desk.form.save as form_save
from frappe.model import no_value_fields, table_fields
from frappe.utils import cstr, now_datetime, strip_html

LOG_DOCTYPE = "BYKY User Activity Log"
MAX_LEN = 50000

# System / noisy / log-type DocTypes that should never be audited
EXCLUDED_DOCTYPES = {
    LOG_DOCTYPE,
    "Version", "Activity Log", "Access Log", "View Log", "Route History",
    "Comment", "Communication", "Error Log", "Scheduled Job Log",
    "Email Queue", "Email Queue Recipient", "Notification Log",
    "Deleted Document", "Prepared Report", "RQ Job", "Data Import Log",
    "Console Log", "Integration Request", "Webhook Request Log",
    "Submission Queue", "Document Follow", "Energy Point Log", "Tag Link",
    "Unhandled Email", "Session Default", "Bulk Update", "Event Update Log",
    "RMS Sync Log",
}

# Standard fields that change on every save and carry no audit value
IGNORED_FIELDS = {
    "modified", "modified_by", "creation", "owner", "idx", "docstatus",
    "_user_tags", "_comments", "_assign", "_liked_by", "_seen",
}


# --------------------------------------------------------------------------
# doc_events hook entry points (hooks.py -> doc_events["*"])
# --------------------------------------------------------------------------
def on_create(doc, method=None):
    if not _should_log(doc):
        return
    _insert_log(
        doc, "Create", "Created {0} {1}".format(doc.doctype, doc.name),
        None, _snapshot(doc),
    )


def on_update(doc, method=None):
    if not _should_log(doc):
        return
    # Insert is logged by on_create; submit is logged by on_submit
    if doc.flags.in_insert or getattr(doc, "_action", None) == "submit":
        return
    _log_changes(doc)


def on_update_after_submit(doc, method=None):
    if not _should_log(doc):
        return
    _log_changes(doc)


def on_submit(doc, method=None):
    if not _should_log(doc):
        return
    _insert_log(
        doc, "Submit", "Submitted {0} {1}".format(doc.doctype, doc.name),
        {"docstatus": 0}, {"docstatus": 1},
    )


def on_cancel(doc, method=None):
    if not _should_log(doc):
        return
    _insert_log(
        doc, "Cancel", "Cancelled {0} {1}".format(doc.doctype, doc.name),
        {"docstatus": 1}, {"docstatus": 2},
    )


def on_trash(doc, method=None):
    if not _should_log(doc):
        return
    _insert_log(
        doc, "Delete", "Deleted {0} {1}".format(doc.doctype, doc.name),
        _snapshot(doc), None,
    )


# --------------------------------------------------------------------------
# Helpers for document events
# --------------------------------------------------------------------------
def _should_log(doc):
    flags = frappe.flags
    if flags.in_install or flags.in_migrate or flags.in_patch or flags.in_import:
        return False
    if doc.doctype in EXCLUDED_DOCTYPES:
        return False
    if doc.meta.istable:
        return False
    if doc.flags.get("ignore_user_activity_log"):
        return False
    return True


def _log_changes(doc):
    old_doc = doc.get_doc_before_save()
    if not old_doc:
        return

    old_vals, new_vals = {}, {}
    for df in doc.meta.fields:
        fn = df.fieldname
        if fn in IGNORED_FIELDS:
            continue
        if df.fieldtype in no_value_fields and df.fieldtype not in table_fields:
            continue

        if df.fieldtype in table_fields:
            old_rows = _rows(old_doc.get(fn))
            new_rows = _rows(doc.get(fn))
            if old_rows != new_rows:
                old_vals[fn], new_vals[fn] = old_rows, new_rows
            continue

        old_v, new_v = old_doc.get(fn), doc.get(fn)
        if cstr(old_v) == cstr(new_v):
            continue
        if df.fieldtype == "Password":
            old_v, new_v = "******", "******"
        old_vals[fn], new_vals[fn] = old_v, new_v

    if not new_vals:
        return

    _insert_log(
        doc, "Update",
        "Updated {0} {1}: {2}".format(doc.doctype, doc.name, ", ".join(new_vals)),
        old_vals, new_vals,
    )


def _rows(rows):
    return [
        r.as_dict(no_default_fields=True, convert_dates_to_str=True)
        for r in (rows or [])
    ]


def _snapshot(doc):
    data = {}
    for df in doc.meta.fields:
        fn = df.fieldname
        if fn in IGNORED_FIELDS:
            continue
        if df.fieldtype in no_value_fields and df.fieldtype not in table_fields:
            continue
        if df.fieldtype in table_fields:
            rows = _rows(doc.get(fn))
            if rows:
                data[fn] = rows
            continue
        val = doc.get(fn)
        if val in (None, ""):
            continue
        data[fn] = "******" if df.fieldtype == "Password" else val
    return data


def _dump(value):
    if value is None:
        return None
    return json.dumps(value, default=str, indent=1, ensure_ascii=False)[:MAX_LEN]


def _insert_log(doc, action, description, old_value, new_value):
    try:
        log = frappe.get_doc({
            "doctype": LOG_DOCTYPE,
            "user": frappe.session.user,
            "action": action,
            "status": "Success",
            "reference_doctype": doc.doctype,
            "reference_document": doc.name,
            "description": description[:2000],
            "old_value": _dump(old_value),
            "new_value": _dump(new_value),
            "ip_address": getattr(frappe.local, "request_ip", None),
            "date_time": now_datetime(),
        })
        log.flags.ignore_user_activity_log = True
        log.insert(ignore_permissions=True, ignore_links=True)
    except Exception:
        # Auditing must never block the user's actual save
        frappe.log_error(frappe.get_traceback(), "BYKY User Activity Log failed")


# --------------------------------------------------------------------------
# Error logging (validation / "Missing Fields" messages)
# --------------------------------------------------------------------------
def _write_error_log(doctype, name, message, action="Update", status="Error"):
    log = frappe.get_doc({
        "doctype": LOG_DOCTYPE,
        "user": frappe.session.user,
        "action": action,
        "status": status,
        "reference_doctype": doctype,
        "reference_document": name or None,
        "description": "{0} {1}: {2}".format(
            doctype, name or "(new, unsaved)", (message or "")[:2000]
        ),
        "ip_address": getattr(frappe.local, "request_ip", None),
        "date_time": now_datetime(),
    })
    log.flags.ignore_user_activity_log = True
    log.insert(ignore_permissions=True, ignore_links=True)


@frappe.whitelist()
def log_client_error(doctype, docname=None, message=None):
    """Called from the browser (public/js/user_activity_log.js) when the
    'Missing Fields' dialog is shown, since that check never reaches the server."""
    if frappe.session.user == "Guest" or doctype in EXCLUDED_DOCTYPES:
        return
    if not frappe.db.exists("DocType", doctype):
        return
    is_new = not docname or str(docname).startswith("new-")
    _write_error_log(
        doctype,
        None if is_new else docname,
        strip_html(message or ""),
        action="Create" if is_new else "Update",
    )


def _error_text(exc):
    parts = []
    for m in frappe.local.message_log or []:
        try:
            m = json.loads(m) if isinstance(m, str) else m
            if m.get("message"):
                parts.append(m["message"])
        except Exception:
            pass
    return strip_html(" ".join(parts)) if parts else str(exc)


@frappe.whitelist()
def savedocs(doc, action):
    """Wraps the standard form save so server-side validation errors get logged.
    Registered in hooks.py via override_whitelisted_methods."""
    try:
        return form_save.savedocs(doc, action)
    except (frappe.ValidationError, frappe.PermissionError) as e:
        try:
            d = frappe.parse_json(doc)
            doctype = d.get("doctype")
            if doctype and doctype not in EXCLUDED_DOCTYPES:
                is_new = bool(d.get("__islocal"))
                name = None if is_new else d.get("name")
                if action == "Save":
                    log_action = "Create" if is_new else "Update"
                else:  # Submit / Update (after submit) / Cancel
                    log_action = action if action in ("Submit", "Cancel") else "Update"
                text = _error_text(e)
                # The request fails anyway; roll back first so the log row
                # is not lost with the failed transaction, then commit it.
                frappe.db.rollback()
                _write_error_log(doctype, name, text, action=log_action)
                frappe.db.commit()
        except Exception:
            frappe.log_error(frappe.get_traceback(), "BYKY User Activity Log failed")
        raise