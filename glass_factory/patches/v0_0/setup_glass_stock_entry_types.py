"""Create glass-specific Repack Stock Entry Types on existing sites."""

from glass_factory.glass_factory.glass_stock_entry_types import ensure_glass_stock_entry_types


def execute():
	ensure_glass_stock_entry_types()
