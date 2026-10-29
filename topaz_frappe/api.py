"""Public API methods for Topaz Frappe.

Provides whitelisted methods for satellite microservices (FastAPI, Go, Spring Boot)
to validate and assign relations in Topaz.
"""

from __future__ import annotations

import frappe
import yaml
from frappe import _

from topaz_frappe.core.domain import TopazRelation
from topaz_frappe.transport.factory import get_transport


def _get_doctype_for_subject_type(subject_type: str) -> str | None:
	"""Map a Topaz subject type (e.g. user, committee, department) to Frappe DocType."""
	from topaz_frappe.core.registry import registry

	# Check standard built-ins first
	if subject_type == "user":
		return "User"

	# Look through registered DocTypes to find matching object type
	for dt, mapping in registry.all_mappings().items():
		if mapping.object_spec.object_type == subject_type:
			return dt

	return None


def _get_external_resource_definition(object_type: str) -> dict | None:
	"""Fetch and parse Topaz External Resource manifest fragment for an object type."""
	resource = frappe.db.get_value(
		"Topaz External Resource",
		{"resource_type": object_type, "enabled": 1},
		["name", "manifest_fragment"],
		as_dict=True,
	)
	if not resource:
		return None

	try:
		data = yaml.safe_load(resource.manifest_fragment)
		if not isinstance(data, dict):
			return None
		if object_type in data:
			return data[object_type]
		return data
	except Exception:
		return None


@frappe.whitelist()
def assign_relation(
	object_type: str,
	object_id: str,
	relation: str,
	subject_type: str,
	subject_id: str,
	subject_relation: str | None = None,
) -> dict:
	"""Validate DDL contract and core entity existence, then write tuple to Topaz.

	:param object_type: The external resource type (e.g. 'candidate')
	:param object_id: The local resource primary key (e.g. 'CAND-001')
	:param relation: The relation name (e.g. 'interview_committee')
	:param subject_type: The target subject type (e.g. 'committee', 'user')
	:param subject_id: The target subject identifier in Frappe (e.g. 'Order_Of_The_Phoenix')
	:param subject_relation: Optional subject relation for subject sets (e.g. 'member')
	"""
	if not object_type or not object_id or not relation or not subject_type or not subject_id:
		frappe.throw(
			_("All arguments (object_type, object_id, relation, subject_type, subject_id) are required.")
		)

	# 1. Validate object_type and relation against Topaz External Resource DDL
	res_def = _get_external_resource_definition(object_type)
	if not res_def:
		frappe.throw(
			_("Unregistered or disabled resource type '{0}'. Please register it in Topaz External Resource.").format(
				object_type
			)
		)

	relations_def = res_def.get("relations") or {}
	if relation not in relations_def:
		frappe.throw(
			_("Relation '{0}' is not defined for resource '{1}' in its manifest schema.").format(
				relation, object_type
			)
		)

	# Verify subject_type target compatibility from manifest fragment:
	# e.g. "interview_committee: committee | user"
	allowed_targets = [t.strip().split("#")[0] for t in str(relations_def[relation]).split("|")]
	if subject_type not in allowed_targets:
		frappe.throw(
			_(
				"Subject type '{0}' is not allowed for relation '{1}' on '{2}' (allowed: {3})."
			).format(subject_type, relation, object_type, ", ".join(allowed_targets))
		)

	# 2. Validate existence of subject_id in Frappe core directory
	target_doctype = _get_doctype_for_subject_type(subject_type)
	if target_doctype:
		if not frappe.db.exists(target_doctype, subject_id):
			frappe.throw(
				_("{0} '{1}' does not exist in the central directory.").format(
					target_doctype, subject_id
				)
			)

	# 3. Write tuple directly to Topaz REST API
	rel = TopazRelation(
		object_type=object_type,
		object_id=object_id,
		relation=relation,
		subject_type=subject_type,
		subject_id=subject_id,
		subject_relation=subject_relation,
	)
	transport = get_transport()
	transport.set_relation(rel)

	return {
		"success": True,
		"relation": {
			"object_type": object_type,
			"object_id": object_id,
			"relation": relation,
			"subject_type": subject_type,
			"subject_id": subject_id,
			"subject_relation": subject_relation,
		},
	}


@frappe.whitelist()
def remove_relation(
	object_type: str,
	object_id: str,
	relation: str,
	subject_type: str,
	subject_id: str,
	subject_relation: str | None = None,
) -> dict:
	"""Remove an assigned relation tuple from Topaz."""
	if not object_type or not object_id or not relation or not subject_type or not subject_id:
		frappe.throw(
			_("All arguments (object_type, object_id, relation, subject_type, subject_id) are required.")
		)

	rel = TopazRelation(
		object_type=object_type,
		object_id=object_id,
		relation=relation,
		subject_type=subject_type,
		subject_id=subject_id,
		subject_relation=subject_relation,
	)
	transport = get_transport()
	transport.delete_relation(rel)

	return {
		"success": True,
		"deleted": True,
	}
