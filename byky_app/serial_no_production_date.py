"""
byky_app/serial_no_production_date.py

Autofills Serial No -> custom_production_date when a Serial No is created.

Date priority:
  1. Value already set by the user (never overwritten)
  2. posting_date of the source document (Stock Entry / Purchase Receipt ...)
  3. Today
"""

import frappe
from frappe.utils import today


def set_production_date(doc, method=None):
    # Keep any value the user (or an import) already entered
    if doc.get("custom_production_date"):
        return

    posting_date = None

    doc_type = doc.get("purchase_document_type")
    doc_name = doc.get("purchase_document_no")

    if doc_type and doc_name:
        try:
            posting_date = frappe.db.get_value(doc_type, doc_name, "posting_date")
        except Exception:
            # Source doctype has no posting_date field - fall back to today
            posting_date = None

    doc.custom_production_date = posting_date or today()