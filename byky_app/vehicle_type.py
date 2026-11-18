from byky_app.item_sync import upsert_item


def sync_vehicle_item(doc, method=None):
    upsert_item(
        doc.vehicle_type_code,
        {
            "item_name": doc.vehicle_type,
            "item_group": doc.category,
            "brand": doc.brand,
            "stock_uom": doc.get("uom") or "Nos",
            "custom_subcategory": doc.sub_category,
            "custom_coo": doc.coc,
            "custom_status": doc.status,
            "custom_finished_good": 1,
            "custom_item_type": "Finished Item",
        },
        has_serial_no=doc.has_serial_no,
        serial_series=doc.get("serial_no_series"),
        suppliers=doc.get("supplier_list") or [],
        child_tables={"custom_box_items": doc.get("spare_items")},
    )