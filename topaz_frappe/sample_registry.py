"""Example Topaz registry mappings.

These are *not* auto-registered. They illustrate how a consuming app declares
its DocType -> Topaz mapping, either declaratively in ``hooks.py`` or
programmatically here.

Declarative style (preferred) — add to your app's ``hooks.py``::

        topaz_registry = {
            "ToDo": {
                "object": {"type": "todo", "id_field": "name"},
                "relations": [
                    {"kind": "link", "relation": "owner", "target_type": "user", "field": "owner"},
                    # Hide the rest of the world behind a group subject-set:
                    {
                        "kind": "child",
                        "table": "shares",
                        "subject_field": "user",
                        "static_relation": "viewer",
                    },
                ],
            },
        }

Programmatic style — call :func:`register_example_mappings` (e.g. from an
``after_install`` hook)::

        from topaz_frappe.sample_registry import register_example_mappings

        register_example_mappings()
"""

from __future__ import annotations

from topaz_frappe.core.domain import (
	ChildTableRelation,
	DocTypeMapping,
	LinkRelation,
	TopazObjectSpec,
)
from topaz_frappe.core.registry import registry

__all__ = ["build_todo_mapping", "register_example_mappings"]


def build_todo_mapping() -> DocTypeMapping:
	"""A DocTypeMapping for ``ToDo``: owner + shared viewers."""
	return DocTypeMapping(
		object_spec=TopazObjectSpec(object_type="todo", id_field="name"),
		relations=[
			LinkRelation(relation="owner", target_type="user", fieldname="owner"),
			ChildTableRelation(
				table="shares",
				subject_field="user",
				static_relation="viewer",
			),
		],
	)


def register_example_mappings() -> None:
	"""Register the example mappings into the global registry."""
	registry.register("ToDo", build_todo_mapping())
