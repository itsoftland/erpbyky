import frappe
from frappe.model.document import Document
from frappe.model.mapper import get_mapped_doc


class VehicleTearDownRequest(Document):
    pass


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