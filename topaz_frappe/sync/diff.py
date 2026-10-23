"""Relationship diff computation.

When a document changes we must not blindly destroy and recreate all of its
tuples (a "deletion storm"). Instead we compute the delta between the previous
and the current relationship sets and emit only the minimal ``to_add`` /
``to_delete`` batches.

The helper functions here operate on plain sequences of :class:`TopazRelation`
and are pure (no Frappe / network dependencies) so they are trivially testable.
"""

from __future__ import annotations

from collections.abc import Iterable

from topaz_frappe.core.domain import TopazRelation

__all__ = ["RelationDiff", "compute_diff"]


class RelationDiff:
	"""The delta between two relationship sets."""

	__slots__ = ("to_add", "to_delete", "unchanged")

	def __init__(
		self,
		to_add: list[TopazRelation],
		to_delete: list[TopazRelation],
		unchanged: list[TopazRelation],
	):
		self.to_add = to_add
		self.to_delete = to_delete
		self.unchanged = unchanged

	@property
	def is_empty(self) -> bool:
		return not self.to_add and not self.to_delete

	def __repr__(self) -> str:  # pragma: no cover - debug helper
		return (
			f"RelationDiff(to_add={len(self.to_add)}, "
			f"to_delete={len(self.to_delete)}, "
			f"unchanged={len(self.unchanged)})"
		)


def compute_diff(
	old_relations: Iterable[TopazRelation],
	new_relations: Iterable[TopazRelation],
) -> RelationDiff:
	"""Compute the delta between ``old`` and ``new`` relationship sets.

	``to_add`` are relations present in ``new`` but not ``old``;
	``to_delete`` are relations present in ``old`` but no longer in ``new``;
	``unchanged`` appear in both.

	Identity is based on :meth:`TopazRelation.key`. Order of the outputs
	follows the input ordering and is deterministic.
	"""
	old_map: dict[tuple, TopazRelation] = {}
	for rel in old_relations:
		old_map.setdefault(rel.key(), rel)

	new_map: dict[tuple, TopazRelation] = {}
	for rel in new_relations:
		new_map.setdefault(rel.key(), rel)

	to_add: list[TopazRelation] = []
	unchanged: list[TopazRelation] = []
	for key, rel in new_map.items():
		if key in old_map:
			unchanged.append(rel)
		else:
			to_add.append(rel)

	to_delete: list[TopazRelation] = [rel for key, rel in old_map.items() if key not in new_map]

	return RelationDiff(to_add=to_add, to_delete=to_delete, unchanged=unchanged)
