"""Sync mixin letting DocType controllers participate programmatically.

Most apps will use the declarative :mod:`topaz_frappe.core.registry` instead,
but a controller can subclass :class:`TopazSyncMixin` (or set
``extend_doctype_class``) to compute the object/relations in Python.

Both styles are supported: the registry is consulted first, and if a DocType's
controller implements the mixin methods they are used as a fallback (or
override with ``use_mixin_override = True``).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, ClassVar, Optional

from topaz_frappe.core.registry import registry

if TYPE_CHECKING:
	from topaz_frappe.core.domain import TopazObject, TopazRelation

__all__ = ["TopazSyncMixin"]


class TopazSyncMixin:
	"""Mixin that maps a DocType to a Topaz object and relationship set."""

	#: Topaz object type; ``None`` means "use the registry".
	topaz_object_type: ClassVar[str | None] = None

	#: When True, methods defined on the controller take precedence over the
	#: registry. Useful for fully programmatic DocTypes.
	use_mixin_override: ClassVar[bool] = False

	def get_topaz_object(self) -> "TopazObject" | None:
		"""Return the :class:`TopazObject` for this document.

		Default implementation reads ``topaz_object_type`` and the ``name``
		field. Override for custom identifiers.
		"""
		from topaz_frappe.core.domain import TopazObject

		if not self.topaz_object_type:
			return None
		name = getattr(self, "name", None)
		if not name:
			return None
		return TopazObject(self.topaz_object_type, str(name))

	def get_topaz_relations(self) -> list["TopazRelation"]:
		"""Return all relationship tuples for this document.

		Default implementation returns an empty list; override to provide
		relations, or prefer the declarative registry.
		"""
		return []


def resolve_object(doc) -> "TopazObject" | None:
	"""Resolve the Topaz object for ``doc`` honouring mixin overrides first."""
	mixin = _get_mixin(doc)
	if mixin is not None and getattr(doc, "use_mixin_override", False):
		return mixin.get_topaz_object()  # type: ignore[attr-defined]
	obj = registry.get_object(doc)
	if obj is not None:
		return obj
	if mixin is not None:
		return mixin.get_topaz_object()  # type: ignore[attr-defined]
	return None


def resolve_relations(doc, obj: "TopazObject" | None = None) -> list["TopazRelation"]:
	"""Resolve all relationship tuples for ``doc`` honouring mixin overrides."""
	obj = obj or resolve_object(doc)
	if obj is None:
		return []
	mixin = _get_mixin(doc)

	if mixin is not None and getattr(doc, "use_mixin_override", False):
		rels = list(mixin.get_topaz_relations())  # type: ignore[attr-defined]
		# Ensure relations point at the resolved object.
		return [_rebind(rel, obj) for rel in rels]

	relations = registry.get_relations(doc)
	if relations:
		return relations
	if mixin is not None:
		rels = list(mixin.get_topaz_relations())  # type: ignore[attr-defined]
		return [_rebind(rel, obj) for rel in rels]
	return []


def _get_mixin(doc):
	"""Return ``doc`` if it implements the mixin methods, else ``None``."""
	if hasattr(doc, "get_topaz_object") and hasattr(doc, "get_topaz_relations"):
		return doc
	return None


def _rebind(rel, obj):
	from topaz_frappe.core.domain import TopazRelation

	if rel.object_type == obj.type and rel.object_id == obj.id:
		return rel
	return TopazRelation(
		object_type=obj.type,
		object_id=obj.id,
		relation=rel.relation,
		subject_type=rel.subject_type,
		subject_id=rel.subject_id,
		subject_relation=rel.subject_relation,
	)
