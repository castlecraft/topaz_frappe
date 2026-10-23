"""Unit tests for relationship diff computation and domain value objects."""

from __future__ import annotations

import unittest


# Import without initialising Frappe: the diff/domain modules are pure python.
def _rel(rel, subject_id, subject_type="user", subject_relation=None, object_id="O1"):
	from topaz_frappe.core.domain import TopazRelation

	return TopazRelation(
		object_type="document",
		object_id=object_id,
		relation=rel,
		subject_type=subject_type,
		subject_id=subject_id,
		subject_relation=subject_relation,
	)


class TestComputeDiff(unittest.TestCase):
	def test_empty_to_empty(self):
		from topaz_frappe.sync.diff import compute_diff

		diff = compute_diff([], [])
		self.assertTrue(diff.is_empty)
		self.assertEqual(diff.to_add, [])
		self.assertEqual(diff.to_delete, [])

	def test_all_added(self):
		from topaz_frappe.sync.diff import compute_diff

		new = [_rel("viewer", "alice"), _rel("editor", "bob")]
		diff = compute_diff([], new)
		self.assertEqual(len(diff.to_add), 2)
		self.assertEqual(diff.to_delete, [])
		self.assertFalse(diff.is_empty)

	def test_all_removed(self):
		from topaz_frappe.sync.diff import compute_diff

		old = [_rel("viewer", "alice")]
		diff = compute_diff(old, [])
		self.assertEqual(diff.to_add, [])
		self.assertEqual(len(diff.to_delete), 1)

	def test_no_change(self):
		from topaz_frappe.sync.diff import compute_diff

		old = [_rel("viewer", "alice"), _rel("editor", "bob")]
		new = list(old)
		diff = compute_diff(old, new)
		self.assertTrue(diff.is_empty)
		self.assertEqual(len(diff.unchanged), 2)

	def test_mixed_add_remove_unchanged(self):
		from topaz_frappe.sync.diff import compute_diff

		old = [_rel("viewer", "alice"), _rel("editor", "bob")]
		new = [_rel("viewer", "alice"), _rel("editor", "carol")]
		diff = compute_diff(old, new)
		self.assertEqual(len(diff.to_add), 1)
		self.assertEqual(diff.to_add[0].subject_id, "carol")
		self.assertEqual(len(diff.to_delete), 1)
		self.assertEqual(diff.to_delete[0].subject_id, "bob")
		self.assertEqual(len(diff.unchanged), 1)

	def test_subject_relation_distinguishes(self):
		from topaz_frappe.sync.diff import compute_diff

		old = [_rel("viewer", "eng", subject_type="group", subject_relation="member")]
		new = [_rel("viewer", "eng", subject_type="group")]  # no subject_relation
		diff = compute_diff(old, new)
		self.assertEqual(len(diff.to_add), 1)
		self.assertEqual(len(diff.to_delete), 1)
		self.assertIsNone(diff.to_add[0].subject_relation)
		self.assertEqual(diff.to_delete[0].subject_relation, "member")

	def test_order_independence(self):
		from topaz_frappe.sync.diff import compute_diff

		a = _rel("viewer", "alice")
		b = _rel("editor", "bob")
		diff1 = compute_diff([a, b], [b, a])
		diff2 = compute_diff([b, a], [a, b])
		self.assertTrue(diff1.is_empty)
		self.assertTrue(diff2.is_empty)

	def test_field_change_moves_relation(self):
		from topaz_frappe.sync.diff import compute_diff

		# customer changed from ACME to GLOBEX -> delete old owner, add new owner
		old = [_rel("owner", "ACME", subject_type="customer")]
		new = [_rel("owner", "GLOBEX", subject_type="customer")]
		diff = compute_diff(old, new)
		self.assertEqual(len(diff.to_add), 1)
		self.assertEqual(diff.to_add[0].subject_id, "GLOBEX")
		self.assertEqual(len(diff.to_delete), 1)
		self.assertEqual(diff.to_delete[0].subject_id, "ACME")


class TestDomainObjects(unittest.TestCase):
	def test_topaz_object_api_dict(self):
		from topaz_frappe.core.domain import TopazObject

		obj = TopazObject("document", "README")
		self.assertEqual(obj.key(), ("document", "README"))
		self.assertEqual(obj.as_api_dict(), {"objectType": "document", "objectId": "README"})
		self.assertEqual(str(obj), "document:README")

	def test_relation_api_dict_direct_subject(self):
		from topaz_frappe.core.domain import TopazRelation

		rel = TopazRelation("document", "D1", "viewer", "user", "alice")
		payload = rel.as_api_dict()
		self.assertEqual(payload["relationship"]["resource"], {"objectType": "document", "objectId": "D1"})
		self.assertEqual(payload["relationship"]["relation"], "viewer")
		self.assertEqual(
			payload["relationship"]["subject"],
			{"object": {"objectType": "user", "objectId": "alice"}},
		)
		self.assertNotIn("relation", payload["relationship"]["subject"])

	def test_relation_api_dict_subject_set(self):
		from topaz_frappe.core.domain import TopazRelation

		rel = TopazRelation("document", "D1", "viewer", "group", "eng", subject_relation="member")
		payload = rel.as_api_dict()
		self.assertEqual(
			payload["relationship"]["subject"],
			{"object": {"objectType": "group", "objectId": "eng"}, "relation": "member"},
		)
		self.assertEqual(str(rel), "document:D1#viewer@group:eng#member")

	def test_object_spec_transform(self):
		from topaz_frappe.core.domain import TopazObjectSpec

		spec = TopazObjectSpec(object_type="user", id_field="email", id_transform=str.lower)
		obj = spec.get_object({"email": "Alice@Example.COM"})
		self.assertEqual(obj.id, "alice@example.com")

	def test_object_spec_missing_id(self):
		from topaz_frappe.core.domain import TopazObjectSpec

		spec = TopazObjectSpec(object_type="user", id_field="email")
		self.assertIsNone(spec.get_object({"email": None}))
		self.assertIsNone(spec.get_object({"email": ""}))


if __name__ == "__main__":
	unittest.main()
