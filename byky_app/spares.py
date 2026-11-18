from byky_app.item_sync import upsert_item


def sync_spare_item(doc, method=None):
    upsert_item(
        doc.spare_code,
        {
            "item_name": doc.spare_name,
            "item_group": doc.category,
            "brand": doc.brand,
            "stock_uom": doc.get("uom") or "Nos",
            "custom_subcategory": doc.sub_category,
            "custom_coo": doc.coc,
            "custom_status": doc.status,
            "custom_single_item": 1,
            "custom_item_type": "Spare Item",
        },
        has_serial_no=doc.has_serial_no,
        serial_series=doc.get("serial_no_series"),
        suppliers=doc.get("suppliers_list") or [],
    )