"""Domain model for the Topaz / Authzed relationship engine.

This module defines the immutable value objects (:class:`TopazObject`,
:class:`TopazRelation`) and the declarative mapping specifications
(:class:`TopazObjectSpec`, :class:`LinkRelation`, :class:`ChildTableRelation`,
:class:`ComputedRelation`) that let a DocType be translated into Zanzibar
tuples *without* touching the DocType's Python controller.

All mapping objects are plain ``dataclass`` instances so that they can be
declared inline in a ``topaz_registry`` hook dictionary or built
programmatically.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Optional

if TYPE_CHECKING:
	from frappe.model.document import Document

__all__ = [
	"ChildTableRelation",
	"ComputedRelation",
	"LinkRelation",
	"RelationSpec",
	"TopazObject",
	"TopazObjectSpec",
	"TopazRelation",
]


# ----------------------------------------------------------------------
# Value objects
# ----------------------------------------------------------------------
@dataclass(frozen=True)
class TopazObject:
	"""A bare Zanzibar object, e.g. ``invoice:INV-0001``.

	:param type: The Topaz object type (the "namespace").
	:param id: The stable identifier of the object within ``type``.
	"""

	type: str
	id: str

	def key(self) -> tuple[str, str]:
		"""Return a hashable ``(type, id)`` tuple."""
		return (self.type, self.id)

	def as_api_dict(self) -> dict[str, str]:
		"""Serialise to the Topaz directory API object shape."""
		return {"type": self.type, "id": self.id}

	def __str__(self) -> str:
		return f"{self.type}:{self.id}"


@dataclass(frozen=True)
class TopazRelation:
	"""A Zanzibar relationship tuple.

	Represents ``object#relation@subject`` optionally with a subject relation
	(set membership), e.g. ``document:README#viewer@group:eng#member``.

	:param object_type: Type of the object the relation is defined on.
	:param object_id: Identifier of that object.
	:param relation: Name of the relation (``viewer``, ``editor`` ...).
	:param subject_type: Type of the subject.
	:param subject_id: Identifier of the subject.
	:param subject_relation: Optional relation on the subject, enabling
	    subject-set / nested relationships (``group:dev#member``).
	"""

	object_type: str
	object_id: str
	relation: str
	subject_type: str
	subject_id: str
	subject_relation: str | None = None

	@property
	def object(self) -> TopazObject:
		return TopazObject(self.object_type, self.object_id)

	@property
	def subject(self) -> TopazObject:
		return TopazObject(self.subject_type, self.subject_id)

	def key(self) -> tuple[str, ...]:
		"""Hashable identity used for diffing relation sets."""
		return (
			self.object_type,
			self.object_id,
			self.relation,
			self.subject_type,
			self.subject_id,
			self.subject_relation or "",
		)

	def as_api_dict(self) -> dict[str, Any]:
		"""Serialise to the Topaz directory relationship API shape."""
		d: dict[str, Any] = {
			"object_type": self.object_type,
			"object_id": self.object_id,
			"relation": self.relation,
			"subject_type": self.subject_type,
			"subject_id": self.subject_id,
		}
		if self.subject_relation:
			d["subject_relation"] = self.subject_relation
		return {"relation": d}

	def __str__(self) -> str:
		subject = f"{self.subject_type}:{self.subject_id}"
		if self.subject_relation:
			subject = f"{subject}#{self.subject_relation}"
		return f"{self.object_type}:{self.object_id}#{self.relation}@{subject}"


# ----------------------------------------------------------------------
# Mapping specifications
# ----------------------------------------------------------------------
# A relation spec can be any of the declarative types below. The protocol is
# simply "call me with a document and I return zero or more TopazRelation".
RelationSpec = "LinkRelation | ChildTableRelation | ComputedRelation"


@dataclass
class TopazObjectSpec:
	"""Declares how a DocType becomes a :class:`TopazObject`.

	:param object_type: The Topaz object type, e.g. ``"invoice"``.
	:param id_field: The Frappe field/column used as the object id.
	:param id_transform: Optional normaliser applied to the raw id
	    (e.g. lower-casing). Receives the raw value and returns a string.
	"""

	object_type: str
	id_field: str = "name"
	id_transform: Callable[[str], str] | None = None

	def get_object(self, doc: Document) -> TopazObject | None:
		"""Build a :class:`TopazObject` from ``doc`` or ``None`` if the id
		cannot be resolved."""
		raw = doc.get(self.id_field) if hasattr(doc, "get") else getattr(doc, self.id_field, None)
		if raw is None or raw == "":
			return None
		value = str(raw)
		if self.id_transform:
			value = self.id_transform(value)
		if not value:
			return None
		return TopazObject(self.object_type, value)


@dataclass
class LinkRelation:
	"""Maps a ``Link`` field on the parent document to a relation tuple.

	Example: an ``Invoice`` with ``customer = "ACME"`` produces
	``invoice:INV-1#owner@customer:ACME``.

	:param relation: The relation name to write.
	:param target_type: Subject object type that ``field`` points at.
	:param fieldname: The Link fieldname on the parent document.
	:param subject_relation: Optional relation on the subject (subject-set).
	:param transform: Optional callable to normalise the raw field value.
	"""

	relation: str
	target_type: str
	fieldname: str
	subject_relation: str | None = None
	transform: Callable[[str], str] | None = None
	field: str | None = None  # alias accepted in hook dicts

	def __post_init__(self) -> None:
		if self.field and not self.fieldname:
			self.fieldname = self.field
		self.fieldname = self.fieldname or self.field or ""

	def _raw_value(self, doc: Document) -> str | None:
		if hasattr(doc, "get"):
			return doc.get(self.fieldname)
		return getattr(doc, self.fieldname, None)

	def get_relations(self, doc: Document, obj: TopazObject) -> list[TopazRelation]:
		raw = self._raw_value(doc)
		if raw is None or raw == "":
			return []
		value = str(raw)
		if self.transform:
			value = self.transform(value)
		if not value:
			return []
		return [
			TopazRelation(
				object_type=obj.type,
				object_id=obj.id,
				relation=self.relation,
				subject_type=self.target_type,
				subject_id=value,
				subject_relation=self.subject_relation,
			)
		]


@dataclass
class ChildTableRelation:
	"""Maps rows of a child table to relation tuples.

	Each child row contributes one (or zero) tuples. The subject id comes from
	``subject_field`` while the *relation* may be either a static value or read
	dynamically from ``relation_field`` on the row. The subject *type* may
	likewise come from ``subject_type_field`` or fall back to
	``default_subject_type``.

	Example: a ``DocShare``-style child table holding ``(user, role)`` rows
	generates ``document:D1#role@user:alice`` per matching row.
	"""

	table: str
	subject_field: str
	relation_field: str | None = None
	static_relation: str | None = None
	subject_type_field: str | None = None
	default_subject_type: str = "user"
	subject_relation_field: str | None = None
	subject_relation: str | None = None
	relation_transform: Callable[[str], str] | None = None
	subject_transform: Callable[[str], str] | None = None
	# Optional per-row predicate to skip rows.
	condition: Callable[[Any], bool] | None = None

	def __post_init__(self) -> None:
		if not self.relation_field and not self.static_relation:
			raise ValueError(
				f"ChildTableRelation on table '{self.table}' requires either "
				"`relation_field` or `static_relation`."
			)

	def get_relations(self, doc: Document, obj: TopazObject) -> list[TopazRelation]:
		rows = doc.get(self.table) if hasattr(doc, "get") else getattr(doc, self.table, None)
		if not rows:
			return []

		relations: list[TopazRelation] = []
		for row in rows:
			if self.condition and not self.condition(row):
				continue

			subject_raw = _row_get(row, self.subject_field)
			if subject_raw is None or subject_raw == "":
				continue
			subject_id = str(subject_raw)
			if self.subject_transform:
				subject_id = self.subject_transform(subject_id)
			if not subject_id:
				continue

			relation = self.static_relation
			if self.relation_field:
				raw_relation = _row_get(row, self.relation_field)
				if raw_relation:
					relation = str(raw_relation)
			if not relation:
				continue
			if self.relation_transform:
				relation = self.relation_transform(relation)

			subject_type = self.default_subject_type
			if self.subject_type_field:
				raw_type = _row_get(row, self.subject_type_field)
				if raw_type:
					subject_type = str(raw_type)

			subject_relation = self.subject_relation
			if self.subject_relation_field:
				raw_sr = _row_get(row, self.subject_relation_field)
				subject_relation = str(raw_sr) if raw_sr else None

			relations.append(
				TopazRelation(
					object_type=obj.type,
					object_id=obj.id,
					relation=relation,
					subject_type=subject_type,
					subject_id=subject_id,
					subject_relation=subject_relation,
				)
			)
		return relations


@dataclass
class ComputedRelation:
	"""Escapes the declarative model for fully programmatic mapping.

	:param generator: A callable ``(doc) -> Iterable[TopazRelation]`` (or a
	    single relation / ``None``) invoked whenever the document changes.
	"""

	generator: Callable[[Document], Iterable[TopazRelation] | TopazRelation | None]

	def get_relations(self, doc: Document, obj: TopazObject) -> list[TopazRelation]:
		result = self.generator(doc)
		if result is None:
			return []
		if isinstance(result, TopazRelation):
			return [result]
		relations = list(result)
		# Defensive: normalise object identity to the resolved object.
		out = []
		for rel in relations:
			if not isinstance(rel, TopazRelation):
				raise TypeError(
					f"ComputedRelation generators must yield TopazRelation instances, got {type(rel)!r}"
				)
			if rel.object_type != obj.type or rel.object_id != obj.id:
				rel = TopazRelation(
					object_type=obj.type,
					object_id=obj.id,
					relation=rel.relation,
					subject_type=rel.subject_type,
					subject_id=rel.subject_id,
					subject_relation=rel.subject_relation,
				)
			out.append(rel)
		return out


@dataclass
class DocTypeMapping:
	"""The complete mapping for one DocType: how to build the object and all
	the relations derived from the document.

	:param object_spec: How the document becomes a :class:`TopazObject`.
	:param relations: The relation specs to evaluate for each document.
	:param permission_relations: Optional per-DocType overrides mapping a
	    Frappe ``ptype`` (``read``, ``write``, ``delete`` ...) to the Topaz
	    relation that grants it (e.g. ``{"read": "can_access_committee"}``).
	    Keys not present here fall back to the global
	    :data:`~topaz_frappe.permissions.checker.ACTION_RELATION_MAP` defaults.
	    Use ``"*"`` as a ptype to map *all* otherwise-unmapped permission types.
	:param managed: When ``True`` (default) this DocType is *authoritative* for
	    its object type: the sync engine writes/prunes it. Set ``False`` to
	    declare a mapping **only** for the opt-in Desk-permission path without
	    making Frappe responsible for syncing that object type — e.g. when an
	    external service owns the object type and writes it to Topaz directly.
	"""

	object_spec: TopazObjectSpec
	relations: list = field(default_factory=list)
	permission_relations: dict[str, list[str]] = field(default_factory=dict)
	managed: bool = True

	def get_object(self, doc: Document) -> TopazObject | None:
		return self.object_spec.get_object(doc)

	def get_relations(self, doc: Document, obj: TopazObject | None = None) -> list[TopazRelation]:
		obj = obj or self.get_object(doc)
		if obj is None:
			return []
		relations: list[TopazRelation] = []
		for spec in self.relations:
			relations.extend(spec.get_relations(doc, obj))
		return relations

	def get_permission_relations(self, ptype: str, default: str | list[str] | None = None) -> list[str]:
		"""Resolve the Topaz relations that grant a Frappe ``ptype``.

		Lookup order: explicit ``ptype`` override -> ``"*"`` override -> the
		supplied ``default`` (from the global map) -> empty list.
		"""
		if ptype in self.permission_relations:
			val = self.permission_relations[ptype]
			return list(val) if isinstance(val, (list, tuple)) else [val]
		if "*" in self.permission_relations:
			val = self.permission_relations["*"]
			return list(val) if isinstance(val, (list, tuple)) else [val]
		if default is not None:
			return list(default) if isinstance(default, (list, tuple)) else [default]
		return []

	def get_permission_relation(self, ptype: str, default: str | None = None) -> str | None:
		"""Resolve the primary Topaz relation for a Frappe ``ptype`` (backwards-compat).

		Returns the first configured relation for ``ptype``, or ``default``.
		"""
		rels = self.get_permission_relations(ptype, default)
		return rels[0] if rels else None


def _row_get(row: Any, fieldname: str) -> Any:
	"""Read a field from a child row that may be a ``Document`` or a dict."""
	if hasattr(row, "get") and callable(row.get):
		value = row.get(fieldname)
		if value is not None:
			return value
	# Fall back to attribute access (frappe Document rows).
	return getattr(row, fieldname, None)
