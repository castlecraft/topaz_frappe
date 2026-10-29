"""Topaz Manifest compiler and stitcher.

Combines the base manifest (from core apps or hooks) with external resource
manifest fragments registered via `Topaz External Resource`.
"""

from __future__ import annotations

import os
from typing import Any

import frappe
import yaml


def get_base_manifest_dict(app: str | None = None, file_path: str | None = None) -> dict[str, Any]:
	"""Load and parse the base manifest YAML.

	Falls back to eduauth_frappe/topaz/manifest.yaml or site hook if unspecified.
	"""
	target_path = file_path
	if not target_path:
		app_name = app or "eduauth_frappe"
		try:
			target_path = frappe.get_app_path(app_name, "topaz", "manifest.yaml")
		except Exception:
			target_path = None

	if not target_path or not os.path.exists(target_path):
		# Default minimal Zanzibar model
		return {
			"model": {"version": 3},
			"types": {
				"user": {},
			},
		}

	with open(target_path) as f:
		data = yaml.safe_load(f) or {}
	return data


def stitch_manifest(
	base_app: str | None = None,
	base_file: str | None = None,
	dry_run_fragment: dict[str, Any] | None = None,
) -> str:
	"""Stitch core manifest with all enabled Topaz External Resources.

	:param base_app: Base app containing topaz/manifest.yaml (default: eduauth_frappe).
	:param base_file: Explicit path to base manifest.yaml.
	:param dry_run_fragment: Optional single {resource_type: fragment_dict} to include
	    during validation before saving.
	:returns: Compiled YAML string ready for Topaz set_manifest.
	"""
	base_dict = get_base_manifest_dict(app=base_app, file_path=base_file)
	if "types" not in base_dict or not isinstance(base_dict["types"], dict):
		base_dict["types"] = {}

	# Load all enabled Topaz External Resource records
	try:
		records = frappe.get_all(
			"Topaz External Resource",
			filters={"enabled": 1},
			fields=["name", "resource_type", "manifest_fragment"],
		)
	except Exception:
		# Database table might not be migrated yet
		records = []

	for rec in records:
		# If we are dry-running an update for this resource, dry_run_fragment takes precedence
		if dry_run_fragment and rec.resource_type in dry_run_fragment:
			continue

		fragment_text = rec.manifest_fragment or ""
		if not fragment_text.strip():
			continue

		fragment_data = yaml.safe_load(fragment_text)
		if not isinstance(fragment_data, dict):
			continue

		# A fragment can be:
		# 1) { "candidate": { "relations": ..., "permissions": ... } }
		# 2) { "relations": ..., "permissions": ... } (resource_type is the key)
		if rec.resource_type in fragment_data:
			base_dict["types"][rec.resource_type] = fragment_data[rec.resource_type]
		elif "relations" in fragment_data or "permissions" in fragment_data:
			base_dict["types"][rec.resource_type] = fragment_data
		else:
			# Multiple types declared inside fragment
			for k, v in fragment_data.items():
				base_dict["types"][k] = v

	if dry_run_fragment:
		for k, v in dry_run_fragment.items():
			base_dict["types"][k] = v

	# Return formatted YAML string
	return yaml.dump(base_dict, sort_keys=False)


def apply_stitched_manifest(base_app: str | None = None, base_file: str | None = None) -> bool:
	"""Stitch manifest and apply it to Topaz PDP."""
	from topaz_frappe.config.settings import is_topaz_enabled
	from topaz_frappe.transport.factory import get_transport

	if not is_topaz_enabled():
		return False

	stitched_yaml = stitch_manifest(base_app=base_app, base_file=base_file)
	transport = get_transport()
	transport.set_manifest(stitched_yaml)
	return True
