"""Persistent log of sync operations (``Topaz Sync Log`` DocType).

Records every upsert/delete attempt with its payload, status and error. Writes
are best-effort and must never raise into the caller; failures fall back to the
standard error log.
"""

from __future__ import annotations

from typing import Any, Optional

import frappe

__all__ = ["write_sync_log"]

_LOG_DOCTYPE = "Topaz Sync Log"


def write_sync_log(
	*,
	doctype: str,
	docname: str,
	method: str,
	status: str,
	payload: dict[str, Any] | None = None,
	error: str | None = None,
	retry_count: int = 0,
) -> None:
	"""Insert a ``Topaz Sync Log`` record.

	Silently degrades if the DocType is not (yet) migrated, so that this can be
	called safely during install/tests.
	"""
	try:
		if not frappe.db.table_exists(_LOG_DOCTYPE):
			return
		doc = frappe.new_doc(_LOG_DOCTYPE)
		doc.source_doctype = doctype
		doc.docname = docname
		doc.method = method
		doc.status = status
		doc.payload = frappe.as_json(payload) if payload is not None else None
		doc.error = error
		doc.retry_count = retry_count
		doc.flags.ignore_permissions = True
		doc.insert(ignore_permissions=True)
	except Exception:
		frappe.log_error(title="Topaz sync log write failed", message=frappe.get_traceback())
