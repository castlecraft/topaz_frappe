"""Integration tests for Topaz-backed permissions (has_permission + filters)."""

from __future__ import annotations

import unittest

import frappe
from frappe.tests import IntegrationTestCase

from topaz_frappe.core.domain import TopazObject, TopazRelation
from topaz_frappe.core.registry import registry
from topaz_frappe.permissions.checker import custom_has_permission
from topaz_frappe.permissions.filter import (
	HARD_CAP_IDS,
	build_in_condition,
	get_permission_query_conditions,
)
from topaz_frappe.transport import factory
from topaz_frappe.transport.mock import MockTransport


class TopazPermissionTestCase(IntegrationTestCase):
	def setUp(self):
		super().setUp()
		frappe.set_user("Administrator")
		frappe.local.conf["topaz_enabled"] = 1
		# Desk-permission enforcement is opt-in; enable it for these tests.
		frappe.local.conf["topaz_desk_permissions"] = 1
		self.transport = MockTransport()
		factory.reset_transport()
		factory.set_transport(self.transport)
		frappe.flags.topaz_transport = self.transport
		for attr in ("_topaz_perm_cache",):
			if hasattr(frappe.local, attr):
				delattr(frappe.local, attr)

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

	def tearDown(self):
		if self._orig is not None:
			registry.register("ToDo", self._orig)
		else:
			registry.unregister("ToDo")
		frappe.local.conf.pop("topaz_desk_permissions", None)
		frappe.flags.topaz_transport = None
		factory.reset_transport()
		frappe.db.rollback()
		super().tearDown()

	def _make_todo(self, owner="alice@example.com"):
		todo = frappe.get_doc({"doctype": "ToDo", "description": "perm"}).insert(ignore_permissions=True)
		frappe.db.set_value("ToDo", todo.name, "owner", owner, update_modified=False)
		return todo

	# ------------------------------------------------------------------
	# has_permission
	# ------------------------------------------------------------------
	def test_admin_always_allowed(self):
		todo = self._make_todo()
		frappe.set_user("Administrator")
		self.assertTrue(custom_has_permission("ToDo", "read", doc=todo))

	def test_viewer_allowed(self):
		todo = self._make_todo()
		# alice is viewer on todo:<name>
		self.transport.set_relation(TopazRelation("todo", todo.name, "viewer", "user", "alice@example.com"))
		frappe.set_user("alice@example.com")
		self.assertTrue(custom_has_permission("ToDo", "read", doc=todo))

	def test_non_viewer_denied(self):
		todo = self._make_todo()
		self.transport.set_relation(TopazRelation("todo", todo.name, "viewer", "user", "alice@example.com"))
		frappe.set_user("bob@example.com")
		self.assertFalse(custom_has_permission("ToDo", "read", doc=todo))

	def test_editor_relation_for_write(self):
		todo = self._make_todo()
		self.transport.set_relation(TopazRelation("todo", todo.name, "editor", "user", "bob@example.com"))
		frappe.set_user("bob@example.com")
		self.assertTrue(custom_has_permission("ToDo", "write", doc=todo))
		# but not viewer unless editor implies it in the schema; mock treats them separately
		self.assertFalse(custom_has_permission("ToDo", "read", doc=todo))

	def test_unregistered_doctype_defers(self):
		registry.unregister("ToDo")
		frappe.set_user("alice@example.com")
		self.assertIsNone(custom_has_permission("ToDo", "read", doc=self._make_todo()))

	def test_no_doc_defers(self):
		frappe.set_user("alice@example.com")
		self.assertIsNone(custom_has_permission("ToDo", "read", doc=None))

	def test_ignore_permissions_bypass(self):
		todo = self._make_todo()
		frappe.set_user("bob@example.com")
		frappe.flags.ignore_permissions = True
		try:
			self.assertTrue(custom_has_permission("ToDo", "read", doc=todo))
		finally:
			frappe.flags.ignore_permissions = False

	def test_transport_failure_fails_closed(self):
		todo = self._make_todo()
		self.transport.fail_on = {"check"}
		frappe.set_user("alice@example.com")
		self.assertFalse(custom_has_permission("ToDo", "read", doc=todo))

	def test_permission_cached(self):
		todo = self._make_todo()
		self.transport.set_relation(TopazRelation("todo", todo.name, "viewer", "user", "alice@example.com"))
		frappe.set_user("alice@example.com")
		custom_has_permission("ToDo", "read", doc=todo)
		calls_after_first = self.transport.calls.get("check", 0)
		custom_has_permission("ToDo", "read", doc=todo)
		self.assertEqual(self.transport.calls.get("check", 0), calls_after_first)

	# ------------------------------------------------------------------
	# query filter
	# ------------------------------------------------------------------
	def test_filter_empty_returns_block(self):
		self.transport.set_relation(TopazRelation("todo", "OTHER", "viewer", "user", "alice@example.com"))
		frappe.set_user("alice@example.com")
		# no objects visible for this subject? there is one ("OTHER"), so not block.
		cond = get_permission_query_conditions(user="alice@example.com", doctype="ToDo")
		self.assertIn("tabToDo", cond)
		self.assertIn("OTHER", cond)

	def test_filter_no_relations_blocks_all(self):
		frappe.set_user("nobody@example.com")
		cond = get_permission_query_conditions(user="nobody@example.com", doctype="ToDo")
		self.assertEqual(cond, "1 = 0")

	def test_filter_wildcard_allows_all(self):
		self.transport.set_relation(TopazRelation("todo", "*", "viewer", "user", "alice@example.com"))
		cond = get_permission_query_conditions(user="alice@example.com", doctype="ToDo")
		self.assertEqual(cond, "")

	def test_filter_lists_allowed_ids(self):
		for oid in ("T1", "T2"):
			self.transport.set_relation(TopazRelation("todo", oid, "viewer", "user", "alice@example.com"))
		self.transport.set_relation(TopazRelation("todo", "T3", "viewer", "user", "bob@example.com"))
		cond = get_permission_query_conditions(user="alice@example.com", doctype="ToDo")
		self.assertIn("T1", cond)
		self.assertIn("T2", cond)
		self.assertNotIn("T3", cond)

	def test_filter_admin_no_restriction(self):
		cond = get_permission_query_conditions(user="Administrator", doctype="ToDo")
		self.assertEqual(cond, "")

	def test_filter_unregistered_doctype(self):
		registry.unregister("ToDo")
		cond = get_permission_query_conditions(user="alice@example.com", doctype="ToDo")
		self.assertEqual(cond, "")

	def test_filter_applied_to_real_query(self):
		# Create two ToDos, grant visibility of only one to alice.
		t1 = self._make_todo(owner="alice@example.com")
		t2 = self._make_todo(owner="alice@example.com")
		self.transport.set_relation(TopazRelation("todo", t1.name, "viewer", "user", "alice@example.com"))
		frappe.set_user("alice@example.com")
		# Rebuild hooks-driven filter by clearing perm cache.
		frappe.local._topaz_perm_cache = {}
		rows = frappe.get_list("ToDo", fields=["name"], limit_page_length=0)
		names = {r.name for r in rows}
		self.assertIn(t1.name, names)
		self.assertNotIn(t2.name, names)

	# ------------------------------------------------------------------
	# opt-in gate: hooks are no-ops unless topaz_desk_permissions is set
	# ------------------------------------------------------------------
	def test_hooks_noop_when_desk_permissions_disabled(self):
		from topaz_frappe.permissions.hooks import (
			has_permission_dispatcher,
			query_conditions_dispatcher,
		)

		todo = self._make_todo()
		frappe.set_user("bob@example.com")
		# Even though bob has no relation, with the gate OFF Topaz must not
		# interfere: filter is empty and has_permission defers (True).
		frappe.local.conf["topaz_desk_permissions"] = 0
		self.assertEqual(query_conditions_dispatcher(user="bob@example.com", doctype="ToDo"), "")
		self.assertTrue(has_permission_dispatcher(doc=todo, ptype="read", user="bob@example.com"))

	def test_hooks_enforce_when_desk_permissions_enabled(self):
		from topaz_frappe.permissions.hooks import (
			has_permission_dispatcher,
			query_conditions_dispatcher,
		)

		todo = self._make_todo()
		frappe.set_user("bob@example.com")
		# Gate ON (set in setUp): bob is denied.
		self.assertFalse(has_permission_dispatcher(doc=todo, ptype="read", user="bob@example.com"))
		cond = query_conditions_dispatcher(user="bob@example.com", doctype="ToDo")
		self.assertEqual(cond, "1 = 0")


class TestBuildInCondition(unittest.TestCase):
	def test_single_chunk(self):
		cond = build_in_condition("ToDo", ["A", "B"], threshold=10)
		self.assertIn("tabToDo", cond)
		self.assertIn("'A'", cond)
		self.assertIn("'B'", cond)
		self.assertTrue(cond.startswith("`tabToDo`"))

	def test_chunking(self):
		ids = [f"T{i}" for i in range(25)]
		cond = build_in_condition("ToDo", ids, threshold=10)
		# 25 ids / 10 -> 3 IN blocks OR-ed.
		self.assertEqual(cond.count(" IN ("), 3)

	def test_escapes_quotes(self):
		cond = build_in_condition("ToDo", ["O'Brien"], threshold=10)
		self.assertIn("O\\'Brien", cond)


if __name__ == "__main__":
	unittest.main()
