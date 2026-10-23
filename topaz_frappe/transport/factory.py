"""Transport factory.

Resolution order (first match wins):

1. ``frappe.flags.topaz_transport`` — explicit injection (tests, CLI). Per
   request, thread-local, highest priority.
2. ``frappe.local._topaz_transport`` — explicit pin via :func:`set_transport`.
3. A **thread-local, process-reused** default transport keyed by host.

Why thread-local for the default (and not ``frappe.local``)? Under gunicorn's
sync/gevent workers each request runs on one thread, and ``frappe.local`` is
torn down per request. Caching the HTTP transport there would build a brand-new
``requests.Session`` (and throw away its connection pool) on *every* request.
Instead we keep one transport per *worker thread*, keyed by the configured
host, so connection pooling works across the many requests a thread serves.
The transport is rebuilt automatically if the host/credentials change. A
``requests.Session`` is not documented as thread-safe, which is exactly why the
default is thread-local (one session per thread) and never a shared global.
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from typing import TYPE_CHECKING, Optional

import frappe

if TYPE_CHECKING:
	from topaz_frappe.transport.base import TopazTransportPort

__all__ = [
	"get_transport",
	"reset_transport",
	"set_transport",
	"set_transport_factory",
]

#: Optional factory override. Process-global but only intended for tests, which
#: run single-threaded. Not used in production request paths.
_factory: Callable[[], "TopazTransportPort"] | None = None

_LOCAL_ATTR = "_topaz_transport"

#: Per-thread default transport cache: thread id -> (cache_key, transport).
#: Kept module-level (shared per process) but the value is keyed by thread so
#: each worker thread gets its own connection pool. Guarded by a lock for
#: safety on the rare concurrent access.
_thread_transports: dict[int, tuple[str, "TopazTransportPort"]] = {}
_thread_lock = threading.Lock()


def set_transport_factory(factory: Callable[[], "TopazTransportPort"] | None) -> None:
	"""Override how transports are constructed (mainly for tests)."""
	global _factory
	_factory = factory


def set_transport(transport: "TopazTransportPort") -> None:
	"""Pin a specific transport instance for the current request."""
	setattr(frappe.local, _LOCAL_ATTR, transport)


def reset_transport() -> None:
	"""Clear the current request's transport pin and the factory override.

	Does not touch the thread-local default pool; use :func:`_clear_thread_pool`
	(or process restart) for that.
	"""
	global _factory
	_factory = None
	if hasattr(frappe.local, _LOCAL_ATTR):
		delattr(frappe.local, _LOCAL_ATTR)
	with _thread_lock:
		_thread_transports.pop(threading.get_ident(), None)


def _build_default() -> "TopazTransportPort":
	if _factory is not None:
		return _factory()
	from topaz_frappe.transport.http import HttpTransport

	return HttpTransport()


def _default_transport() -> "TopazTransportPort":
	"""Return a per-thread, host-keyed default transport (pooled across calls)."""
	from topaz_frappe.config.settings import get_topaz_settings

	settings = get_topaz_settings()
	cache_key = f"{settings.host}|{settings.api_key or ''}"
	tid = threading.get_ident()
	with _thread_lock:
		entry = _thread_transports.get(tid)
		if entry is not None and entry[0] == cache_key:
			return entry[1]
	transport = _build_default()
	with _thread_lock:
		_thread_transports[tid] = (cache_key, transport)
	return transport


def get_transport() -> "TopazTransportPort":
	"""Return the transport for the current request/job/thread.

	Priority: ``frappe.flags.topaz_transport`` (injection) ->
	``frappe.local`` pin -> per-thread pooled default.
	"""
	# 1. Explicit injection (tests / CLI overrides).
	flag_transport = getattr(frappe.flags, "topaz_transport", None)
	if flag_transport is not None:
		return flag_transport

	# 2. Explicit per-request pin.
	cached = getattr(frappe.local, _LOCAL_ATTR, None)
	if cached is not None:
		return cached

	# 3. Per-thread pooled default.
	return _default_transport()
