
import frappe
from frappe import _
from frappe.model.document import Document
from frappe.model.mapper import get_mapped_doc


class VehicleTearDownRequest(Document):
    pass


# ==============================================================
# AUTOMATICALLY CREATE VEHICLE TEAR DOWN REQUEST
# ==============================================================

def create_tear_down_request(doc, method=None):
    """
    Automatically create a Vehicle Tear Down Request when
    Service Entry status is Non-repairable.

    Prevent duplicate requests using the Service Entry link.
    """

    # Only process non-repairable vehicles
    if doc.service_status != "Non-repairable":
        return

    if not doc.name:
        frappe.throw(_("Service Entry ID is missing."))

    if not doc.vehicle_number:
        frappe.throw(_("Please select a Vehicle Serial No."))

    if not doc.item:
        frappe.throw(_("Vehicle Item is missing."))

    # Check whether a request already exists for this Service Entry
    existing_request = frappe.db.get_value(
        "Vehicle Tear Down Request",
        {"service_id": doc.name},
        "name"
    )

    if existing_request:
        # Keep the existing request; do not create a duplicate.
        # Update vehicle details if they have changed.
        request = frappe.get_doc(
            "Vehicle Tear Down Request",
            existing_request
        )

        changed = False

        if request.vehicle_number != doc.vehicle_number:
            request.vehicle_number = doc.vehicle_number
            changed = True

        if request.item != doc.item:
            request.item = doc.item
            changed = True

        if changed:
            request.save(ignore_permissions=True)

        return existing_request

    # Create a new request
    request = frappe.new_doc("Vehicle Tear Down Request")

    request.service_id = doc.name
    request.vehicle_number = doc.vehicle_number
    request.item = doc.item
    request.status = "Open"

    request.insert(ignore_permissions=True)

    frappe.msgprint(
        _("Vehicle Tear Down Request {0} created successfully.")
        .format(request.name),
        alert=True,
        indicator="green"
    )

    return request.name


# ==============================================================
# MAP VEHICLE TEAR DOWN REQUEST TO VEHICLE TEAR DOWN
# ==============================================================

@frappe.whitelist()
def make_vehicle_tear_down(source_name, target_doc=None):

    def set_missing_values(source, target):

        # Fetch vehicle details
        target.vehicle_serial_no = source.vehicle_number
        target.item = source.item

        # Map Service Entry to Vehicle Service
        target.vehicle_service = source.service_id

        # Link the original Tear Down Request
        if target.meta.has_field("tear_down_request"):
            target.tear_down_request = source.name

    doc = get_mapped_doc(
        "Vehicle Tear Down Request",
        source_name,
        {
            "Vehicle Tear Down Request": {
                "doctype": "Vehicle Tear Down",
                "field_map": {
                    "vehicle_number": "vehicle_serial_no",
                    "item": "item",
                    "service_id": "vehicle_service"
                }
            }
        },
        target_doc,
        set_missing_values
    )

    # Ensure the Service Entry is populated
    source_service = frappe.db.get_value(
        "Vehicle Tear Down Request",
        source_name,
        "service_id"
    )

    doc.vehicle_service = source_service

    return doc
