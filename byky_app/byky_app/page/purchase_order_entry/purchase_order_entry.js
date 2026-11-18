// frappe.pages['purchase-order-entry'].on_page_load = function (wrapper) {
//     const page = frappe.ui.make_app_page({
//         parent: wrapper,
//         title: 'Purchase Order Entry',
//         single_column: true
//     });
//     new POEntry(page);
// };

// class POEntry {
//     constructor(page) {
//         this.page = page;
//         this.tax_template = null;
//         this.make_form();
//         this.summary();
//         page.set_primary_action('Save Purchase Order', () => this.save());
//         page.set_secondary_action('New', () => window.location.reload());
//     }

//     make_form() {
//         this.form = new frappe.ui.FieldGroup({
//             parent: $(this.page.body),
//             fields: [
//                 { fieldtype: 'Section Break', label: '1. Purchase Order Information' },
//                 { fieldname: 'transaction_date', label: 'Purchase Order Date', fieldtype: 'Date',
//                   default: frappe.datetime.get_today(), reqd: 1 },
//                 { fieldname: 'schedule_date', label: 'Delivery Date', fieldtype: 'Date',
//                   default: frappe.datetime.get_today(), reqd: 1 },
//                 { fieldtype: 'Column Break' },
//                 { fieldname: 'set_warehouse', label: 'Delivery Warehouse', fieldtype: 'Link',
//                   options: 'Warehouse', reqd: 1 },
//                 { fieldname: 'shipping_address', label: 'Delivery Address', fieldtype: 'Link',
//                   options: 'Address' },

//                 { fieldtype: 'Section Break', label: '2. Supplier Information' },
//                 { fieldname: 'supplier', label: 'Supplier Code', fieldtype: 'Link',
//                   options: 'Supplier', reqd: 1, onchange: () => this.on_supplier() },
//                 { fieldname: 'supplier_name', label: 'Supplier Name', fieldtype: 'Data', read_only: 1 },
//                 { fieldtype: 'Column Break' },
//                 { fieldname: 'country', label: 'Supplier Country', fieldtype: 'Data', read_only: 1 },
//                 { fieldname: 'currency', label: 'Supplier Currency', fieldtype: 'Link',
//                   options: 'Currency', reqd: 1, onchange: () => this.get_rate() },
//                 { fieldname: 'tax_pct', label: 'Supplier Country Tax %', fieldtype: 'Percent',
//                   read_only: 1, description: 'Taken from the Tax Rule / Purchase Taxes template',
//                   onchange: () => this.summary() },

//                 { fieldtype: 'Section Break', label: '3. Exchange Rate' },
//                 { fieldname: 'conversion_rate', label: '1 Supplier Currency = AED', fieldtype: 'Float',
//                   precision: 4, reqd: 1, onchange: () => this.summary() },

//                 { fieldtype: 'Section Break', label: '4. Item Details' },
//                 { fieldname: 'items', fieldtype: 'Table', label: 'Items', data: [],
//                   fields: [
//                     { fieldname: 'item_code', label: 'Item', fieldtype: 'Link', options: 'Item',
//                       in_list_view: 1, reqd: 1, columns: 4 },
//                     { fieldname: 'qty', label: 'Quantity', fieldtype: 'Float',
//                       in_list_view: 1, reqd: 1, columns: 2 },
//                     { fieldname: 'rate', label: 'Price (Supplier Currency)', fieldtype: 'Float',
//                       in_list_view: 1, reqd: 1, columns: 3 }
//                   ] },

//                 { fieldtype: 'Section Break', label: '5. Order Summary' },
//                 { fieldname: 'summary_html', fieldtype: 'HTML' }
//             ]
//         });
//         this.form.make();
//         this.form.fields_dict.items.grid.wrapper.on('change', () => this.summary());
//     }

//     on_supplier() {
//         const s = this.form.get_value('supplier');
//         if (!s) return;
//         frappe.db.get_value('Supplier', s, ['supplier_name', 'country', 'default_currency'])
//             .then(r => {
//                 const d = r.message || {};
//                 this.form.set_value('supplier_name', d.supplier_name);
//                 this.form.set_value('country', d.country);
//                 if (d.default_currency) this.form.set_value('currency', d.default_currency);
//                 this.fetch_tax(s);
//             });
//     }

//     fetch_tax(supplier) {
//         frappe.call({
//             method: 'byky_app.api.purchase_order.get_supplier_tax',
//             args: {
//                 supplier: supplier,
//                 posting_date: this.form.get_value('transaction_date')
//             },
//             callback: r => {
//                 const d = r.message || {};
//                 this.tax_template = d.template || null;
//                 this.form.set_value('tax_pct', flt(d.rate));
//                 this.summary();
//             }
//         });
//     }

//     get_rate() {
//         const cur = this.form.get_value('currency');
//         if (!cur) return;
//         frappe.call({
//             method: 'erpnext.setup.utils.get_exchange_rate',
//             args: {
//                 from_currency: cur,
//                 to_currency: frappe.boot.sysdefaults.currency,
//                 transaction_date: this.form.get_value('transaction_date')
//             },
//             callback: r => this.form.set_value('conversion_rate', r.message)
//         });
//     }

//     summary() {
//         const v = this.form.get_values(true) || {};
//         const cr = flt(v.conversion_rate) || 1;
//         const pct = flt(v.tax_pct);
//         const taxable = (v.items || []).reduce((a, r) => a + flt(r.qty) * flt(r.rate), 0);
//         const tax = taxable * pct / 100;
//         const f = n => format_number(n, null, 2);
//         this.form.get_field('summary_html').$wrapper.html(`
//             <table class="table table-bordered">
//               <tr><th></th><th>${v.currency || ''}</th><th>AED</th></tr>
//               <tr><td>Total Taxable Amount</td><td>${f(taxable)}</td><td>${f(taxable * cr)}</td></tr>
//               <tr><td>Total Tax Amount</td><td>${f(tax)}</td><td>${f(tax * cr)}</td></tr>
//               <tr><th>Grand Total (Net Amount)</th><th>${f(taxable + tax)}</th><th>${f((taxable + tax) * cr)}</th></tr>
//             </table>`);
//     }

//     save() {
//         const values = this.form.get_values();
//         if (!values) return;
//         if (!(values.items || []).length) {
//             frappe.msgprint('Please add at least one item.');
//             return;
//         }
//         values.tax_template = this.tax_template;

//         frappe.call({
//             method: 'byky_app.api.purchase_order.create_purchase_order',
//             args: { data: values },
//             freeze: true,
//             callback: r => {
//                 if (!r.message) return;
//                 const po = r.message;
//                 frappe.show_alert({ message: `Purchase Order ${po} created`, indicator: 'green' });
//                 this.page.clear_inner_toolbar();
//                 this.page.add_inner_button('Open PO', () =>
//                     frappe.set_route('Form', 'Purchase Order', po));
//                 this.page.add_inner_button('Create Purchase Invoice', () => {
//                     frappe.model.open_mapped_doc({
//                         method: 'erpnext.buying.doctype.purchase_order.purchase_order.make_purchase_invoice',
//                         source_name: po
//                     });
//                 });
//             }
//         });
//     }
// }