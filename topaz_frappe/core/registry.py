"""Central, declarative registry mapping DocTypes to Topaz object/relation specs.

The registry is intentionally *global* and *declarative*: apps can register
mappings from their ``hooks.py`` via a ``topaz_registry`` hook without
modifying the target DocType's controller code.

Example hook (in any installed app's ``hooks.py``)::

        topaz_registry = {
            "Invoice": {
                "object": {"type": "invoice", "id_field": "name"},
                "relations": [
                    {"kind": "link", "relation": "owner", "target_type": "customer", "field": "customer"},
                    {
                        "kind": "child",
                        "table": "user_permissions",
                        "subject_field": "user",
                        "static_relation": "viewer",
                    },
                ],
            },
        }

The same structures can also be built programmatically using the dataclasses
in :mod:`topaz_frappe.core.domain`.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Optional

import frappe

from topaz_frappe.core.domain import (
	ChildTableRelation,
	ComputedRelation,
	DocTypeMapping,
	LinkRelation,
	TopazObjectSpec,
)

if TYPE_CHECKING:
	from frappe.model.document import Document

	from topaz_frappe.core.domain import TopazObject, TopazRelation

__all__ = ["TopazRegistry", "registry"]


_KIND_LINK = "link"
_KIND_CHILD = "child"
_KIND_COMPUTED = "computed"


class TopazRegistry:
	"""Holds the DocType -> :class:`DocTypeMapping` table.

	A single module-level :data:`registry` instance is shared across the app.
	It lazily loads mappings from the ``topaz_registry`` hook and caches them
	for the lifetime of the process; call :meth:`reload` (e.g. after install or
	from tests) to clear the cache.
	"""

	def __init__(self) -> None:
		self._mappings: dict[str, DocTypeMapping] = {}
		self._loaded = False
		# Guards lazy hook loading across worker threads (gunicorn). The mapping
		# table itself is read-mostly after load; runtime register()/unregister()
		# calls are only expected in tests (single-threaded).
		import threading

		self._lock = threading.RLock()

	# -- registration -------------------------------------------------
	def register(self, doctype: str, mapping: DocTypeMapping) -> None:
		"""Register (or replace) the mapping for ``doctype``."""
		self._mappings[doctype] = mapping

	def register_dict(self, doctype: str, config: dict[str, Any]) -> None:
		"""Register a mapping from a declarative dict config."""
		self._mappings[doctype] = _build_mapping_from_dict(config)

	def unregister(self, doctype: str) -> None:
		self._mappings.pop(doctype, None)

	def reload(self) -> None:
		"""Drop cached hook mappings and reload from ``topaz_registry`` hooks."""
		self._loaded = False
		# Preserve programmatic registrations done at import time by keeping the
		# dict but re-levelling hook entries (hooks win on conflict).
		self._ensure_loaded(force=True)

	# -- lookup -------------------------------------------------------
	def get(self, doctype: str) -> DocTypeMapping | None:
		self._ensure_loaded()
		return self._mappings.get(doctype)

	def is_registered(self, doctype: str) -> bool:
		return self.get(doctype) is not None

	def doctypes(self) -> list[str]:
		self._ensure_loaded()
		return list(self._mappings.keys())

	def managed_doctypes(self) -> list[str]:
		"""DocTypes whose object types Frappe is authoritative for.

		Only these are synced/pruned. Declared mappings with ``managed=False``
		(external-service-owned object types) are excluded.
		"""
		self._ensure_loaded()
		return [dt for dt, m in self._mappings.items() if getattr(m, "managed", True)]

	def all_mappings(self) -> dict[str, DocTypeMapping]:
		self._ensure_loaded()
		return dict(self._mappings)

	# -- internal -----------------------------------------------------
	def _ensure_loaded(self, force: bool = False) -> None:
		if self._loaded and not force:
			return
		with self._lock:
			# Re-check under the lock: another thread may have loaded already.
			if self._loaded and not force:
				return
			for doctype, config in self._iter_hook_configs():
				try:
					self._mappings[doctype] = _config_to_mapping(config)
				except Exception:
					frappe.log_error(
						title="Topaz registry: failed to load mapping",
						message=f"DocType: {doctype}\nconfig: {config!r}\n{frappe.get_traceback()}",
					)
			self._loaded = True

	def _iter_hook_configs(self):
		hooks = frappe.get_hooks("topaz_registry", {})
		if not hooks:
			return
		# ``topaz_registry`` may be declared as a dict (one app) or as a list of
		# dicts (merged across apps by frappe.get_hooks).
		if isinstance(hooks, dict):
			yield from hooks.items()
		elif isinstance(hooks, list):
			for hook in hooks:
				if isinstance(hook, dict):
					yield from hook.items()

	# -- convenience helpers -----------------------------------------
	def get_object(self, doc: "Document") -> "TopazObject" | None:
		mapping = self.get(doc.doctype)
		if mapping is None:
			return None
		return mapping.get_object(doc)

	def get_relations(self, doc: "Document") -> list["TopazRelation"]:
		mapping = self.get(doc.doctype)
		if mapping is None:
			return []
		return mapping.get_relations(doc)


def _config_to_mapping(config: Any) -> DocTypeMapping:
	"""Coerce a registry config (dict or :class:`DocTypeMapping`) to a mapping."""
	if isinstance(config, DocTypeMapping):
		return config
	if not isinstance(config, dict):
		raise TypeError(f"Invalid topaz_registry config: {config!r}")
	return _build_mapping_from_dict(config)


def _unwrap_hook_value(val: Any) -> Any:
	"""frappe.get_hooks wraps nested dict values into lists; unwrap singletons."""
	if isinstance(val, (list, tuple)) and len(val) == 1:
		return val[0]
	return val


def _build_mapping_from_dict(config: dict[str, Any]) -> DocTypeMapping:
	object_conf = config.get("object") or {}
	if isinstance(object_conf, TopazObjectSpec):
		object_spec = object_conf
	else:
		raw_type = object_conf.get("type") or object_conf.get("object_type")
		raw_id_field = object_conf.get("id_field", "name")
		raw_id_transform = object_conf.get("id_transform")

		object_spec = TopazObjectSpec(
			object_type=_unwrap_hook_value(raw_type),
			id_field=_unwrap_hook_value(raw_id_field),
			id_transform=_unwrap_hook_value(raw_id_transform),
		)
	if not object_spec.object_type:
		raise ValueError("Registry mapping 'object' requires a 'type'.")

	relations = [_build_relation(spec) for spec in config.get("relations", [])]

	# Per-DocType permission overrides: ptype -> Topaz relation.
	# Accept either key for readability in hooks.
	perm_conf = config.get("permissions") or config.get("permission_relations") or {}
	if not isinstance(perm_conf, dict):
		raise TypeError(f"'permissions' in registry mapping must be a dict, got {type(perm_conf)!r}")
	permission_relations = {str(k): str(v) for k, v in perm_conf.items()}

	return DocTypeMapping(
		object_spec=object_spec,
		relations=relations,
		permission_relations=permission_relations,
		managed=bool(config.get("managed", True)),
	)


def _build_relation(spec: Any):
	"""Coerce one relation spec (dict or dataclass instance) to a spec object."""
	if isinstance(spec, (LinkRelation, ChildTableRelation, ComputedRelation)):
		return spec
	if not isinstance(spec, dict):
		raise TypeError(f"Invalid relation spec: {spec!r}")

	kind = spec.get("kind") or spec.get("type")
	if kind is None:
		# Infer: explicit generator -> computed; table -> child; else link.
		if "generator" in spec:
			kind = _KIND_COMPUTED
		elif "table" in spec:
			kind = _KIND_CHILD
		else:
			kind = _KIND_LINK

	if kind == _KIND_LINK:
		return LinkRelation(
			relation=spec["relation"],
			target_type=spec.get("target_type") or spec["target"],
			fieldname=spec.get("fieldname") or spec.get("field", ""),
			subject_relation=spec.get("subject_relation"),
			transform=spec.get("transform"),
		)
	if kind == _KIND_CHILD:
		return ChildTableRelation(
			table=spec["table"],
			subject_field=spec["subject_field"],
			relation_field=spec.get("relation_field"),
			static_relation=spec.get("static_relation"),
			subject_type_field=spec.get("subject_type_field"),
			default_subject_type=spec.get("default_subject_type", "user"),
			subject_relation_field=spec.get("subject_relation_field"),
			subject_relation=spec.get("subject_relation"),
			relation_transform=spec.get("relation_transform"),
			subject_transform=spec.get("subject_transform"),
			condition=spec.get("condition"),
		)
	if kind == _KIND_COMPUTED:
		return ComputedRelation(generator=spec["generator"])

	raise ValueError(f"Unknown relation kind: {kind!r}")


#: Process-wide singleton registry.
registry = TopazRegistry()
