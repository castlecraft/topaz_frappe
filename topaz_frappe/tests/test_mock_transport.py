"""Tests for the in-memory MockTransport, including subject-set semantics."""

from __future__ import annotations

import unittest

from topaz_frappe.core.domain import TopazObject, TopazRelation
from topaz_frappe.transport.base import TopazTransportError, TopazTransportPort
from topaz_frappe.transport.mock import MockTransport


class TestMockTransportBasics(unittest.TestCase):
	def setUp(self):
		self.t = MockTransport()

	def test_implements_protocol(self):
		self.assertIsInstance(self.t, TopazTransportPort)

	def test_put_and_object_exists(self):
		obj = TopazObject("doc", "D1")
		self.assertTrue(self.t.put_object(obj))
		self.assertTrue(self.t.object_exists(obj))

	def test_delete_object_cascades(self):
		self.t.set_relation(TopazRelation("doc", "D1", "viewer", "user", "alice"))
		self.t.put_object(TopazObject("doc", "D1"))
		self.t.delete_object(TopazObject("doc", "D1"))
		self.assertFalse(self.t.object_exists(TopazObject("doc", "D1")))
		self.assertEqual(self.t.relations(), [])

	def test_set_and_delete_relation(self):
		rel = TopazRelation("doc", "D1", "viewer", "user", "alice")
		self.t.set_relation(rel)
		self.assertTrue(self.t.check("doc", "D1", "viewer", "user", "alice"))
		self.t.delete_relation(rel)
		self.assertFalse(self.t.check("doc", "D1", "viewer", "user", "alice"))

	def test_check_missing(self):
		self.assertFalse(self.t.check("doc", "D1", "viewer", "user", "nobody"))

	def test_idempotent_set(self):
		rel = TopazRelation("doc", "D1", "viewer", "user", "alice")
		self.t.set_relation(rel)
		self.t.set_relation(rel)
		self.assertEqual(len(self.t.relations()), 1)

	def test_forced_failure(self):
		t = MockTransport(fail_on={"put_object"})
		with self.assertRaises(TopazTransportError):
			t.put_object(TopazObject("doc", "D1"))


class TestSubjectSets(unittest.TestCase):
	def setUp(self):
		self.t = MockTransport()

	def test_group_membership_grants(self):
		# document:D1#viewer@group:eng#member, and group:eng#member@user:alice
		self.t.set_relation(TopazRelation("doc", "D1", "viewer", "group", "eng", subject_relation="member"))
		self.t.set_relation(TopazRelation("group", "eng", "member", "user", "alice"))
		self.assertTrue(self.t.check("doc", "D1", "viewer", "user", "alice"))

	def test_group_membership_denies_non_member(self):
		self.t.set_relation(TopazRelation("doc", "D1", "viewer", "group", "eng", subject_relation="member"))
		self.t.set_relation(TopazRelation("group", "eng", "member", "user", "alice"))
		self.assertFalse(self.t.check("doc", "D1", "viewer", "user", "bob"))

	def test_nested_subject_set(self):
		# doc -> group:eng#member, group:eng#member -> team:platform#member,
		# team:platform#member -> user:alice  (transitive)
		self.t.set_relation(TopazRelation("doc", "D1", "viewer", "group", "eng", subject_relation="member"))
		self.t.set_relation(
			TopazRelation("group", "eng", "member", "team", "platform", subject_relation="member")
		)
		self.t.set_relation(TopazRelation("team", "platform", "member", "user", "alice"))
		self.assertTrue(self.t.check("doc", "D1", "viewer", "user", "alice"))

	def test_cycle_does_not_hang(self):
		self.t.set_relation(TopazRelation("group", "a", "member", "group", "b", subject_relation="member"))
		self.t.set_relation(TopazRelation("group", "b", "member", "group", "a", subject_relation="member"))
		self.assertFalse(self.t.check("group", "a", "member", "user", "x"))

	def test_direct_subject_relation_match(self):
		self.t.set_relation(TopazRelation("doc", "D1", "viewer", "group", "eng", subject_relation="member"))
		# Checking for the exact subject-set subject should match directly.
		self.assertTrue(self.t.check("doc", "D1", "viewer", "group", "eng", subject_relation="member"))


class TestReverseLookups(unittest.TestCase):
	def setUp(self):
		self.t = MockTransport()

	def test_search_subjects(self):
		self.t.set_relation(TopazRelation("doc", "D1", "viewer", "user", "alice"))
		self.t.set_relation(TopazRelation("doc", "D1", "viewer", "user", "bob"))
		self.assertEqual(self.t.search_subjects("doc", "D1", "viewer", "user"), ["alice", "bob"])

	def test_search_resources(self):
		self.t.set_relation(TopazRelation("doc", "D1", "viewer", "user", "alice"))
		self.t.set_relation(TopazRelation("doc", "D2", "viewer", "user", "alice"))
		self.t.set_relation(TopazRelation("doc", "D3", "viewer", "user", "bob"))
		self.assertEqual(self.t.search_resources("doc", "viewer", "user", "alice"), ["D1", "D2"])

	def test_search_resources_via_group(self):
		self.t.set_relation(TopazRelation("doc", "D1", "viewer", "group", "eng", subject_relation="member"))
		self.t.set_relation(TopazRelation("group", "eng", "member", "user", "alice"))
		self.assertIn("D1", self.t.search_resources("doc", "viewer", "user", "alice"))

	def test_list_objects(self):
		self.t.put_object(TopazObject("doc", "D1"))
		self.t.put_object(TopazObject("doc", "D2"))
		self.t.put_object(TopazObject("other", "X"))
		self.assertEqual(self.t.list_objects("doc"), ["D1", "D2"])

	def test_health(self):
		health = self.t.health()
		self.assertTrue(health["ok"])


if __name__ == "__main__":
	unittest.main()
