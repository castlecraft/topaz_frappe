"""Install / migrate lifecycle hooks for topaz_frappe."""

from __future__ import annotations

import frappe


def after_install():
	"""Warm the registry after installation and log the available mappings."""
	try:
		from topaz_frappe.core.registry import registry

		registry.reload()
		if doctypes := registry.doctypes():
			frappe.logger("topaz_frappe").info(
				"Topaz registry loaded for DocTypes: %s", ", ".join(sorted(doctypes))
			)
	except Exception:
		frappe.log_error(
			title="topaz_frappe after_install: registry warm-up failed",
			message=frappe.get_traceback(),
		)
