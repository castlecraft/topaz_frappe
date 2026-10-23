"""Bench CLI commands for Topaz synchronisation.

Exposed via the app's ``commands`` module attribute:

* ``bench topaz-sync``   — bulk (re)sync DB records into Topaz.
* ``bench topaz-health`` — connectivity / latency / authorization probe.
* ``bench topaz-prune``  — remove dangling Topaz objects missing in Frappe.

Every command runs inside the site context provided by ``bench --site``.
"""

from __future__ import annotations

import time

import click
import frappe

__all__ = ["commands"]


def _require_registry():
	from topaz_frappe.core.registry import registry

	try:
		return registry
	except Exception:
		frappe.throw("Failed to load the Topaz registry.")


@click.command("topaz-sync")
@click.option("--doctype", default=None, help="Restrict sync to a single registered DocType.")
@click.option("--batch-size", default=500, type=int, help="Progress report batch size.")
def topaz_sync(doctype=None, batch_size=500):
	"""Bulk sync database records into Topaz."""
	from topaz_frappe.config.settings import is_topaz_enabled
	from topaz_frappe.sync.engine import bulk_sync_doctype

	if not is_topaz_enabled():
		click.echo("Topaz is not enabled (set `topaz_enabled` in site_config.json).")
		return

	registry = _require_registry()
	doctypes = [doctype] if doctype else registry.managed_doctypes()
	if not doctypes:
		click.echo("No managed DocTypes are registered with Topaz.")
		return

	overall_failed = 0
	for dt in doctypes:
		mapping = registry.get(dt)
		if mapping is None:
			click.echo(f"Skipping '{dt}': not registered with Topaz.")
			continue
		if not getattr(mapping, "managed", True):
			click.echo(f"Skipping '{dt}': marked managed=False (owned by an external service).")
			continue

		click.echo(f"Syncing '{dt}' ...")

		def _progress(done: int, total: int) -> None:
			click.echo(f"  {dt}: {done}/{total}")

		summary = bulk_sync_doctype(dt, batch_size=batch_size, progress=_progress)
		overall_failed += summary["failed"]
		click.echo(
			f"  {dt}: processed={summary['processed']} failed={summary['failed']} total={summary['total']}"
		)

	if overall_failed:
		click.echo(f"Completed with {overall_failed} failure(s). See Error Log / Topaz Sync Log.")
	else:
		click.echo("Sync completed successfully.")


@click.command("topaz-health")
@click.option("--object-type", default=None, help="Object type to probe (default: first registered).")
@click.option("--object-id", default=None, help="Object id to probe for authorization.")
@click.option("--relation", default="viewer", help="Relation to probe.")
def topaz_health(object_type=None, object_id=None, relation="viewer"):
	"""Test connectivity, latency and a sample authorization probe."""
	from topaz_frappe.config.settings import get_topaz_settings
	from topaz_frappe.transport.factory import get_transport

	settings = get_topaz_settings()
	click.echo(f"Topaz host: {settings.host}")
	click.echo(f"Enabled:    {settings.enabled}")

	if not settings.enabled:
		click.echo("Topaz is disabled; nothing to probe.")
		return

	transport = get_transport()

	start = time.perf_counter()
	result = transport.health()
	latency = (time.perf_counter() - start) * 1000.0
	click.echo(f"Health:     {result.get('ok')} ({latency:.2f} ms) host={result.get('host')}")
	if not result.get("ok"):
		click.echo(f"Error:      {result.get('error')}")
		raise SystemExit(1)

	# Optional authorization probe.
	if object_type is None:
		registry = _require_registry()
		for dt in registry.doctypes():
			mapping = registry.get(dt)
			if mapping:
				object_type = mapping.object_spec.object_type
				break

	if object_type and object_id:
		probe_start = time.perf_counter()
		allowed = transport.check(
			object_type=object_type,
			object_id=object_id,
			relation=relation,
			subject_type=settings.subject_type,
			subject_id=frappe.session.user,
		)
		probe_latency = (time.perf_counter() - probe_start) * 1000.0
		click.echo(
			f"Authz probe: {settings.subject_type}:{frappe.session.user} "
			f"{relation} on {object_type}:{object_id} -> {allowed} "
			f"({probe_latency:.2f} ms)"
		)
	else:
		click.echo("Authz probe: skipped (pass --object-type and --object-id to enable).")

	click.echo("Health check passed.")


@click.command("topaz-prune")
@click.option("--dry-run", is_flag=True, default=False, help="Report only; do not delete.")
def topaz_prune(dry_run=False):
	"""Reconcile Topaz against Frappe and prune dangling objects."""
	from topaz_frappe.config.settings import is_topaz_enabled
	from topaz_frappe.core.domain import TopazObject
	from topaz_frappe.transport.factory import get_transport

	if not is_topaz_enabled():
		click.echo("Topaz is not enabled (set `topaz_enabled` in site_config.json).")
		return

	registry = _require_registry()
	transport = get_transport()
	total_pruned = 0

	for dt in registry.managed_doctypes():
		mapping = registry.get(dt)
		if mapping is None:
			continue
		object_type = mapping.object_spec.object_type

		try:
			topaz_ids = set(transport.list_objects(object_type))
		except Exception as exc:
			click.echo(f"  {dt}: could not list Topaz objects ({exc}); skipping.")
			continue

		# Build the set of ids that *should* exist from Frappe.
		db_names = frappe.get_all(dt, pluck="name", limit_page_length=0)
		transform = mapping.object_spec.id_transform
		expected_ids = set()
		for name in db_names:
			value = str(name)
			if mapping.object_spec.id_field != "name":
				value = frappe.db.get_value(dt, name, mapping.object_spec.id_field) or ""
				value = str(value)
			if transform:
				value = transform(value)
			if value:
				expected_ids.add(value)

		dangling = topaz_ids - expected_ids
		click.echo(
			f"  {dt} ({object_type}): topaz={len(topaz_ids)} expected={len(expected_ids)} "
			f"dangling={len(dangling)}"
		)
		for object_id in sorted(dangling):
			if dry_run:
				click.echo(f"    would prune {object_type}:{object_id}")
			else:
				transport.delete_object(TopazObject(object_type, object_id))
				total_pruned += 1

	if dry_run:
		click.echo("Dry run complete (no changes made).")
	else:
		click.echo(f"Prune complete: removed {total_pruned} dangling object(s).")


commands = [topaz_sync, topaz_health, topaz_prune]
