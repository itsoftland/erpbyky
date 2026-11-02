import frappe


def execute():
    frappe.db.create_sequence("Vehicle Tear Down", check_not_exists=True)