frappe.pages["item-storage-location"].on_page_load = function (wrapper) {

    const page = frappe.ui.make_app_page({
        parent: wrapper,
        title: "Item Count & Storage Location",
        single_column: true
    });

    // Render the HTML template (item_storage_location.html)
    $(frappe.render_template("item_storage_location", {})).appendTo(page.main);

    init_item_storage($(wrapper));
};


function init_item_storage($root) {

    // =========================================
    // ELEMENTS (looked up inside this page only)
    // =========================================

    const el = function (id) {
        return $root.find("#" + id)[0];
    };

    const search_input = el("item-search");
    const search_button = el("item-search-button");
    const clear_button = el("storage-filter-clear");
    const tbody = el("item-storage-results");
    const status = el("item-storage-status");

    if (!search_input || !search_button || !tbody || !status) {
        return;
    }


    // =========================================
    // FILTER CONFIGURATION
    // Column indexes match the HTML table.
    // =========================================

    const filter_fields = [
        { id: "filter-item-code", column: 0 },
        { id: "filter-item-name", column: 1 },
        { id: "filter-warehouse", column: 5 },
        { id: "filter-floor", column: 6 },
        { id: "filter-area", column: 7 },
        { id: "filter-shelf", column: 8 },
        { id: "filter-rack", column: 9 }
    ];


    // =========================================
    // DISPLAY MESSAGE IN TABLE
    // =========================================

    function show_table_message(message) {

        tbody.replaceChildren();

        const tr = document.createElement("tr");
        const td = document.createElement("td");

        td.colSpan = 10;
        td.className = "storage-empty";
        td.textContent = message;

        tr.appendChild(td);
        tbody.appendChild(tr);
    }


    // =========================================
    // READ CURRENT FILTER VALUES
    // =========================================

    function get_filter_values() {

        const filters = {};

        filter_fields.forEach(function (field) {

            const input = el(field.id);

            filters[field.column] = input
                ? input.value.trim().toLowerCase()
                : "";

        });

        return filters;
    }


    // =========================================
    // APPLY INSTANT FILTERING
    // =========================================

    function filter_storage_rows() {

        const filters = get_filter_values();

        const rows = tbody.querySelectorAll(
            'tr[data-storage-row="1"]'
        );

        let visible_count = 0;

        rows.forEach(function (row) {

            const cells = row.querySelectorAll("td");
            let matches = true;

            filter_fields.forEach(function (field) {

                const search_value = filters[field.column];

                if (!search_value || !matches) {
                    return;
                }

                const cell = cells[field.column];

                const cell_value = cell
                    ? cell.textContent.trim().toLowerCase()
                    : "";

                // Partial matching while typing.
                if (!cell_value.includes(search_value)) {
                    matches = false;
                }

            });

            row.style.display = matches ? "" : "none";

            if (matches) {
                visible_count++;
            }

        });


        // Update the record count.
        if (rows.length > 0) {

            status.textContent =
                visible_count +
                " matching storage-location record(s) found.";

            let empty_row = el("storage-filter-empty-row");

            if (visible_count === 0) {

                if (!empty_row) {

                    empty_row = document.createElement("tr");
                    empty_row.id = "storage-filter-empty-row";

                    const td = document.createElement("td");

                    td.colSpan = 10;
                    td.className = "storage-empty";
                    td.textContent = "No matching storage records found.";

                    empty_row.appendChild(td);
                    tbody.appendChild(empty_row);

                }

                empty_row.style.display = "";

            } else if (empty_row) {

                empty_row.remove();

            }

        }

    }


    // =========================================
    // LOAD STORAGE DATA FROM SERVER
    // =========================================

    function load_item_storage() {

        const search_text = search_input.value.trim();

        status.textContent = "Loading inventory...";

        search_button.disabled = true;

        show_table_message("Loading storage records...");

        frappe.call({

            method: "byky_app.item_storage_dashboard.get_item_storage_summary",

            args: {
                search_text: search_text
            },

            callback: function (response) {

                search_button.disabled = false;

                if (response.exc) {

                    status.textContent =
                        "Unable to load inventory. Check the server error log.";

                    show_table_message("Unable to load storage records.");

                    return;
                }

                const rows = response.message || [];

                tbody.replaceChildren();

                if (rows.length === 0) {

                    status.textContent = "No matching records found.";

                    show_table_message("No storage-location records found.");

                    return;
                }


                // Create table rows safely.
                rows.forEach(function (item) {

                    const tr = document.createElement("tr");

                    tr.dataset.storageRow = "1";

                    const values = [
                        item.item,
                        item.item_name,
                        item.total_quantity,
                        item.location_quantity,
                        item.uom,
                        item.warehouse,
                        item.floor,
                        item.area,
                        item.shelf,
                        item.rack
                    ];

                    values.forEach(function (value) {

                        const td = document.createElement("td");

                        td.textContent =
                            value === null || value === undefined
                                ? ""
                                : String(value);

                        tr.appendChild(td);

                    });

                    tbody.appendChild(tr);

                });


                // Apply active filters to newly loaded rows.
                filter_storage_rows();

            },

            error: function () {

                search_button.disabled = false;

                status.textContent =
                    "Request failed. Check login, permissions, and server logs.";

                show_table_message("Unable to load storage records.");

            }

        });

    }


    // =========================================
    // SEARCH BUTTON
    // =========================================

    search_button.addEventListener("click", function () {
        load_item_storage();
    });


    // Search when Enter is pressed.
    search_input.addEventListener("keydown", function (event) {

        if (event.key === "Enter") {

            event.preventDefault();

            load_item_storage();

        }

    });


    // =========================================
    // INSTANT FILTERING WHILE TYPING
    // =========================================

    filter_fields.forEach(function (field) {

        const input = el(field.id);

        if (input) {

            input.addEventListener("input", function () {

                filter_storage_rows();

            });

        }

    });


    // =========================================
    // CLEAR ALL FILTERS AND SEARCH
    // =========================================

    if (clear_button) {

        clear_button.addEventListener("click", function () {

            search_input.value = "";

            filter_fields.forEach(function (field) {

                const input = el(field.id);

                if (input) {
                    input.value = "";
                }

            });

            // Reload all available records.
            load_item_storage();

        });

    }


    // =========================================
    // INITIAL LOAD
    // =========================================

    load_item_storage();

}