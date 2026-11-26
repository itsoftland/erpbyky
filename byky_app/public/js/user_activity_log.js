(function () {
	if (!frappe.msgprint || frappe._byky_msgprint_wrapped) return;
	frappe._byky_msgprint_wrapped = true;
	const original = frappe.msgprint;

	frappe.msgprint = function (msg, title) {
		try {
			const opts =
				msg && typeof msg === "object" && !Array.isArray(msg)
					? msg
					: { message: msg, title: title };

			if (opts.title === __("Missing Fields") && window.cur_frm && cur_frm.doc) {
				const html = String(opts.message || "").replace(/<\/li>/gi, "; ");
				const text = $("<div>").html(html).text().replace(/\s+/g, " ").trim();

				frappe.call({
					method: "byky_app.user_activity_log.log_client_error",
					type: "POST",
					args: {
						doctype: cur_frm.doctype,
						docname: cur_frm.docname,
						message: text,
					},
					freeze: false,
					silent: true,
				});
			}
		} catch (e) {
			console.error("BYKY activity log:", e);
		}
		return original.apply(this, arguments);
	};
})();