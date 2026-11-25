app_name = "byky_app"
app_title = "Byky App"
app_publisher = "Byky"
app_description = "Byky ERP Management System"
app_email = "byky@gmail.com"
app_license = "mit"

# (The standard commented-out template sections of hooks.py were removed here.
#  Nothing in them was active. Add back any you use.)

fixtures = [
    # Custom DocTypes
    {"dt": "DocType", "filters": [["module", "=", "Byky App"], ["custom", "=", 1]]},

    # Custom Fields
    {
        "dt": "Custom Field"
    },

    # Property Setters
    {
        "dt": "Property Setter"
    },

    # Role
    {
        "dt": "Role",
        "filters": [
            ["name", "=", "Byky Role"]
        ]
    },

    # Custom Permissions
    {
        "dt": "Custom DocPerm",
        "filters": [
            ["role", "=", "Byky Role"]
        ]
    },

    # Workspaces
    {
        "dt": "Workspace",
        "filters": [
            ["name", "in", [
                "RMS",
                "Purchase",
                "Production",
                "Maintenance",
                "Scrap Sales",
                "Reports",
            ]]
        ]
    },

    # Print Formats
    {
        "dt": "Print Format",
        "filters": [
            ["standard", "=", "No"]
        ]
    },

    # Web Pages
    {
        "dt": "Web Page",
        "filters": [
            ["route", "=", "item-storage-location"]
        ]
    },

    # Client Scripts
    {
        "dt": "Client Script",
        "filters": [
            ["name", "in", [
                "Purchase Order Box wise Item fetching",
                "Stock Entry Item Location",
                "Vehicle Tear Down",
                "Service Entry",
                "Purchase Order -- Default  Warehouse",
                "Work Order - Auto Set Warehouse Fields",
                "Work Order Storage Location Auto Fill",
                "Stock Entry Storage Location from Work Order",
                "Item List Storage Button",
                "Work Order Storage Location Cascading Filter",
                "Item Storage Location Cascading Filter",
                "Work Order Form Storage Button",
                "Stock Entry Storage Location Cascading Filter",
                "Stock Entry - Storage Location Items Autofetch",
                "Storage Rack — filter by Warehouse, Floor, Area, and Shelf",
                "Shelf — filter by Warehouse, Floor, and Area",
                "Storage Area — filter by Warehouse and Floor",
                "Service Entry Serviced Items Cascading Filter",
                "Tear Down Serviced Items Cascading Filter",
                "Vehicle Tear Down - Auto Fetch Vehicle Details",
                "Service Request - Vehicle Details Auto Fetch",
                "Service Entry button for the Service Request form",
                "Service Entry - Submit Only When Completed",
                "Service Entry - Sync Serviced Items to Damaged Items",
                "Vehicle Tear Down Request - Create Tear Down Button",
                "Service Entry - Filter Spares by Box Items",
                "Service Entry - Vehicle Transfer",
                "Branch - Create Branch Warehouse",
                "Stock Entry List - Posting Date Range",
                "Purchase Order - Supplier Wise Items",
                "Hide Purchase Receipt Until Invoiced",
                "Receipt Items Only From Invoice",
                # "Purchase Invoice - Convert Finished Good to Boxes",
                "Stock Entry Box Unpacking",
                "Box Entry - Autofill From Purchase Invoice",
                "Purchase Invoice - Box Entry Defaults",
                "Purchase Invoice - Packaging Table",
                "Stock Entry - Box Name Popup",
                "Work Order - Item Storage Location Filters",
                "Purchase Order – Tax Percentage Display",
            ]]
        ]
    },
]

# ---------------------------------------------------------------------------
# Document Events  (ONE doc_events dict only: a second assignment would
# replace this one completely)
# ---------------------------------------------------------------------------
doc_events = {
    "Stock Entry": {
        "before_validate": [
            "byky_app.storage_location.copy_work_order_storage_locations",
            "byky_app.storage_location.auto_fill_transfer_locations",
            "byky_app.box_unpacking.allocate_rates",
            "byky_app.stock_entry.fill_box_name",
        ],
        "validate": [
            "byky_app.box_unpacking.validate_box_unpacking",
        ],
        "on_submit": [
            "byky_app.storage_location.update_item_storage_locations",
            "byky_app.stock_ledger_location.apply_locations_to_ledger",
            "byky_app.finished_item_storage.on_submit",
            "byky_app.rms_sync.on_manufacture_submit",
        ],
        "on_cancel": [
            "byky_app.storage_location.reverse_item_storage_locations",
            "byky_app.finished_item_storage.on_cancel",
        ],
    },

    "Service Entry": {
        "before_submit": "byky_app.service_entry_stock.check_submit_status",
        "on_update": "byky_app.service_entry_stock.sync_vehicle_transfer",
        "on_submit": [
            "byky_app.service_entry_stock.process_service_stock",
            "byky_app.autocreate_tear_down_request.create_tear_down_request",
            "byky_app.service_entry_stock.close_service_request",
        ],
        "on_cancel": [
            "byky_app.service_entry_stock.reverse_service_stock",
            "byky_app.service_entry_stock.reopen_service_request",
            "byky_app.service_entry_stock.reverse_vehicle_transfer",
        ],
        "on_trash": "byky_app.service_entry_stock.reverse_vehicle_transfer",
    },

    "Vehicle Tear Down": {
        "validate": "byky_app.vehicle_tear_down.validate_vehicle_tear_down",
        "on_update": "byky_app.vehicle_tear_down.close_tear_down_request",
        "on_submit": "byky_app.vehicle_tear_down.process_vehicle_tear_down",
        "on_cancel": [
            "byky_app.vehicle_tear_down.reverse_vehicle_tear_down",
            "byky_app.vehicle_tear_down.reopen_tear_down_request",
        ],
    },

    "Purchase Order": {
        "before_validate": "byky_app.finished_good.zero_value",
        "validate": "byky_app.item_tax.set_row_tax",
    },

    # one before_validate key only (a repeated key silently drops the first)
    "Purchase Receipt": {
        "before_validate": [
            "byky_app.finished_good.add_box_items",
            "byky_app.stock_entry.fill_box_name",
        ],
    },

    "Purchase Invoice": {
        "before_validate": "byky_app.item_tax.process_box_rows",
        "validate": [
            "byky_app.item_tax.set_row_tax",
            "byky_app.item_tax.fill_box_entries",
        ],
    },

    "Serial No": {
        "before_validate": "byky_app.serial_no_production_date.set_production_date",
    },

    # the RMS hooks are added to this one by the loop at the bottom of the block
    "Branch": {
        "after_insert": "byky_app.branch_warehouse.create_warehouse_for_branch",
    },

    "Category": {"on_update": "byky_app.category.sync_item_group"},
    "Box Entry": {"on_update": "byky_app.box_entry.sync_box_item"},
    "Vehicle Type": {"on_update": "byky_app.vehicle_type.sync_vehicle_item"},
    "Spares": {"on_update": "byky_app.spares.sync_spare_item"},
}

override_whitelisted_methods = {
    "erpnext.accounts.doctype.purchase_invoice.purchase_invoice.make_purchase_receipt":
        "byky_app.purchase_invoice.make_purchase_receipt",
}

# ---------------------------------------------------------------------------
# ERP TO RMS SYNC
# ---------------------------------------------------------------------------
# Master data synchronised to RMS (Serial No is NOT here: vehicles go through
# the Manufacture submit hook and the 5-minute sweep).
_RMS_MASTERS = [
    "Country",
    "State",
    "Branch Type",
    "Branch",
    "Event Location",
    "Warehouse",
    "Designation",
    "Employee",
    "Department",
    "Brand",
    "Item Group",
    "Item",
    "Asset",
]

_RMS_EVENTS = {
    "after_insert": "byky_app.rms_sync.on_insert",
    "on_update": "byky_app.rms_sync.on_update",
    "on_trash": "byky_app.rms_sync.on_trash",
}

# Add the RMS events to the doc_events above: merge, don't overwrite.
# Existing handlers run first (Branch: the warehouse is created BEFORE RMS is told).
for _doctype in _RMS_MASTERS:
    _events = doc_events.setdefault(_doctype, {})
    for _event, _handler in _RMS_EVENTS.items():
        _existing = _events.get(_event, [])
        _existing = [_existing] if isinstance(_existing, str) else list(_existing)
        _events[_event] = _existing + [_handler]


scheduler_events = {
    "cron": {
        "*/5 * * * *": [
            "byky_app.rms_sync.sync_pending_serial_nos",
            # Back-off starts at 5 minutes, so this runs every 5 minutes
            # (it only sends rows that are due).
            "byky_app.rms_sync.retry_failed",
        ],
    },
}