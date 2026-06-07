"""Install Phase 0 manual MVP custom fields/settings on existing sites."""

from glass_factory.install import create_phase0_foundation


def execute():
	create_phase0_foundation()
