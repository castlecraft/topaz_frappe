# Copyright (c) 2026, Castlecraft Ecommerce Pvt. Ltd. and contributors
# For license information, please see license.txt

from __future__ import annotations

import frappe
import yaml
from frappe import _
from frappe.model.document import Document


class TopazExternalResource(Document):
	def validate(self):
		self.validate_manifest_syntax()
		self.validate_with_topaz()

	def validate_manifest_syntax(self):
		if not self.manifest_fragment:
			frappe.throw(_("Manifest fragment cannot be empty."))

		try:
			parsed = yaml.safe_load(self.manifest_fragment)
		except Exception as exc:
			frappe.throw(_("Invalid YAML in manifest fragment: {0}").format(str(exc)))

		if not isinstance(parsed, dict):
			frappe.throw(_("Manifest fragment must evaluate to a YAML dictionary mapping."))

		# Verify that the resource_type is represented
		has_type = self.resource_type in parsed or "relations" in parsed or "permissions" in parsed
		if not has_type:
			frappe.throw(
				_("Manifest fragment must define relations/permissions for resource '{0}'.").format(
					self.resource_type
				)
			)

	def validate_with_topaz(self):
		"""Dry-run validation against Topaz PDP to verify relation graph integrity."""
		from topaz_frappe.config.settings import is_topaz_enabled
		from topaz_frappe.core.manifest import stitch_manifest
		from topaz_frappe.transport.factory import get_transport

		if not is_topaz_enabled():
			return

		parsed = yaml.safe_load(self.manifest_fragment)
		fragment_dict = {}
		if self.resource_type in parsed:
			fragment_dict[self.resource_type] = parsed[self.resource_type]
		elif "relations" in parsed or "permissions" in parsed:
			fragment_dict[self.resource_type] = parsed
		else:
			fragment_dict = parsed

		try:
			candidate_yaml = stitch_manifest(dry_run_fragment=fragment_dict)
			transport = get_transport()
			# Validate with Topaz PDP by uploading candidate schema
			transport.set_manifest(candidate_yaml)
		except Exception as exc:
			frappe.throw(
				_("Topaz rejected manifest schema for resource '{0}': {1}").format(
					self.resource_type, str(exc)
				)
			)

	def on_trash(self):
		# Re-stitch after removal
		frappe.db.after_commit(self.sync_manifest)

	def on_update(self):
		frappe.db.after_commit(self.sync_manifest)

	@staticmethod
	def sync_manifest():
		from topaz_frappe.core.manifest import apply_stitched_manifest

		try:
			apply_stitched_manifest()
		except Exception:
			frappe.log_error(title="Failed to apply stitched Topaz manifest")
