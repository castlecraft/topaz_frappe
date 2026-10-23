"""Bench CLI command group for topaz_frappe.

``frappe.utils.bench_helper.get_app_commands`` reads the ``commands`` attribute
from ``<app>.commands``. We re-export the cli commands from
``topaz_frappe.commands.sync`` here.
"""

from topaz_frappe.commands.sync import commands

__all__ = ["commands"]
