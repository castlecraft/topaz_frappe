"""Unit tests for declarative relation mapping specs and the registry builder."""

from __future__ import annotations

import unittest

from topaz_frappe.core.domain import (
	ChildTableRelation,
	ComputedRelation,
	DocTypeMapping,
	LinkRelation,
	TopazObject,
	TopazObjectSpec,
	TopazRelation,
)


class _Row(dict):
	"""Mimic a frappe._dict child row."""


class _Doc(dict):
	"""Mimic a frappe Document (has .get) for pure mapping tests."""

	pass


class TestLinkRelation(unittest.TestCase):
	def test_basic_owner(self):
		spec = LinkRelation(relation="owner", target_type="customer", fieldname="customer")
		obj = TopazObject("invoice", "INV-1")
		doc = _Doc(customer="ACME")
		rels = spec.get_relations(doc, obj)
		self.assertEqual(len(rels), 1)
		self.assertEqual(rels[0].subject_type, "customer")
		self.assertEqual(rels[0].subject_id, "ACME")
		self.assertEqual(rels[0].relation, "owner")

	def test_empty_value(self):
		spec = LinkRelation(relation="owner", target_type="customer", fieldname="customer")
		obj = TopazObject("invoice", "INV-1")
		self.assertEqual(spec.get_relations(_Doc(customer=None), obj), [])
		self.assertEqual(spec.get_relations(_Doc(), obj), [])

	def test_subject_relation(self):
		spec = LinkRelation(
			relation="owner", target_type="team", fieldname="owning_team", subject_relation="member"
		)
		rels = spec.get_relations(_Doc(owning_team="eng"), TopazObject("doc", "D1"))
		self.assertEqual(rels[0].subject_relation, "member")

	def test_field_alias(self):
		spec = LinkRelation(relation="owner", target_type="customer", fieldname="", field="customer")
		# __post_init__ should copy field -> fieldname
		self.assertEqual(spec.fieldname, "customer")


class TestChildTableRelation(unittest.TestCase):
	def test_static_relation_per_row(self):
		spec = ChildTableRelation(table="user_permissions", subject_field="user", static_relation="viewer")
		doc = _Doc(user_permissions=[_Row(user="alice"), _Row(user="bob")])
		rels = spec.get_relations(doc, TopazObject("document", "D1"))
		self.assertEqual({r.subject_id for r in rels}, {"alice", "bob"})
		self.assertTrue(all(r.relation == "viewer" for r in rels))

	def test_dynamic_relation_field(self):
		spec = ChildTableRelation(table="shares", subject_field="user", relation_field="role")
		doc = _Doc(shares=[_Row(user="alice", role="editor"), _Row(user="bob", role="viewer")])
		rels = spec.get_relations(doc, TopazObject("document", "D1"))
		by_subject = {r.subject_id: r.relation for r in rels}
		self.assertEqual(by_subject, {"alice": "editor", "bob": "viewer"})

	def test_condition_filters_rows(self):
		spec = ChildTableRelation(
			table="shares",
			subject_field="user",
			static_relation="viewer",
			condition=lambda row: row.get("active"),
		)
		doc = _Doc(shares=[_Row(user="alice", active=1), _Row(user="bob", active=0)])
		rels = spec.get_relations(doc, TopazObject("document", "D1"))
		self.assertEqual([r.subject_id for r in rels], ["alice"])

	def test_missing_subject_skipped(self):
		spec = ChildTableRelation(table="shares", subject_field="user", static_relation="viewer")
		doc = _Doc(shares=[_Row(user=None), _Row(user="bob")])
		rels = spec.get_relations(doc, TopazObject("document", "D1"))
		self.assertEqual([r.subject_id for r in rels], ["bob"])

	def test_requires_relation_source(self):
		with self.assertRaises(ValueError):
			ChildTableRelation(table="shares", subject_field="user")

	def test_no_rows(self):
		spec = ChildTableRelation(table="shares", subject_field="user", static_relation="viewer")
		self.assertEqual(spec.get_relations(_Doc(), TopazObject("document", "D1")), [])


class TestComputedRelation(unittest.TestCase):
	def test_generator_returns_list(self):
		def gen(doc):
			return [TopazRelation("document", doc["name"], "viewer", "user", "alice")]

		spec = ComputedRelation(generator=gen)
		obj = TopazObject("document", "D1")
		rels = spec.get_relations(_Doc(name="D1"), obj)
		self.assertEqual(len(rels), 1)
		self.assertEqual(rels[0].subject_id, "alice")

	def test_generator_none(self):
		spec = ComputedRelation(generator=lambda doc: None)
		self.assertEqual(spec.get_relations(_Doc(), TopazObject("document", "D1")), [])

	def test_generator_single_relation(self):
		spec = ComputedRelation(
			generator=lambda doc: TopazRelation("document", "D1", "viewer", "user", "alice")
		)
		rels = spec.get_relations(_Doc(), TopazObject("document", "D1"))
		self.assertEqual(len(rels), 1)

	def test_generator_rebinds_object(self):
		def gen(doc):
			# Wrong object id; should be rebound to the resolved object.
			return [TopazRelation("document", "WRONG", "viewer", "user", "alice")]

		spec = ComputedRelation(generator=gen)
		obj = TopazObject("document", "D1")
		rels = spec.get_relations(_Doc(), obj)
		self.assertEqual(rels[0].object_id, "D1")

	def test_generator_rejects_bad_type(self):
		spec = ComputedRelation(generator=lambda doc: ["not a relation"])
		with self.assertRaises(TypeError):
			spec.get_relations(_Doc(), TopazObject("document", "D1"))


class TestDocTypeMapping(unittest.TestCase):
	def test_combines_specs(self):
		mapping = DocTypeMapping(
			object_spec=TopazObjectSpec(object_type="document", id_field="name"),
			relations=[
				LinkRelation(relation="owner", target_type="user", fieldname="owner"),
				ChildTableRelation(table="shares", subject_field="user", static_relation="viewer"),
			],
		)
		doc = _Doc(name="D1", owner="alice", shares=[_Row(user="bob")])
		rels = mapping.get_relations(doc)
		subjects = {(r.relation, r.subject_id) for r in rels}
		self.assertIn(("owner", "alice"), subjects)
		self.assertIn(("viewer", "bob"), subjects)


class TestRegistryBuilder(unittest.TestCase):
	def test_build_from_dict(self):
		from topaz_frappe.core.registry import _build_mapping_from_dict

		mapping = _build_mapping_from_dict(
			{
				"object": {"type": "document", "id_field": "name"},
				"relations": [
					{"kind": "link", "relation": "owner", "target_type": "user", "field": "owner"},
					{
						"kind": "child",
						"table": "shares",
						"subject_field": "user",
						"static_relation": "viewer",
					},
				],
			}
		)
		self.assertEqual(mapping.object_spec.object_type, "document")
		self.assertEqual(len(mapping.relations), 2)
		self.assertIsInstance(mapping.relations[0], LinkRelation)
		self.assertIsInstance(mapping.relations[1], ChildTableRelation)

	def test_infer_kind(self):
		from topaz_frappe.core.registry import _build_relation

		# table -> child
		rel = _build_relation({"table": "shares", "subject_field": "user", "static_relation": "v"})
		self.assertIsInstance(rel, ChildTableRelation)
		# generator -> computed
		rel = _build_relation({"generator": lambda d: None})
		self.assertIsInstance(rel, ComputedRelation)
		# default -> link
		rel = _build_relation({"relation": "owner", "target": "user", "field": "owner"})
		self.assertIsInstance(rel, LinkRelation)

	def test_missing_type_raises(self):
		from topaz_frappe.core.registry import _build_mapping_from_dict

		with self.assertRaises(ValueError):
			_build_mapping_from_dict({"object": {}, "relations": []})

	def test_permission_relations_parsed(self):
		from topaz_frappe.core.registry import _build_mapping_from_dict

		mapping = _build_mapping_from_dict(
			{
				"object": {"type": "committee", "id_field": "name"},
				"relations": [],
				"permissions": {"read": "can_access_committee", "write": "chair"},
			}
		)
		self.assertEqual(
			mapping.permission_relations,
			{"read": "can_access_committee", "write": "chair"},
		)
		self.assertEqual(mapping.get_permission_relation("read", "viewer"), "can_access_committee")
		# Unmapped ptype falls back to the supplied default.
		self.assertEqual(mapping.get_permission_relation("delete", "admin"), "admin")


if __name__ == "__main__":
	unittest.main()
