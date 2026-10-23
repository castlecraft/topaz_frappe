"""Typed accessors for the site-level Topaz configuration.

Reads from ``site_config.json`` with sensible defaults. All keys are optional
so the app degrades gracefully when Topaz is not configured (``enabled`` is
False and all hooks become no-ops).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional

import frappe

__all__ = [
	"TopazSettings",
	"get_topaz_settings",
	"is_desk_permissions_enabled",
	"is_topaz_enabled",
]

DEFAULT_HOST = "http://localhost:9292"
DEFAULT_SYNC_QUEUE = "short"


@dataclass(frozen=True)
class TopazSettings:
	"""Snapshot of the Topaz configuration for the current site."""

	host: str
	api_key: str | None
	sync_queue: str
	enabled: bool
	#: Request-level memory cache toggle for permission checks.
	perm_cache_enabled: bool = True
	#: SQL IN-clause batch threshold for list filtering.
	inflight_threshold: int = 5000
	#: Optional object type override for the "user" subject type.
	subject_type: str = "user"
	#: Opt-in: enforce Frappe Desk permissions (``has_permission`` /
	#: ``permission_query_conditions``) from Topaz. Default OFF — the library's
	#: primary role is to author objects/relations in Frappe and sync them to
	#: Topaz, with enforcement happening in Topaz / external services. Enable
	#: only for Frappe apps that themselves consume Topaz for Desk access.
	desk_permissions: bool = False

	def as_dict(self) -> dict[str, Any]:
		return {
			"host": self.host,
			"api_key": "***" if self.api_key else None,
			"sync_queue": self.sync_queue,
			"enabled": self.enabled,
			"perm_cache_enabled": self.perm_cache_enabled,
			"inflight_threshold": self.inflight_threshold,
			"subject_type": self.subject_type,
			"desk_permissions": self.desk_permissions,
		}


def _conf_value(key: str, default: Any = None) -> Any:
	conf = getattr(frappe.local, "conf", None)
	if conf is None:
		return default
	return conf.get(key, default)


def get_topaz_settings() -> TopazSettings:
	"""Build a :class:`TopazSettings` from ``site_config.json``."""
	from frappe.utils import cint, sbool

	host = _conf_value("topaz_host", DEFAULT_HOST) or DEFAULT_HOST
	return TopazSettings(
		host=str(host).rstrip("/"),
		api_key=_conf_value("topaz_api_key") or None,
		sync_queue=str(_conf_value("topaz_sync_queue", DEFAULT_SYNC_QUEUE) or DEFAULT_SYNC_QUEUE),
		enabled=bool(sbool(_conf_value("topaz_enabled", False))),
		perm_cache_enabled=bool(sbool(_conf_value("topaz_perm_cache_enabled", True))),
		inflight_threshold=cint(_conf_value("topaz_inflight_threshold", 5000)) or 5000,
		subject_type=str(_conf_value("topaz_subject_type", "user") or "user"),
		desk_permissions=bool(sbool(_conf_value("topaz_desk_permissions", False))),
	)


def is_topaz_enabled() -> bool:
	"""Return True if Topaz is enabled for this site."""
	return get_topaz_settings().enabled


def is_desk_permissions_enabled() -> bool:
	"""Return True if Desk-permission enforcement via Topaz is opted in.

	Both ``topaz_enabled`` and ``topaz_desk_permissions`` must be true. The
	permission hooks short-circuit to a no-op otherwise, leaving Frappe's native
	permission system untouched.
	"""
	settings = get_topaz_settings()
	return settings.enabled and settings.desk_permissions
