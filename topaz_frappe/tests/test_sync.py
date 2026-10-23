"""Integration tests for the sync engine against a Frappe site.

Run with::

	bench --site <site> run-tests --app topaz_frappe --module topaz_frappe.tests.test_sync

These tests use the in-memory :class:`MockTransport` injected via
``frappe.flags.topaz_transport`` so they exercise the full sync pipeline
(registry -> resolve -> diff -> transport) without a live Topaz instance.

We use the built-in ``ToDo`` DocType (which always exists) and register a
throw-away mapping for it, restoring the registry afterwards.
"""

from __future__ import annotations

import frappe
from frappe.tests import IntegrationTestCase

from topaz_frappe.core.domain import TopazObject
from topaz_frappe.core.registry import registry
from topaz_frappe.transport import factory
from topaz_frappe.transport.mock import MockTransport


class TopazSyncTestCase(IntegrationTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		frappe.flags.topaz_enabled = True

	def setUp(self):
		super().setUp()
		frappe.set_user("Administrator")
		# Fresh mock transport for every test.
		self.transport = MockTransport()
		factory.reset_transport()
		factory.set_transport(self.transport)
		frappe.flags.topaz_transport = self.transport
		frappe.flags.topaz_sync_immediate = True

		# Register a mapping for ToDo: object = todo:<name>,
		# owner relation -> user:<owner>.
		self._orig = registry.get("ToDo")
		registry.register_dict(
			"ToDo",
			{
				"object": {"type": "todo", "id_field": "name"},
				"relations": [
					{"kind": "link", "relation": "owner", "target_type": "user", "field": "owner"},
				],
			},
		)
		# Reload settings caches.
		self._clear_cache()

	def tearDown(self):
		if self._orig is not None:
			registry.register("ToDo", self._orig)
		else:
			registry.unregister("ToDo")
		frappe.flags.topaz_sync_immediate = False
		frappe.flags.topaz_transport = None
		factory.reset_transport()
		frappe.db.rollback()
		super().tearDown()

	def _clear_cache(self):
		# Perm filters / perm cache live on frappe.local.
		for attr in ("_topaz_perm_cache", "_topaz_pending_jobs"):
			if hasattr(frappe.local, attr):
				delattr(frappe.local, attr)

	def _enable_site(self):
		# Force get_topaz_settings().enabled == True for this test.
		frappe.local.conf["topaz_enabled"] = 1

	# ------------------------------------------------------------------
	def test_upsert_creates_object_and_relation(self):
		self._enable_site()
		todo = frappe.get_doc({"doctype": "ToDo", "description": "hello"}).insert()

		self.assertTrue(self.transport.object_exists(TopazObject("todo", todo.name)))
		rels = self.transport.relations()
		self.assertEqual(len(rels), 1)
		self.assertEqual(rels[0].relation, "owner")
		self.assertEqual(rels[0].subject_id, "Administrator")

	def test_update_moves_relation(self):
		self._enable_site()
		todo = frappe.get_doc({"doctype": "ToDo", "description": "x"}).insert()
		# Reassign owner -> should delete old owner relation, add new one.
		todo.owner = "Administrator"
		todo.save()

		# Change to another owner and sync again.
		frappe.db.set_value("ToDo", todo.name, "owner", "Guest", update_modified=False)
		from topaz_frappe.sync.engine import sync_upsert

		sync_upsert("ToDo", todo.name)

		rels = [r for r in self.transport.relations() if r.object_id == todo.name]
		self.assertEqual(len(rels), 1)
		self.assertEqual(rels[0].subject_id, "Guest")

	def test_delete_removes_object(self):
		self._enable_site()
		todo = frappe.get_doc({"doctype": "ToDo", "description": "x"}).insert()
		self.assertTrue(self.transport.object_exists(TopazObject("todo", todo.name)))

		frappe.delete_doc("ToDo", todo.name)
		# delete fires on_trash -> sync_delete (immediate).
		rels = [r for r in self.transport.relations() if r.object_id == todo.name]
		self.assertEqual(rels, [])

	def test_no_registry_no_sync(self):
		self._enable_site()
		registry.unregister("ToDo")
		frappe.get_doc({"doctype": "ToDo", "description": "x"}).insert()
		# No todo objects were ever created.
		self.assertEqual([r for r in self.transport.relations() if r.object_type == "todo"], [])

	def test_sync_log_written(self):
		self._enable_site()
		before = frappe.db.count("Topaz Sync Log")
		frappe.get_doc({"doctype": "ToDo", "description": "x"}).insert()
		after = frappe.db.count("Topaz Sync Log")
		self.assertGreater(after, before)

	def test_bulk_sync(self):
		self._enable_site()
		from topaz_frappe.sync.engine import bulk_sync_doctype

		for i in range(3):
			frappe.get_doc({"doctype": "ToDo", "description": f"t{i}"}).insert(ignore_permissions=True)
		summary = bulk_sync_doctype("ToDo")
		self.assertGreaterEqual(summary["processed"], 3)
		self.assertEqual(summary["failed"], 0)
