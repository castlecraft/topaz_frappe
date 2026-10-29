"""In-memory :class:`~topaz_frappe.transport.base.TopazTransportPort` for tests.

The mock implements a small but faithful subset of Zanzibar semantics,
including *subject sets* (``group:eng#member``): when checking
``document:D#viewer@user:alice`` a tuple
``document:D#viewer@group:eng#member`` only grants access if
``group:eng#member@user:alice`` also holds. This is recursive with cycle
protection, which makes the mock genuinely useful for exercising the
permission checker.
"""

from __future__ import annotations

import threading
import time
from typing import TYPE_CHECKING, Optional

from topaz_frappe.transport.base import TopazTransportError

if TYPE_CHECKING:
	from topaz_frappe.core.domain import TopazObject, TopazRelation

__all__ = ["MockTransport"]


class MockTransport:
	"""Thread-safe, in-memory directory implementation."""

	def __init__(self, *, fail_on: set[str] | None = None):
		self._objects: set[tuple[str, str]] = set()
		self._relations: set[tuple[str, ...]] = set()
		self._lock = threading.RLock()
		#: Operation names (``"put_object"`` ...) to raise on, for failure tests.
		self.fail_on = fail_on or set()
		self.calls: dict[str, int] = {}

	def _record(self, op: str) -> None:
		self.calls[op] = self.calls.get(op, 0) + 1
		if op in self.fail_on:
			raise TopazTransportError(f"MockTransport: forced failure on {op}")

	@staticmethod
	def _rel_key(rel: "TopazRelation") -> tuple[str, ...]:
		return rel.key()

	# ------------------------------------------------------------------
	# Object operations
	# ------------------------------------------------------------------
	def put_object(self, obj: "TopazObject") -> bool:
		self._record("put_object")
		with self._lock:
			self._objects.add(obj.key())
		return True

	def delete_object(self, obj: "TopazObject") -> bool:
		self._record("delete_object")
		with self._lock:
			self._objects.discard(obj.key())
			# Cascade: remove relationships where this object is resource or subject.
			self._relations = {
				key
				for key in self._relations
				if not ((key[0], key[1]) == obj.key() or (key[3], key[4]) == obj.key())
			}
		return True

	def object_exists(self, obj: "TopazObject") -> bool:
		with self._lock:
			return obj.key() in self._objects

	# ------------------------------------------------------------------
	# Relationship operations
	# ------------------------------------------------------------------
	def set_relation(self, rel: "TopazRelation") -> bool:
		self._record("set_relation")
		with self._lock:
			self._relations.add(self._rel_key(rel))
			# Implicitly register the involved objects.
			self._objects.add((rel.object_type, rel.object_id))
			self._objects.add((rel.subject_type, rel.subject_id))
		return True

	def delete_relation(self, rel: "TopazRelation") -> bool:
		self._record("delete_relation")
		with self._lock:
			self._relations.discard(self._rel_key(rel))
		return True

	def relations(self) -> list["TopazRelation"]:
		"""Return a snapshot of all stored relationships (test helper)."""
		from topaz_frappe.core.domain import TopazRelation

		with self._lock:
			return [
				TopazRelation(
					object_type=k[0],
					object_id=k[1],
					relation=k[2],
					subject_type=k[3],
					subject_id=k[4],
					subject_relation=k[5] or None,
				)
				for k in sorted(self._relations)
			]

	def clear(self) -> None:
		with self._lock:
			self._objects.clear()
			self._relations.clear()
			self.calls.clear()

	# ------------------------------------------------------------------
	# Check with subject-set expansion
	# ------------------------------------------------------------------
	def check(
		self,
		object_type: str,
		object_id: str,
		relation: str,
		subject_type: str,
		subject_id: str,
		subject_relation: str | None = None,
	) -> bool:
		self._record("check")
		with self._lock:
			return self._check_internal(
				(object_type, object_id),
				relation,
				(subject_type, subject_id),
				subject_relation,
				visited=set(),
			)

	def _check_internal(
		self,
		resource: tuple[str, str],
		relation: str,
		subject: tuple[str, str],
		subject_relation: str | None,
		visited: set[tuple],
	) -> bool:
		marker = (resource, relation, subject, subject_relation)
		if marker in visited:
			return False
		visited.add(marker)

		# Direct match?
		for key in self._relations:
			if key[0] != resource[0] or key[1] != resource[1]:
				continue
			if key[2] != relation:
				continue
			if (key[3], key[4]) != subject:
				continue
			if subject_relation and (key[5] or None) != subject_relation:
				continue
			if not subject_relation and key[5]:
				continue
			return True

		# Subject-set expansion: find tuples on this resource/relation whose
		# subject is a set the requested subject belongs to.
		for key in list(self._relations):
			if key[0] != resource[0] or key[1] != resource[1] or key[2] != relation:
				continue
			if not key[5]:
				continue
			set_obj = (key[3], key[4])
			set_relation = key[5]
			if self._check_internal(set_obj, set_relation, subject, subject_relation, visited):
				return True
		return False

	# ------------------------------------------------------------------
	# Reverse lookups
	# ------------------------------------------------------------------
	def search_subjects(
		self,
		object_type: str,
		object_id: str,
		relation: str,
		subject_type: str,
	) -> list[str]:
		self._record("search_subjects")
		with self._lock:
			out = []
			for key in self._relations:
				if key[0] == object_type and key[1] == object_id and key[2] == relation:
					if key[3] == subject_type:
						out.append(key[4])
			return sorted(set(out))

	def search_resources(
		self,
		object_type: str,
		relation: str,
		subject_type: str,
		subject_id: str,
		subject_relation: str | None = None,
	) -> list[str]:
		self._record("search_resources")
		with self._lock:
			out = []
			for key in self._relations:
				if key[0] != object_type or key[2] != relation:
					continue
				if self._check_internal(
					(key[0], key[1]),
					key[2],
					(subject_type, subject_id),
					subject_relation,
					visited=set(),
				):
					out.append(key[1])
			return sorted(set(out))

	def list_objects(self, object_type: str) -> list[str]:
		self._record("list_objects")
		with self._lock:
			return sorted({k[1] for k in self._objects if k[0] == object_type})

	def health(self) -> dict:
		start = time.perf_counter()
		return {
			"ok": True,
			"host": "mock://",
			"latency_ms": round((time.perf_counter() - start) * 1000.0, 4),
			"objects": len(self._objects),
			"relations": len(self._relations),
		}

	def set_manifest(self, manifest_yaml: str) -> bool:
		self._record("set_manifest")
		return True

	def get_manifest(self) -> str:
		self._record("get_manifest")
		return ""
