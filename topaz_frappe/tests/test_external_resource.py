"""Integration and unit tests for Topaz External Resource and assign_relation API."""

from __future__ import annotations

import frappe
from frappe.tests import IntegrationTestCase

from topaz_frappe.api import assign_relation, remove_relation
from topaz_frappe.core.manifest import stitch_manifest
from topaz_frappe.transport import factory
from topaz_frappe.transport.mock import MockTransport


class TestTopazExternalResource(IntegrationTestCase):
	def setUp(self):
		super().setUp()
		frappe.set_user("Administrator")
		self.transport = MockTransport()
		factory.set_transport(self.transport)

		# Clean up any leftover test resources
		for res in ["candidate", "grant_proposal"]:
			if frappe.db.exists("Topaz External Resource", res):
				frappe.delete_doc("Topaz External Resource", res, force=1)

	def tearDown(self):
		for res in ["candidate", "grant_proposal"]:
			if frappe.db.exists("Topaz External Resource", res):
				frappe.delete_doc("Topaz External Resource", res, force=1)
		factory.reset_transport()
		super().tearDown()

	def test_manifest_stitching(self):
		frappe.get_doc(
			{
				"doctype": "Topaz External Resource",
				"resource_type": "candidate",
				"service_name": "recruitment-fastapi",
				"owner_team": "HR Tech",
				"manifest_fragment": (
					"candidate:\n"
					"  relations:\n"
					"    interview_committee: committee\n"
					"    lead: user\n"
					"  permissions:\n"
					"    can_interview: interview_committee->is_member | lead\n"
				),
				"enabled": 1,
			}
		).insert()

		stitched = stitch_manifest()
		self.assertIn("candidate:", stitched)
		self.assertIn("interview_committee: committee", stitched)
		self.assertIn("can_interview: interview_committee->is_member | lead", stitched)

	def test_assign_and_remove_relation_api(self):
		# 1. Register candidate resource
		frappe.get_doc(
			{
				"doctype": "Topaz External Resource",
				"resource_type": "candidate",
				"service_name": "recruitment-fastapi",
				"owner_team": "HR Tech",
				"manifest_fragment": (
					"candidate:\n"
					"  relations:\n"
					"    interview_committee: committee\n"
					"    lead: user\n"
					"  permissions:\n"
					"    can_interview: interview_committee->is_member | lead\n"
				),
				"enabled": 1,
			}
		).insert()

		# Ensure a core committee exists in Frappe DB
		if not frappe.db.exists("Eduauth Committee", "Order_Of_The_Phoenix"):
			frappe.get_doc(
				{
					"doctype": "Eduauth Committee",
					"name": "Order_Of_The_Phoenix",
					"committee_name": "Order of the Phoenix Advisory",
				}
			).insert(ignore_permissions=True)

		# 2. Call assign_relation successfully
		res = assign_relation(
			object_type="candidate",
			object_id="CAND-001",
			relation="interview_committee",
			subject_type="committee",
			subject_id="Order_Of_The_Phoenix",
		)
		self.assertTrue(res.get("success"))

		# Check mock transport has the tuple
		found = self.transport.check(
			object_type="candidate",
			object_id="CAND-001",
			relation="interview_committee",
			subject_type="committee",
			subject_id="Order_Of_The_Phoenix",
		)
		self.assertTrue(found)

		# 3. Call remove_relation
		del_res = remove_relation(
			object_type="candidate",
			object_id="CAND-001",
			relation="interview_committee",
			subject_type="committee",
			subject_id="Order_Of_The_Phoenix",
		)
		self.assertTrue(del_res.get("success"))

		# Tuple should no longer exist
		found_after = self.transport.check(
			object_type="candidate",
			object_id="CAND-001",
			relation="interview_committee",
			subject_type="committee",
			subject_id="Order_Of_The_Phoenix",
		)
		self.assertFalse(found_after)

	def test_assign_relation_validations(self):
		# Missing required fields
		with self.assertRaises(frappe.ValidationError):
			assign_relation(
				object_type="",
				object_id="CAND-001",
				relation="interview_committee",
				subject_type="committee",
				subject_id="Order_Of_The_Phoenix",
			)

		# Unregistered resource
		with self.assertRaises(frappe.ValidationError):
			assign_relation(
				object_type="unknown_resource",
				object_id="123",
				relation="lead",
				subject_type="user",
				subject_id="admin@example.com",
			)
