"""Hook dispatchers for Topaz permission enforcement.

Frappe evaluates both permission hooks with a ``"*"`` wildcard fallback and
passes the DocType context:

* ``permission_query_conditions``: called as ``method(user, doctype=doctype)``
  (see ``frappe.model.db_query.DatabaseQuery.get_permission_query_conditions``).
* ``has_permission``: called as ``method(doc=doc, ptype=..., user=..., debug=...)``
  (see ``frappe.permissions.has_controller_permissions``); the wildcard handler
  resolves the doctype from the document.

Registering ``"*"`` lets the app enforce permissions for *any* DocType declared
in the ``topaz_registry`` hook without having to enumerate them statically in
``hooks.py``.
"""

from __future__ import annotations

from typing import Optional

from topaz_frappe.config.settings import is_desk_permissions_enabled
from topaz_frappe.permissions.checker import custom_has_permission
from topaz_frappe.permissions.filter import get_permission_query_conditions

__all__ = [
	"has_permission_dispatcher",
	"query_conditions_dispatcher",
]


def query_conditions_dispatcher(user: str | None = None, doctype: str | None = None, **kwargs) -> str:
	"""``permission_query_conditions["*"]`` handler.

	No-op unless Desk-permission enforcement is explicitly opted in via
	``topaz_desk_permissions`` in ``site_config.json``.
	"""
	if not is_desk_permissions_enabled():
		return ""
	return get_permission_query_conditions(user=user, doctype=doctype)


def has_permission_dispatcher(doc=None, ptype: str = "read", user: str | None = None, **kwargs):
	"""``has_permission["*"]`` handler.

	Resolves the doctype from ``doc``; when no document is provided there is no
	concrete object to check so Topaz defers (returns ``None``) and Frappe's
	role/permission system decides.

	No-op unless Desk-permission enforcement is explicitly opted in. When not
	opted in, returns ``True`` (controller hook contract: a truthy value means
	"this hook does not deny"), leaving Frappe's native permissions in force.
	"""
	if not is_desk_permissions_enabled():
		return True

	doctype = None
	if doc is not None:
		doctype = getattr(doc, "doctype", None) or (doc if isinstance(doc, str) else None)
	if not doctype:
		# Frappe may pass doctype via kwargs in some code paths.
		doctype = kwargs.get("doctype")

	if not doctype:
		return None

	result = custom_has_permission(doctype=doctype, ptype=ptype, doc=doc, user=user, **kwargs)
	# Returning None from a controller permission hook is treated as "deny" by
	# frappe.permissions.has_controller_permissions (it does `if not result`).
	# So we map "no opinion" to True to avoid blocking non-Topaz paths.
	if result is None:
		return True
	return result
