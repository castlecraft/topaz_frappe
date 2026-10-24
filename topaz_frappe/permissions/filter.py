"""Hybrid list filtering via ``permission_query_conditions``.

Strategy — *"Pick from Topaz, Filter & Page in Local DB"*:

1. Ask Topaz for the set of object ids the current subject can ``viewer`` on
   (reverse lookup / search).
2. Emit a local SQL predicate ```tab{Doctype}`.`name` IN ('id1','id2',...)``.
3. Let the database do the heavy filtering, JOINs, sorting and pagination over
   the indexed primary key.

Edge cases:

* Wildcard (``*``) access -> return ``""`` (no filter).
* Empty allowed set -> return ``1 = 0`` (return zero rows instantly, no scan).
* Allowed ids exceed the SQL IN-clause threshold -> chunk into multiple
   ``IN`` blocks OR-ed together (keeps the query valid); if it still exceeds a
   hard cap we fall back to an impossible condition and log a warning so
   operators can review the schema (use reverse-lookup narrowing instead).
"""

from __future__ import annotations

from typing import Optional

import frappe

from topaz_frappe.config.settings import get_topaz_settings, is_topaz_enabled
from topaz_frappe.core.registry import registry
from topaz_frappe.permissions.checker import (
	_get_perm_cache,
	resolve_relation,
	resolve_relations,
	resolve_subject_id,
)
from topaz_frappe.transport.factory import get_transport

__all__ = ["get_permission_query_conditions"]

#: Hard cap: beyond this many ids we refuse to build an IN clause and return a
#: blocking condition (safety valve against pathological packet sizes).
HARD_CAP_IDS = 100_000


def get_permission_query_conditions(user: str | None = None, doctype: str | None = None) -> str:
	"""Return a SQL condition string restricting ``doctype`` to allowed rows.

	Frappe calls this without arguments for registered DocTypes, passing the
	current user context implicitly. ``doctype`` is injected by the app's hook
	dispatcher (see ``hooks.py``) which binds the DocType name via a closure.

	Returns:
		* ``""`` -> no restriction.
		* ``"1 = 0"`` -> allow nothing.
		* ``"`tab{X}`.`name` IN ('a','b')"`` -> allow listed rows.
	"""
	if not is_topaz_enabled():
		# Let Frappe's own conditions apply; returning "" is a no-op.
		return ""

	if not doctype:
		# Without a concrete doctype we cannot build a predicate.
		return ""

	user = user or frappe.session.user
	if user == "Administrator" or "System Manager" in frappe.get_roles(user):
		return ""
	if frappe.flags.get("ignore_permissions"):
		return ""
	if frappe.local.flags.get("delete_doc"):
		# Avoid injecting filters during delete validation paths.
		return ""

	mapping = registry.get(doctype)
	if mapping is None:
		return ""

	# Skip Topaz filtering for User DocType so administrative user listings defer to Frappe
	if doctype == "User":
		return ""

	settings = get_topaz_settings()
	subject_id = resolve_subject_id(user)
	if subject_id is None:
		# Guest etc. -> deny everything for registered DocTypes.
		return "1 = 0"

	object_type = mapping.object_spec.object_type

	# The relations that grant *list visibility* are whatever the DocType maps
	# ``read`` to (default ``viewer``). This lets a schema expose list access
	# via e.g. ``can_access_committee``, ``["can_access", "is_member"]`` without touching this module.
	list_relations = resolve_relations(doctype, "read")
	if not list_relations:
		# No relation defined for reads -> nothing is listable via Topaz.
		return "1 = 0"

	cache = _get_perm_cache()
	transport = get_transport()
	all_allowed: set[str] = set()

	for list_relation in list_relations:
		cache_key = ("__allowed__", object_type, list_relation, settings.subject_type, subject_id)
		if cache_key in cache:
			allowed = cache[cache_key]
		else:
			try:
				allowed = transport.search_resources(
					object_type=object_type,
					relation=list_relation,
					subject_type=settings.subject_type,
					subject_id=subject_id,
				)
			except Exception:
				frappe.log_error(
					title="Topaz query-filter lookup failed",
					message=frappe.get_traceback(),
				)
				# Fail closed for this relation
				allowed = []
			allowed = list({str(x) for x in allowed})
			cache[cache_key] = allowed

		if "*" in allowed:
			return ""
		all_allowed.update(allowed)

	# Empty set.
	if not all_allowed:
		return "1 = 0"

	allowed = list(all_allowed)
	if len(allowed) > HARD_CAP_IDS:
		frappe.log_error(
			title="Topaz query-filter: too many ids",
			message=(
				f"{len(allowed)} allowed ids for {object_type} exceed hard cap "
				f"{HARD_CAP_IDS}; denying by default. Narrow the schema or use "
				"server-side pagination."
			),
		)
		return "1 = 0"

	return build_in_condition(doctype, allowed, threshold=settings.inflight_threshold)


def build_in_condition(doctype: str, ids: list[str], threshold: int = 5000) -> str:
	"""Build a safe SQL ``IN`` predicate over ``name`` for ``doctype``.

	Escapes each id and chunks them so the final query never exceeds the
	configured IN-clause size.
	"""
	escaped = [frappe.db.escape(str(i)) for i in ids]
	column = f"`tab{doctype}`.`name`"
	if len(escaped) <= threshold:
		return f"{column} IN ({', '.join(escaped)})"

	clauses = []
	for start in range(0, len(escaped), threshold):
		chunk = escaped[start : start + threshold]
		clauses.append(f"{column} IN ({', '.join(chunk)})")
	return "(" + " OR ".join(clauses) + ")"
