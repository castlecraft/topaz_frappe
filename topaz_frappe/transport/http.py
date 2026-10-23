"""HTTP transport for a Topaz / Authzed directory service (REST v3).

Topaz exposes a gRPC-gateway REST API on its reader/writer port (default
``http://localhost:9292``). This adapter talks to the *directory* subset of that
API (objects and relationships), which is all the sync engine and the
permission checker need.

Endpoints used (all under ``<host>/api/v3/directory``)::

	POST   /object          WriteObject
	DELETE /object          DeleteObject
	POST   /relation        WriteRelationship
	DELETE /relation        DeleteRelationship
	    POST   /check           Check
	POST   /relations/read  ReadRelationships
	POST   /search          Search

Configuration is read from ``site_config.json`` (see
``topaz_frappe.config.settings``). Paths are kept as module-level constants so
they can be adjusted for a specific Topaz build without touching call sites.
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING, Any, Optional

import frappe
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from topaz_frappe.transport.base import TopazTransportError

if TYPE_CHECKING:
	from topaz_frappe.core.domain import TopazObject, TopazRelation

__all__ = ["HttpTransport"]

PATH_OBJECT = "/api/v3/directory/object"
PATH_RELATION = "/api/v3/directory/relation"
PATH_CHECK = "/api/v3/directory/check"
PATH_RELATIONS_READ = "/api/v3/directory/relations/read"
PATH_SEARCH = "/api/v3/directory/search"


class HttpTransport:
	"""Concrete :class:`~topaz_frappe.transport.base.TopazTransportPort` over HTTP."""

	def __init__(
		self,
		host: str | None = None,
		api_key: str | None = None,
		timeout: float = 10.0,
		max_retries: int = 3,
		pool_size: int = 10,
		session: requests.Session | None = None,
	):
		from topaz_frappe.config.settings import get_topaz_settings

		settings = get_topaz_settings()
		self.host = (host or settings.host).rstrip("/")
		self.api_key = api_key if api_key is not None else settings.api_key
		self.timeout = timeout
		self.max_retries = max_retries

		self._session = session or self._build_session(pool_size=pool_size, max_retries=max_retries)

	# ------------------------------------------------------------------
	# session / request plumbing
	# ------------------------------------------------------------------
	@staticmethod
	def _build_session(pool_size: int, max_retries: int) -> requests.Session:
		session = requests.Session()
		retry = Retry(
			total=max_retries,
			backoff_factor=0.2,
			status_forcelist=(429, 500, 502, 503, 504),
			allowed_methods=frozenset({"GET", "POST", "DELETE"}),
			raise_on_status=False,
		)
		adapter = HTTPAdapter(
			max_retries=retry,
			pool_connections=pool_size,
			pool_maxsize=pool_size,
		)
		session.mount("http://", adapter)
		session.mount("https://", adapter)
		return session

	def _url(self, path: str) -> str:
		return f"{self.host}{path}"

	def _headers(self) -> dict[str, str]:
		headers = {"Content-Type": "application/json", "Accept": "application/json"}
		if self.api_key:
			headers["Authorization"] = f"Bearer {self.api_key}"
		return headers

	def _request(
		self,
		method: str,
		path: str,
		json: dict[str, Any] | None = None,
		*,
		ok_statuses: tuple[int, ...] = (200, 201, 204),
	) -> Any:
		try:
			response = self._session.request(
				method,
				self._url(path),
				json=json,
				headers=self._headers(),
				timeout=self.timeout,
			)
		except requests.RequestException as exc:  # network / timeout
			raise TopazTransportError(f"{method} {path} failed: {exc}") from exc

		if response.status_code not in ok_statuses:
			# DELETE of a non-existent relation/object can legitimately 404.
			raise TopazTransportError(
				f"{method} {path} returned {response.status_code}: {response.text[:500]}"
			)

		if response.status_code == 204 or not response.content:
			return None
		try:
			return response.json()
		except ValueError:
			return None

	# ------------------------------------------------------------------
	# Object operations
	# ------------------------------------------------------------------
	def put_object(self, obj: "TopazObject") -> bool:
		self._request(
			"POST",
			PATH_OBJECT,
			{"object": {"objectType": obj.type, "objectId": obj.id}},
		)
		return True

	def delete_object(self, obj: "TopazObject") -> bool:
		# Deleting a missing object is treated as success (idempotent).
		self._request(
			"DELETE",
			PATH_OBJECT,
			{"object": {"objectType": obj.type, "objectId": obj.id}},
			ok_statuses=(200, 204, 404),
		)
		return True

	# ------------------------------------------------------------------
	# Relationship operations
	# ------------------------------------------------------------------
	def set_relation(self, rel: "TopazRelation") -> bool:
		payload = rel.as_api_dict()
		self._request("POST", PATH_RELATION, payload)
		return True

	def delete_relation(self, rel: "TopazRelation") -> bool:
		payload = rel.as_api_dict()
		self._request("DELETE", PATH_RELATION, payload, ok_statuses=(200, 204, 404))
		return True

	# ------------------------------------------------------------------
	# Check / read
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
		subject: dict[str, Any] = {"object": {"objectType": subject_type, "objectId": subject_id}}
		if subject_relation:
			subject["relation"] = subject_relation
		payload = {
			"object": {"objectType": object_type, "objectId": object_id},
			"relation": relation,
			"subject": subject,
		}
		data = self._request("POST", PATH_CHECK, payload)
		if not data:
			return False
		# Topaz returns {"check": true} / {"isMember": true} depending on build.
		return bool(data.get("check") or data.get("isMember") or data.get("result"))

	def search_subjects(
		self,
		object_type: str,
		object_id: str,
		relation: str,
		subject_type: str,
	) -> list[str]:
		payload = {
			"object": {"objectType": object_type, "objectId": object_id},
			"relation": relation,
			"subjectObjectType": subject_type,
		}
		data = self._request("POST", PATH_RELATIONS_READ, payload) or {}
		out: list[str] = []
		for item in data.get("results", data.get("relations", [])) or []:
			subject = item.get("subject") or item.get("relationship", {}).get("subject", {})
			obj = subject.get("object") or {}
			if obj.get("objectId"):
				out.append(obj["objectId"])
		return out

	def search_resources(
		self,
		object_type: str,
		relation: str,
		subject_type: str,
		subject_id: str,
		subject_relation: str | None = None,
	) -> list[str]:
		subject: dict[str, Any] = {"object": {"objectType": subject_type, "objectId": subject_id}}
		if subject_relation:
			subject["relation"] = subject_relation
		payload = {
			"objectType": object_type,
			"relation": relation,
			"subject": subject,
		}
		data = self._request("POST", PATH_SEARCH, payload) or {}
		out: list[str] = []
		for item in data.get("results", data.get("objects", [])) or []:
			obj = item.get("object") or item
			object_id = obj.get("objectId") or item.get("objectId")
			if object_id:
				out.append(object_id)
		return out

	def list_objects(self, object_type: str) -> list[str]:
		payload = {"objectType": object_type}
		data = self._request("POST", PATH_SEARCH, payload) or {}
		out: list[str] = []
		for item in data.get("results", data.get("objects", [])) or []:
			obj = item.get("object") or item
			object_id = obj.get("objectId") or item.get("objectId")
			if object_id:
				out.append(object_id)
		return out

	# ------------------------------------------------------------------
	# Diagnostics
	# ------------------------------------------------------------------
	def health(self) -> dict:
		start = time.perf_counter()
		try:
			# A harmless check against a sentinel object exercises connectivity.
			self._request(
				"POST",
				PATH_CHECK,
				{
					"object": {"objectType": "__topaz_health__", "objectId": "ping"},
					"relation": "ping",
					"subject": {"object": {"objectType": "user", "objectId": "ping"}},
				},
				ok_statuses=(200, 201, 204, 404),
			)
			latency_ms = (time.perf_counter() - start) * 1000.0
			return {"ok": True, "host": self.host, "latency_ms": round(latency_ms, 2)}
		except Exception as exc:
			latency_ms = (time.perf_counter() - start) * 1000.0
			return {
				"ok": False,
				"host": self.host,
				"latency_ms": round(latency_ms, 2),
				"error": str(exc),
			}
