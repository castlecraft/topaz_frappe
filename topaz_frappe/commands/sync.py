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
from frappe.commands import pass_context
from frappe.exceptions import SiteNotSpecifiedError
from frappe.utils.bench_helper import CliCtxObj

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
@pass_context
def topaz_sync(context: CliCtxObj, doctype=None, batch_size=500):
	"""Bulk sync database records into Topaz."""
	from topaz_frappe.config.settings import is_topaz_enabled
	from topaz_frappe.sync.engine import bulk_sync_doctype

	if not context.sites:
		raise SiteNotSpecifiedError

	for site in context.sites:
		try:
			frappe.init(site)
			frappe.connect()

			if not is_topaz_enabled():
				click.echo(f"[{site}] Topaz is not enabled (set `topaz_enabled` in site_config.json).")
				continue

			registry = _require_registry()
			doctypes = [doctype] if doctype else registry.managed_doctypes()
			if not doctypes:
				click.echo(f"[{site}] No managed DocTypes are registered with Topaz.")
				continue

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
				click.echo(
					f"[{site}] Completed with {overall_failed} failure(s). See Error Log / Topaz Sync Log."
				)
			else:
				click.echo(f"[{site}] Sync completed successfully.")
		finally:
			frappe.destroy()


@click.command("topaz-health")
@click.option("--object-type", default=None, help="Object type to probe (default: first registered).")
@click.option("--object-id", default=None, help="Object id to probe for authorization.")
@click.option("--relation", default="viewer", help="Relation to probe.")
@pass_context
def topaz_health(context: CliCtxObj, object_type=None, object_id=None, relation="viewer"):
	"""Test connectivity, latency and a sample authorization probe."""
	from topaz_frappe.config.settings import get_topaz_settings
	from topaz_frappe.transport.factory import get_transport

	if not context.sites:
		raise SiteNotSpecifiedError

	for site in context.sites:
		try:
			frappe.init(site)
			frappe.connect()

			settings = get_topaz_settings()
			click.echo(f"[{site}] Topaz host: {settings.host}")
			click.echo(f"[{site}] Enabled:    {settings.enabled}")

			if not settings.enabled:
				click.echo(f"[{site}] Topaz is disabled; nothing to probe.")
				continue

			transport = get_transport()

			start = time.perf_counter()
			result = transport.health()
			latency = (time.perf_counter() - start) * 1000.0
			click.echo(f"Health:     {result.get('ok')} ({latency:.2f} ms) host={result.get('host')}")
			if not result.get("ok"):
				click.echo(f"Error:      {result.get('error')}")
				raise SystemExit(1)

			# Optional authorization probe.
			target_obj_type = object_type
			if target_obj_type is None:
				registry = _require_registry()
				for dt in registry.doctypes():
					mapping = registry.get(dt)
					if mapping:
						target_obj_type = mapping.object_spec.object_type
						break

			if target_obj_type and object_id:
				probe_start = time.perf_counter()
				allowed = transport.check(
					object_type=target_obj_type,
					object_id=object_id,
					relation=relation,
					subject_type=settings.subject_type,
					subject_id=frappe.session.user,
				)
				probe_latency = (time.perf_counter() - probe_start) * 1000.0
				click.echo(
					f"Authz probe: {settings.subject_type}:{frappe.session.user} "
					f"{relation} on {target_obj_type}:{object_id} -> {allowed} "
					f"({probe_latency:.2f} ms)"
				)
			else:
				click.echo("Authz probe: skipped (pass --object-type and --object-id to enable).")

			click.echo("Health check passed.")
		finally:
			frappe.destroy()


@click.command("topaz-prune")
@click.option("--dry-run", is_flag=True, default=False, help="Report only; do not delete.")
@pass_context
def topaz_prune(context: CliCtxObj, dry_run=False):
	"""Reconcile Topaz against Frappe and prune dangling objects."""
	from topaz_frappe.config.settings import is_topaz_enabled
	from topaz_frappe.core.domain import TopazObject
	from topaz_frappe.transport.factory import get_transport

	if not context.sites:
		raise SiteNotSpecifiedError

	for site in context.sites:
		try:
			frappe.init(site)
			frappe.connect()

			if not is_topaz_enabled():
				click.echo(f"[{site}] Topaz is not enabled (set `topaz_enabled` in site_config.json).")
				continue

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
				click.echo(f"[{site}] Dry run complete (no changes made).")
			else:
				click.echo(f"[{site}] Prune complete: removed {total_pruned} dangling object(s).")
		finally:
			frappe.destroy()


@click.command("topaz-load-manifest")
@click.option("--file", "manifest_file", default=None, help="Path to manifest YAML file.")
@click.option("--app", default=None, help="App name to look for topaz/manifest.yaml.")
@pass_context
def topaz_load_manifest(context: CliCtxObj, manifest_file=None, app=None):
	"""Upload and apply a Topaz manifest YAML."""
	import os

	from topaz_frappe.transport.factory import get_transport

	if not context.sites:
		raise SiteNotSpecifiedError

	for site in context.sites:
		try:
			frappe.init(site)
			frappe.connect()

			target_path = manifest_file
			if not target_path:
				app_name = app or "eduauth_frappe"
				try:
					target_path = frappe.get_app_path(app_name, "topaz", "manifest.yaml")
				except Exception:
					target_path = None

			if not target_path or not os.path.exists(target_path):
				click.echo(f"[{site}] Manifest file not found: {target_path}")
				continue

			click.echo(f"[{site}] Loading manifest from: {target_path}")
			with open(target_path) as f:
				content = f.read()

			transport = get_transport()
			transport.set_manifest(content)
			click.echo(f"[{site}] Successfully loaded and applied Topaz manifest.")
		finally:
			frappe.destroy()


@click.command("topaz-stitch-manifest")
@click.option("--base-file", default=None, help="Path to base manifest YAML file.")
@click.option("--base-app", default=None, help="Base app containing topaz/manifest.yaml.")
@pass_context
def topaz_stitch_manifest(context: CliCtxObj, base_file=None, base_app=None):
	"""Stitch core manifest with all enabled Topaz External Resources and apply to Topaz."""
	from topaz_frappe.core.manifest import apply_stitched_manifest

	if not context.sites:
		raise SiteNotSpecifiedError

	for site in context.sites:
		try:
			frappe.init(site)
			frappe.connect()

			click.echo(f"[{site}] Stitching core manifest with Topaz External Resources...")
			success = apply_stitched_manifest(base_app=base_app, base_file=base_file)
			if success:
				click.echo(f"[{site}] Successfully stitched and applied manifest to Topaz.")
			else:
				click.echo(f"[{site}] Topaz is disabled or unavailable.")
		finally:
			frappe.destroy()


commands = [topaz_sync, topaz_health, topaz_prune, topaz_load_manifest, topaz_stitch_manifest]
