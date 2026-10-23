"""Transport port (protocol) for talking to a Topaz / Authzed directory service.

The :class:`TopazTransportPort` defines the minimal set of operations the sync
and permission layers require. Concrete adapters live in
``topaz_frappe.transport.http`` (real Topaz REST v3) and
``topaz_frappe.transport.mock`` (in-memory, for tests).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Optional, Protocol, runtime_checkable

if TYPE_CHECKING:
	from topaz_frappe.core.domain import TopazObject, TopazRelation

__all__ = ["TopazTransportError", "TopazTransportPort"]


class TopazTransportError(RuntimeError):
	"""Raised when a transport operation fails after exhausting retries."""


@runtime_checkable
class TopazTransportPort(Protocol):
	"""Protocol implemented by all Topaz transport adapters."""

	def put_object(self, obj: "TopazObject") -> bool:
		"""Create/ensure ``obj`` exists in the directory. Idempotent."""
		...

	def delete_object(self, obj: "TopazObject") -> bool:
		"""Remove ``obj`` (and its relationships) from the directory. Idempotent."""
		...

	def set_relation(self, rel: "TopazRelation") -> bool:
		"""Create/ensure the relationship tuple exists. Idempotent."""
		...

	def delete_relation(self, rel: "TopazRelation") -> bool:
		"""Remove the relationship tuple. Idempotent."""
		...

	def check(
		self,
		object_type: str,
		object_id: str,
		relation: str,
		subject_type: str,
		subject_id: str,
		subject_relation: str | None = None,
	) -> bool:
		"""Return True if ``subject`` has ``relation`` on the object."""
		...

	# -- extended (optional) operations used by CLI / filtering ---------
	def search_subjects(
		self,
		object_type: str,
		object_id: str,
		relation: str,
		subject_type: str,
	) -> list[str]:
		"""Return subject ids of ``subject_type`` having ``relation`` on the object."""
		...

	def search_resources(
		self,
		object_type: str,
		relation: str,
		subject_type: str,
		subject_id: str,
		subject_relation: str | None = None,
	) -> list[str]:
		"""Return object ids of ``object_type`` on which ``subject`` has ``relation``.

		This is the reverse lookup used for list filtering.
		"""
		...

	def health(self) -> dict:
		"""Return a health/diagnostic dict (connectivity + latency)."""
		...

	def list_objects(self, object_type: str) -> list[str]:
		"""Return all object ids of ``object_type`` known to the directory."""
		...
