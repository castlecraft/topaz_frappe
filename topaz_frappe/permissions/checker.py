"""Topaz-backed authorization checks for ``has_permission``.

Maps Frappe permission types onto Topaz relations and queries the directory
``Check`` API. Results are memoised per request in
``frappe.local._topaz_perm_cache`` to prevent duplicate HTTP round-trips.

Bypass rules (in order):

* ``Administrator`` / ``Guest`` handling.
* ``frappe.flags.ignore_permissions``.
* DocTypes not registered with Topaz -> defer to Frappe (return ``None`` so the
  caller can fall back). The hook wrapper treats ``None`` as "no opinion".
"""

from __future__ import annotations

from typing import Optional

import frappe

from topaz_frappe.config.settings import get_topaz_settings, is_topaz_enabled
from topaz_frappe.core.registry import registry
from topaz_frappe.transport.factory import get_transport

__all__ = [
	"ACTION_RELATION_MAP",
	"custom_has_permission",
	"resolve_relation",
	"resolve_relations",
	"resolve_subject_id",
]

#: Default mapping from Frappe ``ptype`` to Topaz relation name. Per-DocType
#: overrides (``DocTypeMapping.permission_relations``) take precedence; this is
#: only the fallback. Map any relation names your Topaz schema defines.
ACTION_RELATION_MAP: dict[str, str] = {
	"read": "viewer",
	"write": "editor",
	"create": "editor",
	"delete": "admin",
	"submit": "editor",
	"cancel": "admin",
	"amend": "editor",
	"select": "viewer",
	"report": "viewer",
	"export": "viewer",
	"share": "admin",
	"print": "viewer",
	"email": "viewer",
}

_CACHE_ATTR = "_topaz_perm_cache"


def _get_perm_cache() -> dict:
	cache = getattr(frappe.local, _CACHE_ATTR, None)
	if cache is None:
		cache = {}
		setattr(frappe.local, _CACHE_ATTR, cache)
	return cache


def resolve_subject_id(user: str | None = None) -> str | None:
	"""Return the Topaz subject id for a Frappe user.

	Defaults to the user's ``name`` (email). ``user`` may return a custom id by
	some apps; override here if needed.
	"""
	user = user or frappe.session.user
	if not user or user in ("Guest", "Administrator"):
		return None
	return user


def resolve_relations(doctype: str, ptype: str) -> list[str]:
	"""Resolve the list of Topaz relations that grant ``ptype`` for ``doctype``.

	Per-DocType ``permission_relations`` overrides win over the global
	:data:`ACTION_RELATION_MAP`. Returns an empty list when no relation is configured
	(the caller should defer to Frappe).
	"""
	default = ACTION_RELATION_MAP.get(ptype)
	mapping = registry.get(doctype)
	if mapping is None:
		return [default] if default else []
	return mapping.get_permission_relations(ptype, default)


def resolve_relation(doctype: str, ptype: str) -> str | None:
	"""Resolve the primary Topaz relation that grants ``ptype`` for ``doctype``.

	Returns the first relation from :func:`resolve_relations`, or ``None``.
	"""
	rels = resolve_relations(doctype, ptype)
	return rels[0] if rels else None


def custom_has_permission(
	doctype: str,
	ptype: str = "read",
	doc=None,
	user: str | None = None,
	**kwargs,
) -> bool | None:
	"""Return True/False if Topaz can decide, or ``None`` to defer to Frappe.

	Frappe invokes this with the same positional/keyword shape as the built-in
	``has_permission`` hooks.
	"""
	if not is_topaz_enabled():
		return None

	user = user or frappe.session.user

	# --- bypass rules -------------------------------------------------
	if user == "Administrator" or "System Manager" in frappe.get_roles(user):
		return True
	if frappe.flags.get("ignore_permissions"):
		return True

	# Only enforce for DocTypes that are registered.
	mapping = registry.get(doctype)
	if mapping is None:
		return None

	# Skip Topaz checks for User DocType so administrative user permissions defer to Frappe
	if doctype == "User":
		return None

	relations = resolve_relations(doctype, ptype)
	if not relations:
		return None

	subject_id = resolve_subject_id(user)
	if subject_id is None:
		# Guest etc. -> no Topaz subject; defer.
		return None

	object_id = _resolve_object_id(doctype, ptype, doc)
	if object_id is None:
		return None

	settings = get_topaz_settings()
	object_type = mapping.object_spec.object_type
	transport = get_transport()
	cache = _get_perm_cache()

	# If any relation check evaluates to True, access is granted.
	for relation in relations:
		cache_key = (object_type, object_id, relation, settings.subject_type, subject_id)
		if settings.perm_cache_enabled and cache_key in cache:
			if cache[cache_key]:
				return True
			continue

		try:
			allowed = transport.check(
				object_type=object_type,
				object_id=object_id,
				relation=relation,
				subject_type=settings.subject_type,
				subject_id=subject_id,
			)
		except Exception:
			frappe.log_error(
				title="Topaz permission check failed",
				message=frappe.get_traceback(),
			)
			allowed = False

		if settings.perm_cache_enabled:
			cache[cache_key] = allowed

		if allowed:
			return True

	return False


def _resolve_object_id(doctype: str, ptype: str, doc) -> str | None:
	"""Resolve the Topaz object id for the permission check.

	If a document instance is passed, use its mapped object id; otherwise (a
	doctype-level check without a specific document) there is nothing concrete
	to check, so defer.
	"""
	if doc is None:
		return None
	if isinstance(doc, str):
		# A document name was passed rather than a doc instance.
		return doc or None

	mapping = registry.get(doctype)
	if mapping is None:
		return None
	obj = mapping.get_object(doc)
	return obj.id if obj else None
