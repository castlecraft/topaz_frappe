# Copyright (c) 2024, Castlecraft Ecommerce Pvt. Ltd. and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document
from frappe.query_builder import Interval
from frappe.query_builder.functions import Now


class TopazSyncLog(Document):
	@staticmethod
	def clear_old_logs(days=30):
		table = frappe.qb.DocType("Topaz Sync Log")
		frappe.db.delete(
			table,
			filters=(table.expiration_time < (Now() - Interval(days=days))),
		)
