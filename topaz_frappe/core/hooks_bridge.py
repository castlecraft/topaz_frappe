"""Bridge between Frappe document lifecycle hooks and the Topaz sync engine.

This module is the single entry point registered on ``doc_events`` for ``*``.
For every document event it:

1. Consults the :mod:`topaz_frappe.core.registry` (and mixin overrides) to see
   whether the DocType participates in Topaz.
2. In test mode (``frappe.flags.in_test``) performs the sync synchronously so
   tests observe results immediately.
3. Otherwise defers a background job until *after* the surrounding database
   transaction commits via ``frappe.db.after_commit``. This guarantees we never
   push a tuple to Topaz for a document whose insert/update is later rolled
   back.
4. Deduplicates jobs per ``(doctype, docname, method)`` within a transaction so
   that repeated ``save()`` calls in one request enqueue a single job.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Optional

import frappe

from topaz_frappe.config.settings import get_topaz_settings, is_topaz_enabled
from topaz_frappe.core.sync_mixin import resolve_object, resolve_relations

if TYPE_CHECKING:
	from frappe.model.document import Document

__all__ = [
	"dispatch_sync",
	"handle_doc_event",
	"sync_delete_now",
	"sync_document_now",
]

_PENDING_FLAG = "_topaz_pending_jobs"
_EVENTS = ("on_update", "after_insert", "on_trash")


def handle_doc_event(doc: "Document", method: str) -> None:
	"""``doc_events`` handler registered for ``*``.

	:param doc: The document being acted upon.
	:param method: Frappe event name (``after_insert``, ``on_update`` ...).
	"""
	if not is_topaz_enabled():
		return

	meta = getattr(doc, "meta", None)
	if meta is not None and getattr(meta, "issingle", False):
		return
	if getattr(doc, "doctype", None) == "Topaz Sync Log":
		return

	# Only sync DocTypes we actually know how to map *and* that Frappe owns.
	from topaz_frappe.core.registry import registry

	mapping = registry.get(getattr(doc, "doctype", ""))
	if mapping is None or not getattr(mapping, "managed", True):
		return
	if resolve_object(doc) is None:
		return

	sync_method = _normalise_method(method)
	if sync_method is None:
		return

	if getattr(frappe.flags, "in_test", False) or frappe.flags.get("topaz_sync_immediate"):
		_run_now(doc.doctype, doc.name, sync_method)
		return

	_schedule_after_commit(doc.doctype, doc.name, sync_method)


def _normalise_method(method: str) -> str | None:
	if method in ("after_insert", "on_update"):
		return "upsert"
	if method == "on_trash":
		return "delete"
	return None


def _schedule_after_commit(doctype: str, docname: str, method: str) -> None:
	"""Queue a job to run after the current transaction commits."""
	pending = getattr(frappe.local, _PENDING_FLAG, None)
	if pending is None:
		pending = set()
		setattr(frappe.local, _PENDING_FLAG, pending)
		# Register the flush callback exactly once per transaction.
		frappe.db.after_commit.add(_flush_pending_jobs)

	key = (doctype, docname, method)
	if key in pending:
		return
	pending.add(key)


def _flush_pending_jobs() -> None:
	"""``after_commit`` callback: enqueue all queued sync jobs."""
	pending = getattr(frappe.local, _PENDING_FLAG, None)
	if not pending:
		return
	jobs = list(pending)
	pending.clear()
	setattr(frappe.local, _PENDING_FLAG, pending)

	queue = get_topaz_settings().sync_queue
	for doctype, docname, method in jobs:
		frappe.enqueue(
			"topaz_frappe.core.hooks_bridge.dispatch_sync",
			queue=queue,
			doctype=doctype,
			docname=docname,
			method=method,
			job_id=f"topaz_sync::{doctype}::{docname}::{method}",
			deduplicate=True,
			enqueue_after_commit=True,
			now=False,
		)


def dispatch_sync(doctype: str, docname: str, method: str) -> None:
	"""Background job entry point: perform the actual sync."""
	_run_now(doctype, docname, method)


def _run_now(doctype: str, docname: str, method: str) -> None:
	"""Synchronously sync a single document (used by job + test/immediate)."""
	from topaz_frappe.sync.engine import sync_delete, sync_upsert

	try:
		if method == "delete":
			sync_delete(doctype, docname)
		else:
			sync_upsert(doctype, docname)
	except Exception:
		_log_failure(doctype, docname, method)
		if not getattr(frappe.flags, "in_test", False):
			# In production, swallow so one bad doc does not abort the job batch.
			return
		raise


def sync_document_now(doc: "Document") -> None:
	"""Public helper to sync a document immediately, bypassing the queue."""
	_run_now(doc.doctype, doc.name, "upsert")


def sync_delete_now(doctype: str, docname: str) -> None:
	"""Public helper to sync a deletion immediately, bypassing the queue."""
	_run_now(doctype, docname, "delete")


def _log_failure(doctype: str, docname: str, method: str) -> None:
	"""Persist a failure record into the ``Topaz Sync Log`` DocType."""
	tb = frappe.get_traceback()
	frappe.log_error(
		title=f"Topaz sync failed: {doctype} {docname} ({method})",
		message=tb,
	)
	try:
		from topaz_frappe.sync.log import write_sync_log

		write_sync_log(
			doctype=doctype,
			docname=docname,
			method=method,
			status="Failed",
			error=tb,
		)
	except Exception:
		# Logging must never raise.
		frappe.log_error(title="Topaz sync log write failed", message=frappe.get_traceback())
