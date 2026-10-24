"""Synchronisation engine: pushes a Frappe document's object + relations to Topaz.

Responsibilities:

* ``sync_upsert``: load a document, resolve its :class:`TopazObject` and
  relationship set, diff against the previously-stored set (tracked in the
  ``Topaz Sync Log`` / a lightweight cache), and apply the minimal delta.
* ``sync_delete``: remove the object (cascading its relationships).

The previous relationship set is reconstructed from the *live* document on
update in most setups; to avoid full deletion storms we instead read the
currently-stored relations from the transport's reverse lookup when possible.
When that is unavailable we fall back to a tracked snapshot stored in the
per-document sync log.
"""

from __future__ import annotations

import json

import frappe

from topaz_frappe.core.domain import TopazObject, TopazRelation
from topaz_frappe.core.sync_mixin import resolve_object, resolve_relations
from topaz_frappe.sync.diff import compute_diff
from topaz_frappe.transport.factory import get_transport

__all__ = ["bulk_sync_doctype", "sync_delete", "sync_document", "sync_upsert"]


# ----------------------------------------------------------------------
# Snapshot helpers
# ----------------------------------------------------------------------
def _snapshot_key(obj: TopazObject) -> str:
	return f"topaz_relations::{obj.type}::{obj.id}"


def _load_previous_relations(obj: TopazObject) -> list[TopazRelation]:
	"""Load the previously-applied relationship set for ``obj``.

	Reads the last successful snapshot cached in Redis. Returns ``[]`` if none
	is known (first sync).
	"""
	raw = frappe.cache.get_value(_snapshot_key(obj))
	if not raw:
		return []
	try:
		data = json.loads(raw)
	except (TypeError, ValueError):
		return []
	out: list[TopazRelation] = []
	for item in data:
		out.append(
			TopazRelation(
				object_type=item[0],
				object_id=item[1],
				relation=item[2],
				subject_type=item[3],
				subject_id=item[4],
				subject_relation=item[5] or None,
			)
		)
	return out


def _store_previous_relations(obj: TopazObject, relations: list[TopazRelation]) -> None:
	payload = json.dumps([rel.key() for rel in relations])
	frappe.cache.set_value(_snapshot_key(obj), payload, expires_in_sec=60 * 60 * 24 * 30)


def _clear_previous_relations(obj: TopazObject) -> None:
	frappe.cache.delete_value(_snapshot_key(obj))


# ----------------------------------------------------------------------
# Public API
# ----------------------------------------------------------------------
def sync_document(doc) -> None:
	"""Apply a single in-memory document to Topaz (object + relations delta)."""
	obj = resolve_object(doc)
	if obj is None:
		return
	new_relations = resolve_relations(doc, obj)
	_apply(obj, new_relations)


def _apply(obj: TopazObject, new_relations: list[TopazRelation]) -> None:
	transport = get_transport()
	previous = _load_previous_relations(obj)
	diff = compute_diff(previous, new_relations)

	# Ensure the object itself exists before writing relationships.
	transport.put_object(obj)

	for rel in diff.to_delete:
		transport.delete_relation(rel)
	for rel in diff.to_add:
		transport.set_relation(rel)

	_store_previous_relations(obj, new_relations)

	from topaz_frappe.sync.log import write_sync_log

	write_sync_log(
		doctype="",
		docname=obj.id,
		method="upsert",
		status="Success",
		payload={
			"object": [obj.type, obj.id],
			"added": len(diff.to_add),
			"removed": len(diff.to_delete),
		},
	)


def sync_upsert(doctype: str, docname: str) -> None:
	"""Load ``doctype``/``docname`` and sync it to Topaz."""
	if not frappe.db.exists(doctype, docname):
		# The row is already gone; treat as delete.
		sync_delete(doctype, docname)
		return

	doc = frappe.get_doc(doctype, docname)
	obj = resolve_object(doc)
	if obj is None:
		return
	new_relations = resolve_relations(doc, obj)
	_apply(obj, new_relations)


def sync_delete(doctype: str, docname: str) -> None:
	"""Remove the Topaz object for a deleted document.

	We must reconstruct the object id even though the row is gone; for the
	common case (``id_field == "name"``) ``docname`` is the id.
	"""
	from topaz_frappe.core.registry import registry

	mapping = registry.get(doctype)
	if mapping is None:
		return
	spec = mapping.object_spec
	raw_id = docname if spec.id_field == "name" else None
	if raw_id is None:
		# Field-based id (rare): the row is gone so we cannot resolve it here;
		# rely on the snapshot keyed by name-based id if available.
		raw_id = docname
	value = str(raw_id)
	if spec.id_transform:
		value = spec.id_transform(value)
	obj = TopazObject(spec.object_type, value)

	transport = get_transport()
	transport.delete_object(obj)
	_clear_previous_relations(obj)

	from topaz_frappe.sync.log import write_sync_log

	write_sync_log(
		doctype=doctype,
		docname=docname,
		method="delete",
		status="Success",
		payload={"object": [obj.type, obj.id]},
	)


def bulk_sync_doctype(doctype: str, *, batch_size: int = 500, progress=None) -> dict:
	"""Sync every record of ``doctype`` to Topaz.

	Returns a summary dict ``{"processed", "failed"}``.
	"""
	from topaz_frappe.core.registry import registry

	if registry.get(doctype) is None:
		raise frappe.ValidationError(f"DocType '{doctype}' is not registered with Topaz.")
	if not getattr(registry.get(doctype), "managed", True):
		raise frappe.ValidationError(
			f"DocType '{doctype}' is marked managed=False (owned by an external "
			"service); refusing to bulk sync it from Frappe."
		)

	names = frappe.get_all(doctype, pluck="name", limit_page_length=0)
	total = len(names)
	processed = 0
	failed = 0
	for idx, name in enumerate(names, start=1):
		try:
			sync_upsert(doctype, name)
			processed += 1
		except Exception:
			failed += 1
			frappe.log_error(
				title=f"Topaz bulk sync failed: {doctype} {name}",
				message=frappe.get_traceback(),
			)
		if progress and (idx % batch_size == 0 or idx == total):
			progress(idx, total)
	return {"processed": processed, "failed": failed, "total": total}
